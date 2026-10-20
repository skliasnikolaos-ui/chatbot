"""CP-SAT scheduling engine.

Two lexicographic phases:
  1. Maximise covered minutes subject to all hard constraints
     (uncovered slots are allowed, so a best partial schedule always exists).
  2. Keep coverage at the phase-1 value and minimise the weighted soft
     objectives (cost, fairness, preferences, continuity, balance, stability).

Hard constraints are never relaxed. If locked assignments themselves break a
hard rule, generation stops and reports the conflicts instead of dropping them.
"""
from __future__ import annotations

import time as _time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from ortools.sat.python import cp_model

from ..timeutil import iso_week_key
from . import rules
from .types import EmployeeSpec, FixedWork, Problem, Slot

STATUS = {
    cp_model.OPTIMAL: "OPTIMAL",
    cp_model.FEASIBLE: "FEASIBLE",
    cp_model.INFEASIBLE: "INFEASIBLE",
    cp_model.MODEL_INVALID: "MODEL_INVALID",
    cp_model.UNKNOWN: "UNKNOWN",
}

CAUSE_LABELS = {
    "NO_QUALIFIED": "Κανένας εργαζόμενος με τα απαιτούμενα προσόντα/έγκριση σημείου",
    "UNAVAILABLE": "Οι κατάλληλοι εργαζόμενοι απουσιάζουν ή δεν είναι διαθέσιμοι",
    "CAPACITY": "Οι κατάλληλοι εργαζόμενοι έχουν εξαντλήσει όρια ωρών/ανάπαυσης ή εργάζονται αλλού",
    "NOT_ASSIGNED": "Υπάρχει διαθέσιμος εργαζόμενος αλλά ο επιλυτής δεν τον ανέθεσε (όριο χρόνου)",
}
CAUSE_HINTS = {
    "NO_QUALIFIED": "Απαιτείται επιπλέον προσωπικό με τα προσόντα ή αλλαγή απαιτήσεων/εγκρίσεων σημείου.",
    "UNAVAILABLE": "Θα βοηθούσε διαφορετική διαθεσιμότητα/άδειες ή επιπλέον προσωπικό.",
    "CAPACITY": "Απαιτείται επιπλέον προσωπικό ή αλλαγή απαιτήσεων/ορίων (π.χ. διάρκεια βαρδιών).",
    "NOT_ASSIGNED": "Επαναλάβετε με μεγαλύτερο χρόνο επίλυσης.",
}


@dataclass
class Result:
    status: str  # complete | partial | locked_conflict | infeasible | error
    coverage_status: str = ""
    optimisation_status: str = ""
    assignments: list[tuple[int, Slot]] = field(default_factory=list)
    uncovered: list[Slot] = field(default_factory=list)
    explanations: dict[str, dict] = field(default_factory=dict)
    locked_violations: list[rules.Violation] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    @property
    def proven_max_coverage(self) -> bool:
        return self.coverage_status == "OPTIMAL"


def _eligible_pairs(p: Problem) -> tuple[dict[tuple[int, str], bool], dict[tuple[int, str], list]]:
    """Static eligibility plus conflicts with fixed out-of-scope work."""
    elig: dict[tuple[int, str], bool] = {}
    reasons: dict[tuple[int, str], list] = {}
    fixed_by_emp: dict[int, list[FixedWork]] = defaultdict(list)
    for f in p.fixed_work:
        fixed_by_emp[f.employee_id].append(f)
    for emp in p.employees:
        rest = timedelta(hours=emp.min_rest_hours)
        for s in p.slots:
            bl = rules.static_blockers(emp, s, p.sites.get(s.site_id))
            for f in fixed_by_emp[emp.id]:
                if f.start < s.end + rest and s.start < f.end + rest:
                    code = "OVERLAP" if (f.start < s.end and s.start < f.end) else "REST"
                    bl.append(rules.Violation(code, emp.id, [s.key, f"fixed:{f.label}"]))
                    break
            elig[(emp.id, s.key)] = not bl
            if bl:
                reasons[(emp.id, s.key)] = bl
    return elig, reasons


def _target_minutes(emp: EmployeeSpec, start: date, end: date) -> int:
    days = 0
    d = start
    while d <= end:
        employed = (not emp.hire_date or d >= emp.hire_date) and (not emp.termination_date or d <= emp.termination_date)
        absent = any(a0 <= d <= a1 for a0, a1, _ in emp.absences)
        days += 1 if employed and not absent else 0
        d += timedelta(days=1)
    return int(emp.contract_weekly_hours * 60 * days / 7)


