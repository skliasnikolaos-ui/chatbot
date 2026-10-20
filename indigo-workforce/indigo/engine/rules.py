"""Hard-constraint rules, implemented independently of the solver.

The solver encodes the same constraints as a CP-SAT model; this module is the
authoritative validator used for manual edits, the publish gate, replacement
candidates, blocker explanations and the invariant tests that check solver
output.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..timeutil import ATHENS, iso_week_key, local_dates_touched, local_to_utc
from .types import EmployeeSpec, FixedWork, SiteSpec, Slot

# Greek labels for blocker / violation codes
LABELS = {
    "NOT_EMPLOYED": "Εκτός περιόδου απασχόλησης",
    "ABSENCE": "Άδεια/απουσία",
    "WEEKLY_UNAVAILABLE": "Μη διαθέσιμος (εβδομαδιαία διαθεσιμότητα)",
    "QUAL_MISSING": "Δεν έχει το απαιτούμενο προσόν",
    "QUAL_EXPIRED": "Έληξε το απαιτούμενο προσόν/άδεια",
    "QUAL_NOT_YET_VALID": "Το προσόν δεν ισχύει ακόμη",
    "SITE_PROHIBITED": "Απαγορεύεται σε αυτό το σημείο",
    "SITE_NOT_ELIGIBLE": "Δεν είναι εγκεκριμένος για αυτό το σημείο",
    "SHIFT_TOO_LONG": "Υπέρβαση μέγιστης διάρκειας βάρδιας",
    "OVERLAP": "Επικάλυψη με άλλη βάρδια",
    "REST": "Ανεπαρκής ανάπαυση μεταξύ βαρδιών",
    "WEEK_LIMIT": "Υπέρβαση εβδομαδιαίου ορίου ωρών",
    "MONTH_LIMIT": "Υπέρβαση μηνιαίου ορίου ωρών",
    "CONSECUTIVE": "Υπέρβαση συνεχόμενων ημερών εργασίας",
    "DOUBLE_BOOKED": "Η θέση έχει ανατεθεί σε περισσότερους από έναν",
    "UNKNOWN_SLOT": "Η θέση δεν αντιστοιχεί σε τρέχουσα απαίτηση κάλυψης",
    "UNKNOWN_EMPLOYEE": "Άγνωστος/ανενεργός εργαζόμενος",
}

STATIC_CODES = {
    "NOT_EMPLOYED", "ABSENCE", "WEEKLY_UNAVAILABLE", "QUAL_MISSING", "QUAL_EXPIRED",
    "QUAL_NOT_YET_VALID", "SITE_PROHIBITED", "SITE_NOT_ELIGIBLE", "SHIFT_TOO_LONG",
}
QUALIFICATION_CODES = {"QUAL_MISSING", "QUAL_EXPIRED", "QUAL_NOT_YET_VALID", "SITE_PROHIBITED", "SITE_NOT_ELIGIBLE", "SHIFT_TOO_LONG"}
AVAILABILITY_CODES = {"NOT_EMPLOYED", "ABSENCE", "WEEKLY_UNAVAILABLE"}


@dataclass
class Violation:
    code: str
    employee_id: int | None
    slot_keys: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def label(self) -> str:
        return LABELS.get(self.code, self.code)

    def as_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "employee_id": self.employee_id,
                "slot_keys": self.slot_keys, "detail": self.detail}


def _weekly_windows(emp: EmployeeSpec, around: list[date]):
    for d in around:
        for wd, t0, t1 in emp.weekly_unavailability:
            if d.weekday() != wd:
                continue
            end_day = d if t1 > t0 else d + timedelta(days=1)
            yield local_to_utc(d, t0), local_to_utc(end_day, t1)


def static_blockers(emp: EmployeeSpec, slot: Slot, site: SiteSpec | None) -> list[Violation]:
    """Constraints that depend only on the employee and the slot itself."""
    out: list[Violation] = []
    v = lambda code, detail="": out.append(Violation(code, emp.id, [slot.key], detail))  # noqa: E731
    touched = local_dates_touched(slot.start, slot.end, ATHENS)
    if emp.hire_date and slot.service_date < emp.hire_date:
        v("NOT_EMPLOYED", f"πρόσληψη {emp.hire_date:%d/%m/%Y}")
    if emp.termination_date and touched[-1] > emp.termination_date:
        v("NOT_EMPLOYED", f"λήξη {emp.termination_date:%d/%m/%Y}")
    for a0, a1, kind in emp.absences:
        if any(a0 <= d <= a1 for d in touched):
            v("ABSENCE", f"{kind} {a0:%d/%m}–{a1:%d/%m}")
            break
    around = [touched[0] - timedelta(days=1)] + touched
    for w0, w1 in _weekly_windows(emp, around):
        if w0 < slot.end and slot.start < w1:
            v("WEEKLY_UNAVAILABLE")
            break
    for q in sorted(slot.qualifications):
        if q not in emp.qualifications:
            v("QUAL_MISSING", str(q))
            continue
        vf, vu = emp.qualifications[q]
        if vf and touched[0] < vf:
            v("QUAL_NOT_YET_VALID", f"{q}: από {vf:%d/%m/%Y}")
        if vu and touched[-1] > vu:
            v("QUAL_EXPIRED", f"{q}: έως {vu:%d/%m/%Y}")
    rule = emp.site_rules.get(slot.site_id)
    if rule == "prohibited":
        v("SITE_PROHIBITED")
    elif site is not None and site.requires_explicit_eligibility and rule not in ("eligible", "preferred"):
        v("SITE_NOT_ELIGIBLE")
    if emp.max_shift_hours and slot.minutes > emp.max_shift_hours * 60:
        v("SHIFT_TOO_LONG", f"{slot.minutes / 60:.1f}ω > {emp.max_shift_hours}ω")
    return out


@dataclass(frozen=True)
class _Item:
    key: str  # slot key, or "fixed:<label>"
    start: datetime
    end: datetime
    service_date: date
    fixed: bool


def _items_for(emp_id: int, assignments: list[tuple[int, Slot]], fixed: list[FixedWork]) -> list[_Item]:
    items = [_Item(s.key, s.start, s.end, s.service_date, False) for e, s in assignments if e == emp_id]
    items += [_Item(f"fixed:{f.label or f.start.isoformat()}", f.start, f.end, f.service_date, True)
              for f in fixed if f.employee_id == emp_id]
    items.sort(key=lambda i: (i.start, i.end))
    return items


def _timeline_violations(emp: EmployeeSpec, items: list[_Item]) -> list[Violation]:
    out: list[Violation] = []
    rest = timedelta(hours=emp.min_rest_hours)
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if b.start >= a.end + rest:
                break
            if a.fixed and b.fixed:
                continue
            if b.start < a.end:
                out.append(Violation("OVERLAP", emp.id, [a.key, b.key]))
            else:
                gap_h = (b.start - a.end).total_seconds() / 3600
                out.append(Violation("REST", emp.id, [a.key, b.key], f"{gap_h:.1f}ω < {emp.min_rest_hours}ω"))
    if emp.max_hours_week:
        weeks: dict = defaultdict(list)
        for it in items:
            weeks[iso_week_key(it.service_date)].append(it)
        for wk, its in weeks.items():
            mins = sum((it.end - it.start).total_seconds() / 60 for it in its)
            if mins > emp.max_hours_week * 60 + 1e-6 and not all(it.fixed for it in its):
                out.append(Violation("WEEK_LIMIT", emp.id, [it.key for it in its if not it.fixed],
                                     f"εβδ. {wk[1]}/{wk[0]}: {mins / 60:.1f}ω > {emp.max_hours_week}ω"))
    if emp.max_hours_month:
        months: dict = defaultdict(list)
        for it in items:
            months[(it.service_date.year, it.service_date.month)].append(it)
        for mk, its in months.items():
            mins = sum((it.end - it.start).total_seconds() / 60 for it in its)
            if mins > emp.max_hours_month * 60 + 1e-6 and not all(it.fixed for it in its):
                out.append(Violation("MONTH_LIMIT", emp.id, [it.key for it in its if not it.fixed],
                                     f"{mk[1]:02d}/{mk[0]}: {mins / 60:.1f}ω > {emp.max_hours_month}ω"))
    if emp.max_consecutive_days:
        days = sorted({it.service_date for it in items})
        run_start, prev = None, None
        for d in days + [None]:
            if d is not None and prev is not None and d == prev + timedelta(days=1):
                prev = d
                continue
            if prev is not None and run_start is not None:
                length = (prev - run_start).days + 1
                if length > emp.max_consecutive_days:
                    keys = [it.key for it in items if run_start <= it.service_date <= prev and not it.fixed]
                    if keys:
                        out.append(Violation("CONSECUTIVE", emp.id, keys,
                                             f"{length} ημέρες ({run_start:%d/%m}–{prev:%d/%m}) > {emp.max_consecutive_days}"))
            run_start = prev = d
    return out


def validate(
    assignments: list[tuple[int, Slot]],
    employees: dict[int, EmployeeSpec],
    sites: dict[int, SiteSpec],
    fixed: list[FixedWork] | None = None,
    known_slot_keys: set[str] | None = None,
) -> list[Violation]:
    """Validate a full set of (employee_id, slot) assignments against all hard constraints."""
    fixed = fixed or []
    out: list[Violation] = []
    by_slot: dict[str, list[int]] = defaultdict(list)
    for e, s in assignments:
        by_slot[s.key].append(e)
        if known_slot_keys is not None and s.key not in known_slot_keys:
            out.append(Violation("UNKNOWN_SLOT", e, [s.key]))
    for key, emps in by_slot.items():
        if len(emps) > 1:
            out.append(Violation("DOUBLE_BOOKED", None, [key], ", ".join(map(str, emps))))
    for e, s in assignments:
        emp = employees.get(e)
        if emp is None:
            out.append(Violation("UNKNOWN_EMPLOYEE", e, [s.key]))
            continue
        out.extend(static_blockers(emp, s, sites.get(s.site_id)))
    for e in sorted({e for e, _ in assignments}):
        emp = employees.get(e)
        if emp is not None:
            out.extend(_timeline_violations(emp, _items_for(e, assignments, fixed)))
    return out


def candidate_blockers(
    emp: EmployeeSpec,
    slot: Slot,
    site: SiteSpec | None,
    current: list[tuple[int, Slot]],
    fixed: list[FixedWork] | None = None,
) -> list[Violation]:
    """Why `emp` cannot take `slot` given the other current assignments (empty = allowed)."""
    out = static_blockers(emp, slot, site)
    others = [(e, s) for e, s in current if s.key != slot.key]
    before = {(v.code, tuple(v.slot_keys)) for v in _timeline_violations(emp, _items_for(emp.id, others, fixed or []))}
    after = _timeline_violations(emp, _items_for(emp.id, others + [(emp.id, slot)], fixed or []))
    for v in after:
        if slot.key in v.slot_keys and (v.code, tuple(k for k in v.slot_keys if k != slot.key)) not in before:
            out.append(v)
    return out
