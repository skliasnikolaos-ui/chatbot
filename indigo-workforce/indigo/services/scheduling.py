"""Scheduling service: DB ↔ engine adapter, versioning, publication, manual edits.

Concurrency: every mutation of a schedule version or period goes through
`_bump`, an atomic compare-and-set on the `revision` column. A stale form
(another user changed the schedule meanwhile) fails with ConflictError.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import payroll_engine as pe
from ..config import settings
from ..db import utcnow
from ..engine import demand, rules, solver
from ..engine.types import EmployeeSpec, FixedWork, Problem, RequirementSpec, SiteSpec, Slot, Weights
from ..models import (Absence, Assignment, CompensationTerm, Contract, CoverageRequirement, Employee,
                      EmployeeQualification, EmployeeSiteRule, FixedPost, GenerationRun, Holiday,
                      PayrollRuleSet, SchedulePeriod, ScheduleVersion, Site, WeeklyUnavailability)
from ..timeutil import fmt_local
from . import audit
from .scope import get_owned


class ConflictError(Exception):
    """Optimistic-lock failure: the record changed since the user loaded it."""


class RuleViolation(Exception):
    def __init__(self, violations: list[rules.Violation]):
        super().__init__("; ".join(v.label for v in violations))
        self.violations = violations


def month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


# ------------------------------------------------------------------ loading
def holidays_set(db: Session, company_id: int, start: date, end: date) -> set[date]:
    return set(db.scalars(select(Holiday.date).where(Holiday.company_id == company_id,
                                                      Holiday.date >= start, Holiday.date <= end)))


def _f(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def requirement_specs(db: Session, company_id: int) -> list[RequirementSpec]:
    out = []
    rows = db.execute(select(CoverageRequirement, Site, Contract)
                      .join(Site, CoverageRequirement.site_id == Site.id)
                      .outerjoin(Contract, Site.contract_id == Contract.id)
                      .where(CoverageRequirement.company_id == company_id, CoverageRequirement.is_active.is_(True),
                             Site.is_active.is_(True))).all()
    for r, site, contract in rows:
        vf, vt = r.valid_from, r.valid_to
        if contract is not None:  # demand only exists within the active contract period
            vf = max(filter(None, [vf, contract.start_date]))
            vt = min(filter(None, [vt, contract.end_date])) if (vt or contract.end_date) else None
        out.append(RequirementSpec(
            id=r.id, site_id=r.site_id, name=r.name, weekdays=frozenset(r.weekday_set), start_time=r.start_time,
            end_time=r.end_time, headcount=r.headcount, shift_hours=_f(r.shift_hours), holiday_mode=r.holiday_mode,
            valid_from=vf, valid_to=vt, qualifications=frozenset(int(q) for q in (r.qualification_ids or []))))
    return out


def site_specs(db: Session, company_id: int) -> dict[int, SiteSpec]:
    return {s.id: SiteSpec(s.id, s.name, s.requires_explicit_eligibility)
            for s in db.scalars(select(Site).where(Site.company_id == company_id))}


def employee_specs(db: Session, company_id: int, start: date, end: date, include_inactive: bool = False) -> dict[int, EmployeeSpec]:
    q = select(Employee).where(Employee.company_id == company_id)
    if not include_inactive:
        q = q.where(Employee.is_active.is_(True))
    emps = list(db.scalars(q))
    ids = [e.id for e in emps]
    quals, absn, weekly, srules, fixed = (defaultdict(dict), defaultdict(list), defaultdict(list),
                                          defaultdict(dict), defaultdict(dict))
    for q_ in db.scalars(select(EmployeeQualification).where(EmployeeQualification.employee_id.in_(ids))):
        prev = quals[q_.employee_id].get(q_.qualification_id)
        # several records of the same qualification: keep the one valid longest
        if prev is None or (prev[1] is not None and (q_.valid_until is None or q_.valid_until > prev[1])):
            quals[q_.employee_id][q_.qualification_id] = (q_.valid_from, q_.valid_until)
    for a in db.scalars(select(Absence).where(Absence.employee_id.in_(ids), Absence.status == "approved",
                                              Absence.start_date <= end + timedelta(days=8),
                                              Absence.end_date >= start - timedelta(days=8))):
        absn[a.employee_id].append((a.start_date, a.end_date, a.kind))
    for w in db.scalars(select(WeeklyUnavailability).where(WeeklyUnavailability.employee_id.in_(ids))):
        weekly[w.employee_id].append((w.weekday, w.start_time, w.end_time))
    for r in db.scalars(select(EmployeeSiteRule).where(EmployeeSiteRule.employee_id.in_(ids))):
        srules[r.employee_id][r.site_id] = r.rule
    for fp in db.scalars(select(FixedPost).where(FixedPost.employee_id.in_(ids))):
        fixed[fp.employee_id][fp.requirement_id] = frozenset(int(x) for x in fp.weekdays.split(",") if x != "")
    out = {}
    for e in emps:
        out[e.id] = EmployeeSpec(
            id=e.id, name=e.full_name, hire_date=e.hire_date, termination_date=e.termination_date,
            qualifications=dict(quals[e.id]), absences=absn[e.id], weekly_unavailability=weekly[e.id],
            site_rules=srules[e.id], max_hours_week=_f(e.max_hours_week), max_hours_month=_f(e.max_hours_month),
            max_shift_hours=_f(e.max_shift_hours), min_rest_hours=float(e.min_rest_hours),
            max_consecutive_days=e.max_consecutive_days, contract_weekly_hours=float(e.weekly_contract_hours),
            is_relief=e.is_relief, night_preference=e.night_preference, avoid_weekends=e.avoid_weekends,
            fixed_posts=fixed[e.id])
    return out


def compensation_for(db: Session, company_id: int, employee_id: int, on: date) -> pe.Compensation | None:
    t = db.scalar(select(CompensationTerm).where(
        CompensationTerm.company_id == company_id, CompensationTerm.employee_id == employee_id,
        CompensationTerm.effective_from <= on,
        (CompensationTerm.effective_to.is_(None)) | (CompensationTerm.effective_to >= on))
        .order_by(CompensationTerm.effective_from.desc()))
    if t is None:
        return None
    emp = db.get(Employee, employee_id)
    return pe.Compensation(pay_basis=t.pay_basis, hourly_rate=t.hourly_rate, monthly_salary=t.monthly_salary,
                           weekly_hours=emp.weekly_contract_hours, night_premium_pct=t.night_premium_pct,
                           sunday_holiday_premium_pct=t.sunday_holiday_premium_pct,
                           overtime_premium_pct=t.overtime_premium_pct, overwork_premium_pct=t.overwork_premium_pct,
                           effective_from=t.effective_from, effective_to=t.effective_to)


def active_ruleset(db: Session, company_id: int, on: date) -> tuple[PayrollRuleSet | None, dict]:
    rs = db.scalar(select(PayrollRuleSet).where(
        PayrollRuleSet.company_id == company_id, PayrollRuleSet.effective_from <= on,
        (PayrollRuleSet.effective_to.is_(None)) | (PayrollRuleSet.effective_to >= on))
        .order_by(PayrollRuleSet.effective_from.desc()))
    return rs, {**pe.DEFAULT_RULES, **(rs.rules if rs else {})}


def cost_map(db: Session, company_id: int, emps: list[EmployeeSpec], slots: list[Slot], on: date) -> dict:
    _, rset = active_ruleset(db, company_id, on)
    comps = {e.id: compensation_for(db, company_id, e.id, on) for e in emps}
    cache: dict = {}
    out = {}
    for e in emps:
        for s in slots:
            k = (e.id, s.minutes, s.night_minutes, s.sunday_or_holiday_minutes)
            if k not in cache:
                cache[k] = pe.shift_cost_cents(comps[e.id], rset, s.minutes, s.night_minutes, s.sunday_or_holiday_minutes)
            out[(e.id, s.key)] = cache[k]
    return out


def period_slots(db: Session, company_id: int, year: int, month: int) -> list[Slot]:
    start, end = month_range(year, month)
    return demand.expand(requirement_specs(db, company_id), start, end, holidays_set(db, company_id, start, end + timedelta(days=1)))


# ------------------------------------------------------------------ versions
def get_period(db: Session, company_id: int, year: int, month: int, create: bool = True) -> SchedulePeriod | None:
    p = db.scalar(select(SchedulePeriod).where(SchedulePeriod.company_id == company_id,
                                               SchedulePeriod.year == year, SchedulePeriod.month == month))
    if p is None and create:
        p = SchedulePeriod(company_id=company_id, year=year, month=month, revision=0)
        db.add(p)
        db.flush()
    return p


def version_with_status(db: Session, period_id: int, status: str) -> ScheduleVersion | None:
    return db.scalar(select(ScheduleVersion).where(ScheduleVersion.period_id == period_id, ScheduleVersion.status == status)
                     .order_by(ScheduleVersion.number.desc()))


def assignments_of(db: Session, version_id: int) -> list[Assignment]:
    return list(db.scalars(select(Assignment).where(Assignment.version_id == version_id).order_by(Assignment.start_utc)))


def _bump(db: Session, model, obj_id: int, expected: int | None) -> None:
    q = update(model).where(model.id == obj_id)
    if expected is not None:
        q = q.where(model.revision == int(expected))
    rc = db.execute(q.values(revision=model.revision + 1)).rowcount
    if rc != 1:
        db.rollback()
        raise ConflictError("Το πρόγραμμα άλλαξε από άλλον χρήστη. Ανανεώστε τη σελίδα και επαναλάβετε.")


def slot_for_assignment(a: Assignment, slot_map: dict[str, Slot], req_quals: dict[int, frozenset]) -> Slot:
    s = slot_map.get(a.slot_key)
    if s is not None and s.start == a.start_utc and s.end == a.end_utc:
        return s
    # demand changed after assignment: keep the stored times so the validator can flag it
    return Slot(key=a.slot_key, requirement_id=a.requirement_id, site_id=a.site_id, service_date=a.service_date,
                segment=0, position=0, start=a.start_utc, end=a.end_utc,
                qualifications=req_quals.get(a.requirement_id, frozenset()))


def fixed_work_around(db: Session, company_id: int, year: int, month: int) -> list[FixedWork]:
    """Published work in the adjacent months near the boundary (rest/weekly/consecutive rules)."""
    start, end = month_range(year, month)
    out = []
    for (y, m), lo, hi in (((start - timedelta(days=1)).timetuple()[:2], start - timedelta(days=8), start - timedelta(days=1)),
                           ((end + timedelta(days=1)).timetuple()[:2], end + timedelta(days=1), end + timedelta(days=8))):
        p = get_period(db, company_id, y, m, create=False)
        if p is None:
            continue
        v = version_with_status(db, p.id, "published")
        if v is None:
            continue
        for a in db.scalars(select(Assignment).where(Assignment.version_id == v.id, Assignment.service_date >= lo,
                                                     Assignment.service_date <= hi)):
            out.append(FixedWork(a.employee_id, a.start_utc, a.end_utc, a.service_date, f"{a.service_date:%d/%m} (γειτονικός μήνας)"))
    return out


@dataclass
class VersionContext:
    slots: list[Slot]
    slot_map: dict[str, Slot]
    employees: dict[int, EmployeeSpec]
    sites: dict[int, SiteSpec]
    fixed: list[FixedWork]
    req_quals: dict[int, frozenset]


def context_for(db: Session, company_id: int, year: int, month: int) -> VersionContext:
    start, end = month_range(year, month)
    slots = period_slots(db, company_id, year, month)
    reqs = requirement_specs(db, company_id)
    return VersionContext(slots, {s.key: s for s in slots},
                          employee_specs(db, company_id, start - timedelta(days=8), end + timedelta(days=8), include_inactive=True),
                          site_specs(db, company_id), fixed_work_around(db, company_id, year, month),
                          {r.id: r.qualifications for r in reqs})


def pairs_of(assigns: list[Assignment], vc: VersionContext) -> list[tuple[int, Slot]]:
    return [(a.employee_id, slot_for_assignment(a, vc.slot_map, vc.req_quals)) for a in assigns]


def validate_version(db: Session, version: ScheduleVersion, vc: VersionContext | None = None) -> dict:
    p = version.period
    vc = vc or context_for(db, version.company_id, p.year, p.month)
    assigns = assignments_of(db, version.id)
    pairs = pairs_of(assigns, vc)
    inactive = {e.id for e in db.scalars(select(Employee).where(Employee.company_id == version.company_id, Employee.is_active.is_(False)))}
    viol = rules.validate(pairs, vc.employees, vc.sites, vc.fixed, set(vc.slot_map))
    viol += [rules.Violation("UNKNOWN_EMPLOYEE", e, [s.key], "ανενεργός") for e, s in pairs if e in inactive]
    covered = {a.slot_key for a in assigns}
    uncovered = [s for s in vc.slots if s.key not in covered]
    return {"violations": viol, "uncovered": uncovered, "assignments": assigns, "pairs": pairs, "context": vc}


def explain_gaps(db: Session, version: ScheduleVersion, check: dict) -> dict[str, dict]:
    """Blocker analysis for the uncovered slots of a stored version (same logic as after generation)."""
    vc: VersionContext = check["context"]
    p = version.period
    start, end = month_range(p.year, p.month)
    active = {e.id for e in db.scalars(select(Employee).where(Employee.company_id == version.company_id, Employee.is_active.is_(True)))}
    prob = Problem(slots=vc.slots, employees=[e for e in vc.employees.values() if e.id in active], sites=vc.sites,
                   period_start=start, period_end=end, fixed_work=vc.fixed)
    return solver.explain_uncovered(prob, check["pairs"], check["uncovered"])


def diff_versions(db: Session, new: ScheduleVersion, old: ScheduleVersion | None) -> list[dict]:
    a_new = {a.slot_key: a for a in assignments_of(db, new.id)}
    a_old = {a.slot_key: a for a in assignments_of(db, old.id)} if old else {}
    out = []
    for key in sorted(set(a_new) | set(a_old), key=lambda k: (k.split("|")[1], k)):
        n, o = a_new.get(key), a_old.get(key)
        if n and o and n.employee_id == o.employee_id and n.start_utc == o.start_utc:
            continue
        ref = n or o
        out.append({
            "slot_key": key, "date": ref.service_date, "site": ref.site.name, "requirement": ref.requirement.name,
            "time": f"{fmt_local(ref.start_utc, '%H:%M')}–{fmt_local(ref.end_utc, '%H:%M')}",
            "before": o.employee.full_name if o else None, "after": n.employee.full_name if n else None,
            "kind": "added" if not o else "removed" if not n else "changed",
        })
    return out


def _new_version(db: Session, period: SchedulePeriod, user_id: int | None, base: ScheduleVersion | None, note: str) -> ScheduleVersion:
    num = (db.scalar(select(func.max(ScheduleVersion.number)).where(ScheduleVersion.period_id == period.id)) or 0) + 1
    v = ScheduleVersion(company_id=period.company_id, period_id=period.id, number=num, status="draft",
                        base_version_id=base.id if base else None, created_by=user_id, note=note, revision=0)
    db.add(v)
    db.flush()
    return v


def _copy(db: Session, src: Assignment, version: ScheduleVersion, source: str | None = None) -> Assignment:
    a = Assignment(company_id=src.company_id, version_id=version.id, slot_key=src.slot_key, employee_id=src.employee_id,
                   requirement_id=src.requirement_id, site_id=src.site_id, service_date=src.service_date,
                   start_utc=src.start_utc, end_utc=src.end_utc, locked=src.locked, source=source or src.source, note=src.note)
    db.add(a)
    return a


def ensure_draft(db: Session, company_id: int, user_id: int | None, year: int, month: int) -> ScheduleVersion:
    """Return the open draft for the period, creating one from the published version if needed."""
    period = get_period(db, company_id, year, month)
    draft = version_with_status(db, period.id, "draft")
    if draft:
        return draft
    _bump(db, SchedulePeriod, period.id, period.revision)
    pub = version_with_status(db, period.id, "published")
    draft = _new_version(db, period, user_id, pub, "Πρόχειρο από δημοσιευμένο" if pub else "Νέο πρόχειρο")
    if pub:
        for a in assignments_of(db, pub.id):
            _copy(db, a, draft)
    audit.log(db, company_id, user_id, "schedule.draft_created", "schedule_version", draft.id, base=pub.id if pub else None)
    db.flush()
    return draft


# ------------------------------------------------------------------ generation
def generate(db: Session, company_id: int, user_id: int | None, year: int, month: int, *,
             site_ids: set[int] | None = None, dates: set[date] | None = None, weights: Weights | None = None,
             time_limit_s: float = 30.0, expected_period_revision: int | None = None) -> GenerationRun:
    period = get_period(db, company_id, year, month)
    _bump(db, SchedulePeriod, period.id, expected_period_revision)
    start, end = month_range(year, month)
    vc = context_for(db, company_id, year, month)
    draft = version_with_status(db, period.id, "draft")
    pub = version_with_status(db, period.id, "published")
    base = draft or pub
    base_assigns = assignments_of(db, base.id) if base else []
    in_scope = lambda s: (site_ids is None or s.site_id in site_ids) and (dates is None or s.service_date in dates)  # noqa: E731
    scope_slots = [s for s in vc.slots if in_scope(s)]
    scope_keys = {s.key for s in scope_slots}
    carried = [a for a in base_assigns if a.slot_key not in scope_keys]
    locked = {a.slot_key: a.employee_id for a in base_assigns if a.locked and a.slot_key in scope_keys}
    previous = {a.slot_key: a.employee_id for a in (assignments_of(db, pub.id) if pub else []) if a.slot_key in scope_keys}
    fixed = list(vc.fixed) + [FixedWork(a.employee_id, a.start_utc, a.end_utc, a.service_date,
                                        f"{a.service_date:%d/%m} εκτός εύρους") for a in carried]
    active = [e for e in vc.employees.values()
              if db.get(Employee, e.id).is_active]
    p = Problem(slots=scope_slots, employees=active, sites=vc.sites, period_start=start, period_end=end,
                fixed_work=fixed, locked=locked, previous=previous,
                cost_cents=cost_map(db, company_id, active, scope_slots, start), weights=weights or Weights(),
                time_limit_s=time_limit_s, workers=settings.solver_workers)
    res = solver.solve(p)
    report = build_report(db, res, vc)
    report["scope"] = {"sites": sorted(site_ids) if site_ids else None,
                       "dates": sorted(d.isoformat() for d in dates) if dates else None,
                       "carried_assignments": len(carried), "locked": len(locked)}
    run = GenerationRun(company_id=company_id, period_id=period.id, status=res.status, report=report,
                        params={"weights": (weights or Weights()).as_dict(), "time_limit_s": time_limit_s,
                                "base_version_id": base.id if base else None},
                        created_by=user_id)
    db.add(run)
    if res.status in ("complete", "partial"):
        if draft:
            draft.status = "discarded"
        v = _new_version(db, period, user_id, base, f"Αυτόματη δημιουργία ({len(res.assignments)} αναθέσεις)")
        for a in carried:
            _copy(db, a, v, "carried" if a.source == "auto" else a.source)
        for e, s in res.assignments:
            db.add(Assignment(company_id=company_id, version_id=v.id, slot_key=s.key, employee_id=e,
                              requirement_id=s.requirement_id, site_id=s.site_id, service_date=s.service_date,
                              start_utc=s.start, end_utc=s.end, locked=s.key in locked, source="auto"))
        run.version_id = v.id
    audit.log(db, company_id, user_id, "schedule.generate", "generation_run", None, status=res.status,
              period=f"{year}-{month:02d}", coverage=report["stats"].get("coverage_pct"))
    db.commit()
    return run


def _slot_row(s: Slot, vc: VersionContext, db: Session) -> dict:
    req = db.get(CoverageRequirement, s.requirement_id)
    return {"key": s.key, "date": s.service_date.isoformat(), "site_id": s.site_id,
            "site": vc.sites[s.site_id].name if s.site_id in vc.sites else str(s.site_id),
            "requirement": req.name if req else str(s.requirement_id), "position": s.position,
            "start": fmt_local(s.start, "%d/%m %H:%M"), "end": fmt_local(s.end, "%d/%m %H:%M"),
            "hours": round(s.minutes / 60, 2)}


def build_report(db: Session, res: solver.Result, vc: VersionContext) -> dict:
    names = {e.id: e.name for e in vc.employees.values()}
    rep = {"status": res.status, "coverage_status": res.coverage_status, "optimisation_status": res.optimisation_status,
           "proven_max_coverage": res.proven_max_coverage, "messages": res.messages, "stats": dict(res.stats)}
    st = rep["stats"]
    if "per_employee" in st:
        st["per_employee"] = {names.get(int(k), str(k)): {**v, "hours": round(v["minutes"] / 60, 2)}
                              for k, v in st["per_employee"].items()}
    if "uncovered_hours_by_site" in st:
        st["uncovered_hours_by_site"] = {vc.sites[int(k)].name if int(k) in vc.sites else str(k): v
                                         for k, v in st["uncovered_hours_by_site"].items()}
    rep["uncovered"] = [{**_slot_row(s, vc, db), **res.explanations.get(s.key, {})} for s in res.uncovered]
    rep["locked_violations"] = [{**v.as_dict(), "employee": names.get(v.employee_id, "")} for v in res.locked_violations]
    causes = defaultdict(float)
    for u in rep["uncovered"]:
        causes[u["cause"]] += u["hours"]
    rep["recommendations"] = [{"cause": c, "label": solver.CAUSE_LABELS[c], "hint": solver.CAUSE_HINTS[c],
                               "hours": round(h, 2)} for c, h in sorted(causes.items(), key=lambda kv: -kv[1])]
    return rep


# ------------------------------------------------------------------ publication & editing
def publish(db: Session, company_id: int, user_id: int, version_id: int, expected_revision: int,
            accept_gaps: bool = False) -> dict:
    v = get_owned(db, ScheduleVersion, version_id, company_id)
    if v.status != "draft":
        raise ConflictError("Μόνο πρόχειρο πρόγραμμα μπορεί να δημοσιευθεί.")
    check = validate_version(db, v)
    if check["violations"]:
        raise RuleViolation(check["violations"])
    if check["uncovered"] and not accept_gaps:
        raise ConflictError(f"Υπάρχουν {len(check['uncovered'])} ακάλυπτες θέσεις. Επιβεβαιώστε ότι δημοσιεύετε με κενά.")
    _bump(db, ScheduleVersion, v.id, expected_revision)
    period = v.period
    _bump(db, SchedulePeriod, period.id, None)
    prev = version_with_status(db, period.id, "published")
    if prev:
        prev.status = "superseded"
    v.status = "published"
    v.published_by = user_id
    v.published_at = utcnow()
    audit.log(db, company_id, user_id, "schedule.publish", "schedule_version", v.id,
              replaces=prev.id if prev else None, uncovered=len(check["uncovered"]))
    db.commit()
    return {"uncovered": len(check["uncovered"])}


def discard(db: Session, company_id: int, user_id: int, version_id: int, expected_revision: int) -> None:
    v = get_owned(db, ScheduleVersion, version_id, company_id)
    if v.status != "draft":
        raise ConflictError("Μόνο πρόχειρο μπορεί να απορριφθεί.")
    _bump(db, ScheduleVersion, v.id, expected_revision)
    v.status = "discarded"
    audit.log(db, company_id, user_id, "schedule.discard", "schedule_version", v.id)
    db.commit()


def assign(db: Session, company_id: int, user_id: int, version_id: int, slot_key: str, employee_id: int | None,
           expected_revision: int, source: str = "manual", note: str | None = None, commit: bool = True) -> Assignment | None:
    """Set (or clear, employee_id=None) the employee for one slot in a draft. Hard rules are enforced."""
    v = get_owned(db, ScheduleVersion, version_id, company_id)
    if v.status != "draft":
        raise ConflictError("Οι αλλαγές γίνονται μόνο σε πρόχειρο.")
    p = v.period
    vc = context_for(db, company_id, p.year, p.month)
    slot = vc.slot_map.get(slot_key)
    existing = db.scalar(select(Assignment).where(Assignment.version_id == v.id, Assignment.slot_key == slot_key))
    if slot is None and existing is None:
        raise RuleViolation([rules.Violation("UNKNOWN_SLOT", employee_id, [slot_key])])
    if existing is not None and existing.locked:
        raise ConflictError("Η ανάθεση είναι κλειδωμένη. Ξεκλειδώστε την πρώτα.")
    before = existing.employee_id if existing else None
    if employee_id is not None:
        emp = get_owned(db, Employee, employee_id, company_id)
        if not emp.is_active:
            raise RuleViolation([rules.Violation("UNKNOWN_EMPLOYEE", employee_id, [slot_key], "ανενεργός")])
        if slot is None:
            raise RuleViolation([rules.Violation("UNKNOWN_SLOT", employee_id, [slot_key])])
        current = [pr for pr in pairs_of(assignments_of(db, v.id), vc) if pr[1].key != slot_key]
        bl = rules.candidate_blockers(vc.employees[employee_id], slot, vc.sites.get(slot.site_id), current, vc.fixed)
        if bl:
            raise RuleViolation(bl)
    _bump(db, ScheduleVersion, v.id, expected_revision)
    result = None
    if employee_id is None:
        if existing:
            db.delete(existing)
    elif existing:
        existing.employee_id = employee_id
        existing.source = source
        existing.note = note
        result = existing
    else:
        result = Assignment(company_id=company_id, version_id=v.id, slot_key=slot.key, employee_id=employee_id,
                            requirement_id=slot.requirement_id, site_id=slot.site_id, service_date=slot.service_date,
                            start_utc=slot.start, end_utc=slot.end, source=source, note=note)
        db.add(result)
    audit.log(db, company_id, user_id, f"schedule.{source}_assign", "schedule_version", v.id, slot=slot_key,
              before=before, after=employee_id)
    if commit:
        db.commit()
    return result


def set_lock(db: Session, company_id: int, user_id: int, version_id: int, slot_key: str, locked: bool, expected_revision: int) -> None:
    v = get_owned(db, ScheduleVersion, version_id, company_id)
    if v.status != "draft":
        raise ConflictError("Το κλείδωμα αλλάζει μόνο σε πρόχειρο.")
    a = db.scalar(select(Assignment).where(Assignment.version_id == v.id, Assignment.slot_key == slot_key))
    if a is None:
        raise ConflictError("Δεν υπάρχει ανάθεση για κλείδωμα.")
    _bump(db, ScheduleVersion, v.id, expected_revision)
    a.locked = locked
    audit.log(db, company_id, user_id, "schedule.lock" if locked else "schedule.unlock", "schedule_version", v.id, slot=slot_key)
    db.commit()


def published_assignments_between(db: Session, company_id: int, start: date, end: date, employee_id: int | None = None) -> list[Assignment]:
    q = (select(Assignment).join(ScheduleVersion, Assignment.version_id == ScheduleVersion.id)
         .where(Assignment.company_id == company_id, ScheduleVersion.status == "published",
                Assignment.service_date >= start, Assignment.service_date <= end))
    if employee_id is not None:
        q = q.where(Assignment.employee_id == employee_id)
    return list(db.scalars(q.order_by(Assignment.start_utc)))
