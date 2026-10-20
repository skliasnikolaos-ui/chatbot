"""Operational reports and CSV exports (UTF-8 with BOM for Excel)."""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import rules
from ..models import (ApprovedHours, Assignment, AttendanceRecord, CoverageRequirement, Employee, PayrollEmployeeTotal,
                      PayrollLine, Site)
from ..payroll_engine import CATEGORY_LABELS, money
from ..timeutil import fmt_local
from . import payroll as pay
from . import scheduling as sch


def to_csv(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def schedule_version_for(db: Session, company_id: int, year: int, month: int, prefer: str = "published"):
    period = sch.get_period(db, company_id, year, month, create=False)
    if period is None:
        return None
    order = ["published", "draft"] if prefer == "published" else ["draft", "published"]
    for st in order:
        v = sch.version_with_status(db, period.id, st)
        if v:
            return v
    return None


def coverage(db: Session, company_id: int, year: int, month: int, version=None) -> dict:
    vc = sch.context_for(db, company_id, year, month)
    version = version or schedule_version_for(db, company_id, year, month)
    assigns = sch.assignments_of(db, version.id) if version else []
    covered = {a.slot_key for a in assigns}
    by_req: dict[int, dict] = defaultdict(lambda: {"demand": 0, "covered": 0, "slots": 0, "uncovered_slots": 0})
    uncovered = []
    for s in vc.slots:
        d = by_req[s.requirement_id]
        d["demand"] += s.minutes
        d["slots"] += 1
        if s.key in covered:
            d["covered"] += s.minutes
        else:
            d["uncovered_slots"] += 1
            uncovered.append(s)
    reqs = {r.id: r for r in db.scalars(select(CoverageRequirement).where(CoverageRequirement.company_id == company_id))}
    rows = []
    for rid, d in sorted(by_req.items(), key=lambda kv: (reqs[kv[0]].site.name, reqs[kv[0]].name)):
        r = reqs[rid]
        rows.append({"site": r.site.name, "requirement": r.name, "slots": d["slots"], "uncovered_slots": d["uncovered_slots"],
                     "demand_h": round(d["demand"] / 60, 2), "covered_h": round(d["covered"] / 60, 2),
                     "uncovered_h": round((d["demand"] - d["covered"]) / 60, 2),
                     "pct": round(100 * d["covered"] / d["demand"], 1) if d["demand"] else 100.0})
    validation = sch.validate_version(db, version, vc) if version else None
    total_d = sum(r["demand_h"] for r in rows)
    total_c = sum(r["covered_h"] for r in rows)
    return {"version": version, "rows": rows, "uncovered": uncovered, "context": vc,
            "violations": validation["violations"] if validation else [],
            "total_demand_h": round(total_d, 2), "total_covered_h": round(total_c, 2),
            "total_uncovered_h": round(total_d - total_c, 2),
            "pct": round(100 * total_c / total_d, 1) if total_d else 100.0,
            "feasibility": feasibility(db, company_id, year, month, vc)}


def feasibility(db: Session, company_id: int, year: int, month: int, vc=None) -> list[dict]:
    """Indicative capacity check per requirement (necessary, not sufficient, condition)."""
    vc = vc or sch.context_for(db, company_id, year, month)
    start, end = sch.month_range(year, month)
    active = set(db.scalars(select(Employee.id).where(Employee.company_id == company_id, Employee.is_active.is_(True))))
    weeks = ((end - start).days + 1) / 7
    cap = {}
    for e in vc.employees.values():
        if e.id not in active:
            continue
        absent_days = sum(max(0, (min(a1, end) - max(a0, start)).days + 1) for a0, a1, _ in e.absences)
        c = (e.max_hours_week or 48) * weeks
        if e.max_hours_month:
            c = min(c, e.max_hours_month)
        cap[e.id] = max(0.0, c * (1 - absent_days / ((end - start).days + 1)))
    by_req: dict[int, list] = defaultdict(list)
    for s in vc.slots:
        by_req[s.requirement_id].append(s)
    out = []
    for rid, slots in by_req.items():
        demand_h = sum(s.minutes for s in slots) / 60
        qualified = [e for e in vc.employees.values() if e.id in active and any(
            not ({v.code for v in rules.static_blockers(e, s, vc.sites.get(s.site_id))} & rules.QUALIFICATION_CODES) for s in slots[:50])]
        cap_h = sum(cap[e.id] for e in qualified)
        req = db.get(CoverageRequirement, rid)
        out.append({"site": req.site.name, "requirement": req.name, "demand_h": round(demand_h, 1),
                    "qualified": len(qualified), "capacity_h": round(cap_h, 1),
                    "ok": cap_h >= demand_h and len(qualified) >= req.headcount})
    total_demand = sum(s.minutes for s in vc.slots) / 60
    out.append({"site": "ΣΥΝΟΛΟ", "requirement": "", "demand_h": round(total_demand, 1), "qualified": len(cap),
                "capacity_h": round(sum(cap.values()), 1), "ok": sum(cap.values()) >= total_demand})
    return out


def hours(db: Session, company_id: int, year: int, month: int) -> list[dict]:
    start, end = sch.month_range(year, month)
    planned = defaultdict(int)
    for a in sch.published_assignments_between(db, company_id, start, end):
        planned[a.employee_id] += int((a.end_utc - a.start_utc).total_seconds() // 60)
    actual = defaultdict(int)
    for r in db.scalars(select(AttendanceRecord).where(AttendanceRecord.company_id == company_id, AttendanceRecord.status == "active",
                                                       AttendanceRecord.work_date >= start, AttendanceRecord.work_date <= end)):
        actual[r.employee_id] += int((r.clock_out - r.clock_in).total_seconds() // 60)
    approved = defaultdict(int)
    for r in db.scalars(select(ApprovedHours).where(ApprovedHours.company_id == company_id, ApprovedHours.status == "active",
                                                    ApprovedHours.work_date >= start, ApprovedHours.work_date <= end)):
        approved[r.employee_id] += int((r.end_utc - r.start_utc).total_seconds() // 60)
    out = []
    for e in db.scalars(select(Employee).where(Employee.company_id == company_id).order_by(Employee.last_name)):
        p, a, ap = planned[e.id], actual[e.id], approved[e.id]
        if p or a or ap or e.is_active:
            target = float(e.weekly_contract_hours) * ((end - start).days + 1) / 7
            out.append({"employee": e, "planned_h": round(p / 60, 2), "actual_h": round(a / 60, 2), "approved_h": round(ap / 60, 2),
                        "contract_h": round(target, 1), "diff_h": round((ap - p) / 60, 2)})
    return out


def costs(db: Session, company_id: int, year: int, month: int) -> dict:
    """Planned (published schedule) vs actual (approved hours) employer cost, per employee and per site, with margin."""
    planned = {r.employee_id: r for r in pay.compute(db, company_id, year, month, "planned")}
    period = pay.get_period(db, company_id, year, month)
    run = pay.current_run(db, period)
    if run is not None:
        actual_src = "run"
        actual_totals = {t.employee_id: t for t in db.scalars(select(PayrollEmployeeTotal).where(PayrollEmployeeTotal.run_id == run.id))}
        actual_cost = {k: v.employer_cost for k, v in actual_totals.items()}
        actual_sites = defaultdict(Decimal)
        for t in actual_totals.values():
            for sid, v in (t.site_costs or {}).items():
                actual_sites[None if sid == "None" else int(sid)] += Decimal(v)
    else:
        actual_src = "estimate"
        comp = pay.compute(db, company_id, year, month, "approved")
        actual_cost = {r.employee_id: r.employer_cost for r in comp}
        actual_sites = defaultdict(Decimal)
        for r in comp:
            for sid, v in r.site_costs().items():
                actual_sites[sid] += v
    planned_sites = defaultdict(Decimal)
    for r in planned.values():
        for sid, v in r.site_costs().items():
            planned_sites[sid] += v
    names = {e.id: e.full_name for e in db.scalars(select(Employee).where(Employee.company_id == company_id))}
    emp_rows = []
    for eid in sorted(set(planned) | set(actual_cost), key=lambda i: names.get(i, "")):
        pc = planned[eid].employer_cost if eid in planned else Decimal("0.00")
        ac = actual_cost.get(eid, Decimal("0.00"))
        emp_rows.append({"employee": names.get(eid, eid), "planned": pc, "actual": ac, "diff": ac - pc})
    # revenue: billing rate × covered planned hours / approved hours at site
    start, end = sch.month_range(year, month)
    rev_plan, rev_act, shifts = defaultdict(Decimal), defaultdict(Decimal), defaultdict(int)
    for a in sch.published_assignments_between(db, company_id, start, end):
        rate = a.requirement.billing_rate_hourly
        shifts[a.site_id] += 1
        if rate:
            rev_plan[a.site_id] += Decimal(int((a.end_utc - a.start_utc).total_seconds() // 60)) * rate / 60
    site_rate: dict[int, Decimal] = {}
    for r in db.scalars(select(CoverageRequirement).where(CoverageRequirement.company_id == company_id)):
        if r.billing_rate_hourly:
            site_rate.setdefault(r.site_id, r.billing_rate_hourly)
    for ah in db.scalars(select(ApprovedHours).where(ApprovedHours.company_id == company_id, ApprovedHours.status == "active",
                                                     ApprovedHours.work_date >= start, ApprovedHours.work_date <= end)):
        rate = None
        if ah.assignment_id:
            asg = db.get(Assignment, ah.assignment_id)
            rate = asg.requirement.billing_rate_hourly if asg else None
        rate = rate or site_rate.get(ah.site_id)
        if rate:
            rev_act[ah.site_id] += Decimal(int((ah.end_utc - ah.start_utc).total_seconds() // 60)) * rate / 60
    site_names = {s.id: s.name for s in db.scalars(select(Site).where(Site.company_id == company_id))}
    site_rows = []
    for sid in sorted(set(planned_sites) | set(actual_sites) | set(rev_plan), key=lambda s: site_names.get(s, "~")):
        pc, ac = money(planned_sites.get(sid, Decimal(0))), money(actual_sites.get(sid, Decimal(0)))
        rp, ra = money(rev_plan.get(sid, Decimal(0))), money(rev_act.get(sid, Decimal(0)))
        site_rows.append({"site": site_names.get(sid, "Μη κατανεμημένο"), "planned": pc, "actual": ac,
                          "revenue_planned": rp, "revenue_actual": ra, "margin_planned": rp - pc, "margin_actual": ra - ac,
                          "shifts": shifts.get(sid, 0),
                          "cost_per_shift": money(pc / shifts[sid]) if shifts.get(sid) else None})
    return {"employees": emp_rows, "sites": site_rows, "actual_source": actual_src, "run": run,
            "total_planned": sum((r["planned"] for r in emp_rows), Decimal("0.00")),
            "total_actual": sum((r["actual"] for r in emp_rows), Decimal("0.00"))}


def export_schedule(db: Session, company_id: int, version) -> bytes:
    rows = []
    for a in sch.assignments_of(db, version.id):
        rows.append([a.service_date.isoformat(), a.site.name, a.requirement.name, fmt_local(a.start_utc, "%Y-%m-%d %H:%M"),
                     fmt_local(a.end_utc, "%Y-%m-%d %H:%M"), round((a.end_utc - a.start_utc).total_seconds() / 3600, 2),
                     a.employee.code, a.employee.full_name, "ναι" if a.locked else "", a.source])
    return to_csv(["Ημερομηνία", "Σημείο", "Θέση", "Έναρξη", "Λήξη", "Ώρες", "Κωδ. εργαζομένου", "Εργαζόμενος", "Κλειδωμένη", "Προέλευση"], rows)


def export_gaps(db: Session, company_id: int, year: int, month: int) -> bytes:
    cov = coverage(db, company_id, year, month)
    vc = cov["context"]
    reqs = {r.id: r for r in db.scalars(select(CoverageRequirement).where(CoverageRequirement.company_id == company_id))}
    rows = [[s.service_date.isoformat(), vc.sites[s.site_id].name, reqs[s.requirement_id].name, s.position,
             fmt_local(s.start, "%Y-%m-%d %H:%M"), fmt_local(s.end, "%Y-%m-%d %H:%M"), round(s.minutes / 60, 2)] for s in cov["uncovered"]]
    return to_csv(["Ημερομηνία", "Σημείο", "Θέση", "Α/Α θέσης", "Έναρξη", "Λήξη", "Ακάλυπτες ώρες"], rows)


def export_payroll(db: Session, company_id: int, run) -> bytes:
    """Accountant review export: one line per earnings/charge category. ESTIMATE – not an official payroll filing."""
    rows = []
    sites = {s.id: s.name for s in db.scalars(select(Site).where(Site.company_id == company_id))}
    for ln in db.scalars(select(PayrollLine).where(PayrollLine.run_id == run.id, PayrollLine.company_id == company_id)
                         .order_by(PayrollLine.employee_id, PayrollLine.id)):
        rows.append([ln.employee.code, ln.employee.full_name, CATEGORY_LABELS.get(ln.category, ln.category), ln.category,
                     sites.get(ln.site_id, ""), f"{ln.minutes / 60:.2f}", str(ln.rate), str(ln.pct), str(ln.amount)])
    rows.append([])
    rows.append(["ΕΚΤΙΜΗΣΗ – απαιτείται έλεγχος λογιστή. Δεν αποτελεί επίσημη μισθοδοσία/υποβολή."])
    return to_csv(["Κωδ.", "Εργαζόμενος", "Κατηγορία", "Κωδ. κατηγορίας", "Σημείο", "Ώρες", "Τιμή/ώρα ή ποσό βάσης", "%", "Ποσό €"], rows)


def export_hours(db: Session, company_id: int, year: int, month: int) -> bytes:
    rows = [[r["employee"].code, r["employee"].full_name, r["contract_h"], r["planned_h"], r["actual_h"], r["approved_h"], r["diff_h"]]
            for r in hours(db, company_id, year, month)]
    return to_csv(["Κωδ.", "Εργαζόμενος", "Συμβατικές ώρες (αναλογία)", "Προγραμματισμένες", "Πραγματικές", "Εγκεκριμένες",
                   "Εγκεκριμένες − Προγρ."], rows)


def export_costs(db: Session, company_id: int, year: int, month: int) -> bytes:
    c = costs(db, company_id, year, month)
    rows = [[r["site"], r["shifts"], r["planned"], r["actual"], r["revenue_planned"], r["revenue_actual"], r["margin_planned"],
             r["margin_actual"], r["cost_per_shift"]] for r in c["sites"]]
    rows.append([])
    rows.append(["ΕΚΤΙΜΗΣΗ κόστους – απαιτείται έλεγχος λογιστή."])
    return to_csv(["Σημείο", "Βάρδιες", "Κόστος προγρ.", "Κόστος πραγμ.", "Έσοδα προγρ.", "Έσοδα πραγμ.", "Περιθώριο προγρ.",
                   "Περιθώριο πραγμ.", "Κόστος/βάρδια"], rows)