def solve(p: Problem) -> Result:
    t0 = _time.monotonic()
    emps = {e.id: e for e in p.employees}
    slots = {s.key: s for s in p.slots}

    # --- locked assignments must themselves satisfy every hard rule
    locked_pairs = [(e, slots[k]) for k, e in p.locked.items() if k in slots]
    lv = rules.validate(locked_pairs, emps, p.sites, p.fixed_work)
    unknown_locked = [k for k in p.locked if k not in slots]
    if lv or unknown_locked:
        res = Result(status="locked_conflict", locked_violations=lv)
        for k in unknown_locked:
            res.locked_violations.append(rules.Violation("UNKNOWN_SLOT", p.locked[k], [k]))
        res.messages.append("Κλειδωμένες αναθέσεις παραβιάζουν σκληρούς κανόνες. Ξεκλειδώστε ή διορθώστε τις πριν τη δημιουργία.")
        return res

    elig, _ = _eligible_pairs(p)
    m = cp_model.CpModel()
    x: dict[tuple[int, str], cp_model.IntVar] = {}
    for (e, k), ok in elig.items():
        if ok:
            x[(e, k)] = m.NewBoolVar(f"x_{e}_{k}")
    u = {k: m.NewBoolVar(f"u_{k}") for k in slots}

    by_slot: dict[str, list] = defaultdict(list)
    by_emp: dict[int, list[str]] = defaultdict(list)
    for (e, k), var in x.items():
        by_slot[k].append(var)
        by_emp[e].append(k)
    for k in slots:
        m.Add(sum(by_slot[k]) + u[k] == 1)
    for k, e in p.locked.items():
        m.Add(x[(e, k)] == 1)

    fixed_by_emp: dict[int, list[FixedWork]] = defaultdict(list)
    for f in p.fixed_work:
        fixed_by_emp[f.employee_id].append(f)

    for e, keys in by_emp.items():
        emp = emps[e]
        rest = timedelta(hours=emp.min_rest_hours)
        ss = sorted((slots[k] for k in keys), key=lambda s: s.start)
        # overlap + minimum rest: maximal cliques of the extended-interval graph
        seen: set[frozenset] = set()
        for i, a in enumerate(ss):
            clique = [b.key for b in ss if b.start <= a.start < b.end + rest]
            fs = frozenset(clique)
            if len(clique) > 1 and fs not in seen:
                seen.add(fs)
                m.AddAtMostOne(x[(e, k)] for k in clique)
        # weekly / monthly hour limits (including fixed work in the same week/month)
        if emp.max_hours_week:
            wk: dict = defaultdict(list)
            for s in ss:
                wk[iso_week_key(s.service_date)].append(s)
            for w, items in wk.items():
                fixed_min = sum(int((f.end - f.start).total_seconds() // 60) for f in fixed_by_emp[e]
                                if iso_week_key(f.service_date) == w)
                m.Add(sum(x[(e, s.key)] * s.minutes for s in items) + fixed_min <= int(emp.max_hours_week * 60))
        if emp.max_hours_month:
            mo: dict = defaultdict(list)
            for s in ss:
                mo[(s.service_date.year, s.service_date.month)].append(s)
            for mk, items in mo.items():
                fixed_min = sum(int((f.end - f.start).total_seconds() // 60) for f in fixed_by_emp[e]
                                if (f.service_date.year, f.service_date.month) == mk)
                m.Add(sum(x[(e, s.key)] * s.minutes for s in items) + fixed_min <= int(emp.max_hours_month * 60))
        # maximum consecutive working days
        if emp.max_consecutive_days:
            n = emp.max_consecutive_days
            day_vars: dict[date, cp_model.IntVar] = {}
            for s in ss:
                if s.service_date not in day_vars:
                    day_vars[s.service_date] = m.NewBoolVar(f"w_{e}_{s.service_date}")
                m.Add(day_vars[s.service_date] >= x[(e, s.key)])
            fixed_days = {f.service_date for f in fixed_by_emp[e]}
            if day_vars:
                lo = min(day_vars) - timedelta(days=n)
                hi = max(day_vars)
                d = lo
                while d <= hi:
                    window = [d + timedelta(days=i) for i in range(n + 1)]
                    terms = [day_vars[w] for w in window if w in day_vars]
                    const = sum(1 for w in window if w in fixed_days and w not in day_vars)
                    if terms and len(terms) + const > n:
                        m.Add(sum(terms) + const <= n)
                    d += timedelta(days=1)

    uncovered_minutes = sum(u[k] * s.minutes for k, s in slots.items())

    def _solver(limit: float) -> cp_model.CpSolver:
        sv = cp_model.CpSolver()
        sv.parameters.max_time_in_seconds = max(1.0, limit)
        sv.parameters.num_search_workers = max(1, p.workers)
        sv.parameters.random_seed = p.seed
        return sv

    # ---------------- phase 1: coverage
    m.Minimize(uncovered_minutes)
    sv1 = _solver(p.time_limit_s * 0.4)
    st1 = sv1.Solve(m)
    res = Result(status="error", coverage_status=STATUS.get(st1, str(st1)))
    if st1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        res.status = "infeasible" if st1 == cp_model.INFEASIBLE else "error"
        res.messages.append(
            "Το πρόβλημα αποδείχθηκε ανέφικτο με τις κλειδωμένες αναθέσεις και τα όρια." if st1 == cp_model.INFEASIBLE
            else "Ο επιλυτής δεν βρήκε λύση εντός του χρονικού ορίου (δεν έχει αποδειχθεί ανεφικτότητα).")
        res.stats = {"seconds": round(_time.monotonic() - t0, 2)}
        return res
    best_uncovered = int(sv1.ObjectiveValue())
    m.Add(uncovered_minutes <= best_uncovered)
    for var_key, var in x.items():
        m.AddHint(var, sv1.Value(var))
    for k, var in u.items():
        m.AddHint(var, sv1.Value(var))

    # ---------------- phase 2: soft objectives
    W = p.weights
    terms = []
    c_cost = W.coefficient("cost")
    if c_cost:
        terms += [var * (60 * c_cost * p.cost_cents.get(key, 0) // 100) for key, var in x.items()]
    for name, pred in (("night_fairness", lambda s: s.is_night), ("weekend_fairness", lambda s: s.is_weekend)):
        c = W.coefficient(name)
        if not c:
            continue
        counts = []
        for e, keys in by_emp.items():
            if emps[e].is_relief:
                continue
            ks = [k for k in keys if pred(slots[k])]
            if ks:
                counts.append(sum(x[(e, k)] for k in ks))
        if len(counts) > 1:
            hi = m.NewIntVar(0, len(slots), f"max_{name}")
            lo = m.NewIntVar(0, len(slots), f"min_{name}")
            for cnt in counts:
                m.Add(hi >= cnt)
                m.Add(lo <= cnt)
            terms.append((hi - lo) * 60 * c)
    c_pref = W.coefficient("preferences")
    if c_pref:
        for (e, k), var in x.items():
            emp, s = emps[e], slots[k]
            pen = 0
            if s.is_night and emp.night_preference == "avoid":
                pen += c_pref
            if s.is_night and emp.night_preference == "prefer":
                pen -= c_pref // 3
            if s.is_weekend and emp.avoid_weekends:
                pen += c_pref
            if emp.site_rules.get(s.site_id) == "preferred":
                pen -= c_pref // 3
            if pen:
                terms.append(var * 60 * pen)
    c_cont = W.coefficient("continuity")
    if c_cont:
        for e, keys in by_emp.items():
            by_req: dict[int, list[str]] = defaultdict(list)
            for k in keys:
                by_req[slots[k].requirement_id].append(k)
            for rid, ks in by_req.items():
                y = m.NewBoolVar(f"y_{e}_{rid}")
                for k in ks:
                    m.Add(y >= x[(e, k)])
                terms.append(y * 60 * c_cont)
    c_bal = W.coefficient("balance")
    if c_bal:
        for e, keys in by_emp.items():
            emp = emps[e]
            if emp.is_relief:
                continue
            target = _target_minutes(emp, p.period_start, p.period_end)
            fixed_in = sum(int((f.end - f.start).total_seconds() // 60) for f in fixed_by_emp[e]
                           if p.period_start <= f.service_date <= p.period_end)
            worked = sum(x[(e, k)] * slots[k].minutes for k in keys) + fixed_in
            dev = m.NewIntVar(0, 100_000, f"dev_{e}")
            m.Add(dev >= worked - target)
            m.Add(dev >= target - worked)
            terms.append(dev * c_bal)
    c_stab = W.coefficient("stability")
    if c_stab:
        for k, e in p.previous.items():
            if (e, k) in x:
                terms.append(-x[(e, k)] * 60 * c_stab)
    c_fix = W.coefficient("fixed_posts")
    if c_fix:
        for emp in p.employees:
            for rid, wds in emp.fixed_posts.items():
                shifts: dict[str, list] = defaultdict(list)
                for s in p.slots:
                    if s.requirement_id == rid and s.service_date.weekday() in wds and (emp.id, s.key) in x:
                        shifts[s.shift_key].append(x[(emp.id, s.key)])
                for vs in shifts.values():
                    terms.append(-sum(vs) * 60 * c_fix)
    c_rel = W.coefficient("prefer_regular_staff")
    if c_rel:
        for (e, k), var in x.items():
            if emps[e].is_relief:
                terms.append(var * slots[k].minutes * c_rel)

    st2 = None
    sv = sv1
    if terms:
        m.Minimize(sum(terms))
        sv2 = _solver(p.time_limit_s * 0.6)
        st2 = sv2.Solve(m)
        if st2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            sv = sv2
    res.optimisation_status = STATUS.get(st2, "SKIPPED") if st2 is not None else "SKIPPED"
    if st2 is not None and st2 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        res.messages.append("Η βελτιστοποίηση δευτερευόντων στόχων δεν ολοκληρώθηκε· χρησιμοποιείται η λύση κάλυψης.")

    res.assignments = sorted(((e, slots[k]) for (e, k), var in x.items() if sv.Value(var)),
                             key=lambda a: (a[1].start, a[1].key))
    covered = {s.key for _, s in res.assignments}
    res.uncovered = [s for s in p.slots if s.key not in covered]
    res.status = "complete" if not res.uncovered else "partial"
    res.explanations = explain_uncovered(p, res.assignments, res.uncovered)
    res.stats = summarise(p, res, sv1, sv, t0)
    if res.uncovered and res.proven_max_coverage:
        res.messages.append("Αποδεδειγμένα: πλήρης κάλυψη δεν είναι εφικτή με τους τρέχοντες σκληρούς κανόνες· "
                            "το πρόγραμμα έχει τη μέγιστη δυνατή κάλυψη.")
    elif res.uncovered:
        res.messages.append("Λήξη χρονικού ορίου: η κάλυψη ενδέχεται να βελτιώνεται με περισσότερο χρόνο επίλυσης.")
    if res.optimisation_status == "OPTIMAL":
        res.messages.append("Οι δευτερεύοντες στόχοι βελτιστοποιήθηκαν πλήρως (αποδεδειγμένα βέλτιστο).")
    elif res.optimisation_status == "FEASIBLE":
        res.messages.append("Οι δευτερεύοντες στόχοι δεν έχουν αποδειχθεί βέλτιστοι (όριο χρόνου).")
    return res


def explain_uncovered(p: Problem, assignments: list[tuple[int, Slot]], uncovered: list[Slot]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in uncovered:
        counts: dict[str, int] = defaultdict(int)
        qualified = available = free = 0
        free_names = []
        for emp in p.employees:
            bl = rules.candidate_blockers(emp, s, p.sites.get(s.site_id), assignments, p.fixed_work)
            codes = {v.code for v in bl}
            for c in codes:
                counts[c] += 1
            if codes & rules.QUALIFICATION_CODES:
                continue
            qualified += 1
            if codes & rules.AVAILABILITY_CODES:
                continue
            available += 1
            if not codes:
                free += 1
                free_names.append(emp.name)
        cause = "NO_QUALIFIED" if qualified == 0 else "UNAVAILABLE" if available == 0 else "CAPACITY" if free == 0 else "NOT_ASSIGNED"
        out[s.key] = {
            "cause": cause,
            "cause_label": CAUSE_LABELS[cause],
            "hint": CAUSE_HINTS[cause],
            "qualified": qualified,
            "available": available,
            "free": free,
            "free_names": free_names,
            "blocker_counts": {rules.LABELS.get(c, c): n for c, n in sorted(counts.items(), key=lambda kv: -kv[1])},
        }
    return out


def summarise(p: Problem, res: Result, sv1, sv, t0) -> dict:
    demand_min = sum(s.minutes for s in p.slots)
    unc_min = sum(s.minutes for s in res.uncovered)
    per_emp: dict[int, dict] = {}
    for e, s in res.assignments:
        d = per_emp.setdefault(e, {"minutes": 0, "shifts": 0, "nights": 0, "weekends": 0, "cost_cents": 0})
        d["minutes"] += s.minutes
        d["shifts"] += 1
        d["nights"] += int(s.is_night)
        d["weekends"] += int(s.is_weekend)
        d["cost_cents"] += p.cost_cents.get((e, s.key), 0)
    by_cause: dict[str, int] = defaultdict(int)
    by_site: dict[int, int] = defaultdict(int)
    for s in res.uncovered:
        by_cause[res.explanations[s.key]["cause"]] += s.minutes
        by_site[s.site_id] += s.minutes
    return {
        "seconds": round(_time.monotonic() - t0, 2),
        "slots": len(p.slots),
        "uncovered_slots": len(res.uncovered),
        "demand_hours": round(demand_min / 60, 2),
        "uncovered_hours": round(unc_min / 60, 2),
        "coverage_pct": round(100 * (demand_min - unc_min) / demand_min, 2) if demand_min else 100.0,
        "coverage_bound_uncovered_hours": round(sv1.BestObjectiveBound() / 60, 2),
        "planned_cost_eur": round(sum(d["cost_cents"] for d in per_emp.values()) / 100, 2),
        "per_employee": per_emp,
        "uncovered_hours_by_cause": {k: round(v / 60, 2) for k, v in by_cause.items()},
        "uncovered_hours_by_site": {k: round(v / 60, 2) for k, v in by_site.items()},
        "weights": p.weights.as_dict(),
    }
