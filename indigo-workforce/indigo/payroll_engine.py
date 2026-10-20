"""Payroll / employer-cost ESTIMATE engine (pure, Decimal-safe).

IMPORTANT: the statutory parameters are configurable, effective-dated rule
sets. The shipped defaults are NOT verified against authoritative sources;
every result is labelled as an estimate and must be reviewed by an
accountant (see docs/LIMITATIONS.md). This engine does not compute income
tax withholding or net pay and is not an official payroll filing.

Model
-----
* Worked time (approved hours) is cut into atomic segments at local midnight,
  06:00 and 22:00 (Europe/Athens) so every segment has a single local date,
  a night flag and a Sunday/holiday flag.
* Weekly hour categories are assigned chronologically within each ISO week:
  up to contract hours = regular; contract→full-time hours = part-time extra
  (only for part-time contracts); full-time→overwork limit = overwork
  (υπερεργασία); above the limit = overtime (υπερωρία).
* Premium % = max(statutory, contractual). A contractual value below the
  statutory one is reported as a warning and never applied.
* Every monetary line is rounded to cents with ROUND_HALF_UP; totals are sums
  of rounded lines.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .timeutil import ATHENS, NIGHT_END, NIGHT_START, day_bounds_utc, iso_week_key, local_dates_touched, local_to_utc, minutes_between

CENT = Decimal("0.01")
HUNDRED = Decimal(100)
SIXTY = Decimal(60)

DEFAULT_RULES = {
    "night_premium_pct": "25",
    "sunday_holiday_premium_pct": "75",
    "full_time_weekly_hours": "40",
    "overwork_limit_weekly_hours": "45",
    "part_time_extra_premium_pct": "12",
    "overwork_premium_pct": "20",
    "overtime_premium_pct": "40",
    "monthly_to_hourly_divisor": "166.6667",
    "employer_contribution_pct": "21.79",
    "employee_contribution_pct": "13.37",
    "bonus_accrual_pct": "0",
}

RULE_LABELS = {
    "night_premium_pct": "Προσαύξηση νυχτερινής εργασίας 22:00–06:00 (%)",
    "sunday_holiday_premium_pct": "Προσαύξηση Κυριακής/αργίας (%)",
    "full_time_weekly_hours": "Ώρες πλήρους απασχόλησης / εβδομάδα",
    "overwork_limit_weekly_hours": "Όριο υπερεργασίας / εβδομάδα (ώρες)",
    "part_time_extra_premium_pct": "Προσαύξηση πρόσθετης εργασίας μερικής απασχόλησης (%)",
    "overwork_premium_pct": "Προσαύξηση υπερεργασίας (%)",
    "overtime_premium_pct": "Προσαύξηση υπερωρίας (%)",
    "monthly_to_hourly_divisor": "Διαιρέτης μηνιαίου μισθού → ωρομίσθιο",
    "employer_contribution_pct": "Εργοδοτικές εισφορές (%)",
    "employee_contribution_pct": "Εισφορές εργαζομένου (%)",
    "bonus_accrual_pct": "Πρόβλεψη δώρων/επιδόματος αδείας επί μικτών (%)",
}

CATEGORY_LABELS = {
    "base_salary": "Μηνιαίος μισθός (αναλογία)",
    "regular": "Τακτικές ώρες",
    "part_time_extra": "Πρόσθετη εργασία μερικής απασχόλησης – βασική αμοιβή",
    "part_time_extra_premium": "Πρόσθετη εργασία μερικής απασχόλησης – προσαύξηση",
    "overwork": "Υπερεργασία – βασική αμοιβή",
    "overwork_premium": "Υπερεργασία – προσαύξηση",
    "overtime": "Υπερωρία – βασική αμοιβή",
    "overtime_premium": "Υπερωρία – προσαύξηση",
    "night_premium": "Προσαύξηση νυχτερινής",
    "sunday_holiday_premium": "Προσαύξηση Κυριακής/αργίας",
    "employer_contributions": "Εργοδοτικές εισφορές (εκτίμηση)",
    "employee_contributions": "Εισφορές εργαζομένου (ενημερωτικά)",
    "bonus_accrual": "Πρόβλεψη δώρων/επιδόματος αδείας (εκτίμηση)",
}


def D(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Compensation:
    pay_basis: str  # hourly | monthly
    hourly_rate: Decimal | None = None
    monthly_salary: Decimal | None = None
    weekly_hours: Decimal = Decimal(40)
    night_premium_pct: Decimal | None = None
    sunday_holiday_premium_pct: Decimal | None = None
    overtime_premium_pct: Decimal | None = None
    overwork_premium_pct: Decimal | None = None
    effective_from: date | None = None
    effective_to: date | None = None


@dataclass
class WorkInterval:
    start: datetime
    end: datetime
    site_id: int | None = None
    ref: str = ""


@dataclass
class Line:
    category: str
    site_id: int | None
    minutes: int
    rate: Decimal
    pct: Decimal
    amount: Decimal

    @property
    def hours(self) -> Decimal:
        return money(D(self.minutes) / SIXTY)

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)


@dataclass
class EmployeePayroll:
    employee_id: int
    lines: list[Line] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    minutes: dict[str, int] = field(default_factory=dict)
    gross: Decimal = Decimal("0.00")
    employer_contributions: Decimal = Decimal("0.00")
    employee_contributions: Decimal = Decimal("0.00")
    bonus_accrual: Decimal = Decimal("0.00")
    employer_cost: Decimal = Decimal("0.00")

    def site_costs(self) -> dict[int | None, Decimal]:
        """Employer cost per site: earnings lines by site, unallocated salary and
        employer charges distributed pro rata to site earnings (cents remainder
        assigned to the largest site so the sum equals employer_cost)."""
        earn: dict[int | None, Decimal] = defaultdict(Decimal)
        for ln in self.lines:
            if ln.category in ("employer_contributions", "employee_contributions", "bonus_accrual"):
                continue
            earn[ln.site_id] += ln.amount
        unalloc = earn.pop(None, Decimal(0)) + self.employer_contributions + self.bonus_accrual
        total_site = sum(earn.values(), Decimal(0))
        if not earn or total_site == 0:
            return {None: self.employer_cost} if self.employer_cost else {}
        out = {s: money(v + unalloc * v / total_site) for s, v in earn.items()}
        diff = self.employer_cost - sum(out.values(), Decimal(0))
        biggest = max(out, key=lambda s: out[s])
        out[biggest] += diff
        return out


def _atomic_segments(iv: WorkInterval, holidays: set[date]):
    """Yield (start, end, local_date, is_night, is_sun_or_hol)."""
    for d in local_dates_touched(iv.start, iv.end, ATHENS):
        b0, b1 = day_bounds_utc(d, ATHENS)
        cuts = sorted({b0, local_to_utc(d, NIGHT_END), local_to_utc(d, NIGHT_START), b1})
        for a, b in zip(cuts, cuts[1:]):
            s, e = max(a, iv.start), min(b, iv.end)
            if e <= s:
                continue
            local_t = s.astimezone(ATHENS).time()
            night = local_t >= NIGHT_START or local_t < NIGHT_END
            yield s, e, d, night, (d.weekday() == 6 or d in holidays)


def statutory_pct(rules: dict, comp: Compensation, key: str, warnings: list[str]) -> Decimal:
    stat = D(rules[key])
    contractual = getattr(comp, key, None)
    if contractual is None:
        return stat
    contractual = D(contractual)
    if contractual < stat:
        warnings.append(f"Η σύμβαση ορίζει {contractual}% για «{RULE_LABELS[key]}», κάτω από το ελάχιστο {stat}% — εφαρμόζεται το ελάχιστο.")
        return stat
    return contractual


def base_hourly(comp: Compensation, rules: dict) -> Decimal:
    if comp.pay_basis == "monthly":
        return D(comp.monthly_salary or 0) / D(rules["monthly_to_hourly_divisor"])
    return D(comp.hourly_rate or 0)


def calculate(
    employee_id: int,
    year: int,
    month: int,
    comp: Compensation | None,
    rules: dict,
    intervals: list[WorkInterval],
    holidays: set[date],
    employed_from: date | None = None,
    employed_to: date | None = None,
) -> EmployeePayroll:
    """Payroll estimate for one employee and one calendar month.

    `intervals` should include work in the ISO weeks that straddle the month
    so weekly categories are correct; only time on dates inside the month is paid.
    """
    res = EmployeePayroll(employee_id)
    if comp is None:
        res.warnings.append("Δεν υπάρχουν όροι αμοιβής σε ισχύ για την περίοδο — δεν υπολογίστηκε κόστος.")
        return res
    rules = {**DEFAULT_RULES, **rules}
    first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    rate = base_hourly(comp, rules)
    night_pct = statutory_pct(rules, comp, "night_premium_pct", res.warnings)
    sun_pct = statutory_pct(rules, comp, "sunday_holiday_premium_pct", res.warnings)
    ow_pct = statutory_pct(rules, comp, "overwork_premium_pct", res.warnings)
    ot_pct = statutory_pct(rules, comp, "overtime_premium_pct", res.warnings)
    pt_pct = D(rules["part_time_extra_premium_pct"])
    contract_min = int(D(comp.weekly_hours) * 60)
    ft_min = int(D(rules["full_time_weekly_hours"]) * 60)
    limit_min = int(D(rules["overwork_limit_weekly_hours"]) * 60)
    pt_upper = max(contract_min, ft_min)

    segs = []
    for iv in intervals:
        segs.extend((s, e, d, n, sh, iv.site_id) for s, e, d, n, sh in _atomic_segments(iv, holidays))
    segs.sort(key=lambda t: t[0])

    week_used: dict = defaultdict(int)
    agg: dict[tuple[str, int | None], int] = defaultdict(int)
    for s, e, d, night, sunhol, site in segs:
        mins = minutes_between(s, e)
        wk = iso_week_key(d)
        used = week_used[wk]
        week_used[wk] += mins
        in_month = first <= d <= last
        if not in_month:
            continue
        # split the segment across category thresholds
        pos, remaining = used, mins
        for cat, upper in (("regular", contract_min), ("part_time_extra", pt_upper), ("overwork", limit_min), ("overtime", 10**9)):
            if remaining <= 0:
                break
            room = max(0, upper - pos)
            take = min(room, remaining)
            if take:
                agg[(cat, site)] += take
                pos += take
                remaining -= take
        if night:
            agg[("night", site)] += mins
        if sunhol:
            agg[("sunhol", site)] += mins

    def add(cat, site, minutes, r, pct, amount):
        if minutes or amount:
            res.lines.append(Line(cat, site, minutes, money(r) if r else Decimal("0.00"), pct, money(amount)))

    totals: dict[str, int] = defaultdict(int)
    for (cat, site), mins in sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        totals[cat] += mins
        hours = D(mins) / SIXTY
        if cat == "regular":
            if comp.pay_basis == "hourly":
                add("regular", site, mins, rate, Decimal(0), hours * rate)
            else:
                add("regular", site, mins, Decimal(0), Decimal(0), Decimal(0))  # covered by salary
        elif cat in ("part_time_extra", "overwork", "overtime"):
            pct = {"part_time_extra": pt_pct, "overwork": ow_pct, "overtime": ot_pct}[cat]
            add(cat, site, mins, rate, Decimal(0), hours * rate)
            add(f"{cat}_premium", site, mins, rate, pct, hours * rate * pct / HUNDRED)
        elif cat == "night":
            add("night_premium", site, mins, rate, night_pct, hours * rate * night_pct / HUNDRED)
        elif cat == "sunhol":
            add("sunday_holiday_premium", site, mins, rate, sun_pct, hours * rate * sun_pct / HUNDRED)
    if comp.pay_basis == "monthly":
        days_in_month = (last - first).days + 1
        start = max(first, employed_from or first, comp.effective_from or first)
        end = min(last, employed_to or last, comp.effective_to or last)
        days = max(0, (end - start).days + 1)
        res.lines.insert(0, Line("base_salary", None, 0, money(D(comp.monthly_salary or 0)),
                                 Decimal(0), money(D(comp.monthly_salary or 0) * D(days) / D(days_in_month))))
        if days != days_in_month:
            res.warnings.append(f"Αναλογία μισθού {days}/{days_in_month} ημέρες.")
    res.minutes = {
        "total": totals["regular"] + totals["part_time_extra"] + totals["overwork"] + totals["overtime"],
        **dict(totals),
    }
    res.gross = sum((ln.amount for ln in res.lines), Decimal("0.00"))
    res.employer_contributions = money(res.gross * D(rules["employer_contribution_pct"]) / HUNDRED)
    res.employee_contributions = money(res.gross * D(rules["employee_contribution_pct"]) / HUNDRED)
    res.bonus_accrual = money(res.gross * D(rules["bonus_accrual_pct"]) / HUNDRED)
    res.employer_cost = res.gross + res.employer_contributions + res.bonus_accrual
    res.lines.append(Line("employer_contributions", None, 0, Decimal("0.00"), D(rules["employer_contribution_pct"]), res.employer_contributions))
    res.lines.append(Line("employee_contributions", None, 0, Decimal("0.00"), D(rules["employee_contribution_pct"]), res.employee_contributions))
    if res.bonus_accrual:
        res.lines.append(Line("bonus_accrual", None, 0, Decimal("0.00"), D(rules["bonus_accrual_pct"]), res.bonus_accrual))
    return res


def shift_cost_cents(comp: Compensation | None, rules: dict, minutes: int, night_minutes: int, sunhol_minutes: int) -> int:
    """Planning estimate of employer cost for one shift (no weekly overtime effects)."""
    if comp is None:
        return 0
    rules = {**DEFAULT_RULES, **rules}
    w: list[str] = []
    rate = base_hourly(comp, rules)
    gross = (D(minutes) * rate
             + D(night_minutes) * rate * statutory_pct(rules, comp, "night_premium_pct", w) / HUNDRED
             + D(sunhol_minutes) * rate * statutory_pct(rules, comp, "sunday_holiday_premium_pct", w) / HUNDRED) / SIXTY
    factor = 1 + (D(rules["employer_contribution_pct"]) + D(rules["bonus_accrual_pct"])) / HUNDRED
    return int(money(gross * factor) * 100)


def week_window(year: int, month: int) -> tuple[date, date]:
    """Dates of the full ISO weeks that touch the month (for weekly categories)."""
    first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    return first - timedelta(days=first.weekday()), last + timedelta(days=6 - last.weekday())
