"""Absences after publication → affected assignments → ranked replacement candidates.

Replacements are applied to a draft version only; the manager must publish
that draft (standard approval) before the change becomes the official plan.
Published versions are never edited, so assignment history is preserved.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import rules
from ..models import Absence, Assignment, Employee, ScheduleVersion
from ..timeutil import ATHENS, iso_week_key, local_dates_touched
from . import audit, scheduling as sch
from .scope import get_owned


def affected_assignments(db: Session, company_id: int, absence: Absence) -> list[Assignment]:
    """Assignments of the absent employee in published or draft versions that touch the absence dates."""
    q = (select(Assignment).join(ScheduleVersion, Assignment.version_id == ScheduleVersion.id)
         .where(Assignment.company_id == company_id, Assignment.employee_id == absence.employee_id,
                ScheduleVersion.status.in_(("published", "draft")),
                Assignment.service_date >= absence.start_date - timedelta(days=1),
                Assignment.service_date <= absence.end_date).order_by(Assignment.start_utc))
    out = []
    for a in db.scalars(q):
        if any(absence.start_date <= d <= absence.end_date for d in local_dates_touched(a.start_utc, a.end_utc, ATHENS)):
            out.append(a)
    return out


def candidates(db: Session, company_id: int, assignment_id: int) -> dict:
    a = get_owned(db, Assignment, assignment_id, company_id)
    v = db.get(ScheduleVersion, a.version_id)
    p = v.period
    vc = sch.context_for(db, company_id, p.year, p.month)
    slot = sch.slot_for_assignment(a, vc.slot_map, vc.req_quals)
    # evaluate against the draft if one exists (it is where the change will be applied), else the published plan
    target = sch.version_with_status(db, p.id, "draft") or v
    out = slot_candidates(db, company_id, target, slot, vc, exclude_employee=a.employee_id)
    out.update({"assignment": a, "version": v})
    return out


def slot_candidates(db: Session, company_id: int, target: ScheduleVersion, slot, vc, exclude_employee: int | None = None) -> dict:
    """Rank every active employee for one slot of `target` (hard-rule violators listed separately with reasons)."""
    assigns = sch.assignments_of(db, target.id)
    current = [pr for pr in sch.pairs_of(assigns, vc) if pr[1].key != slot.key]
    cost = sch.cost_map(db, company_id, list(vc.employees.values()), [slot], slot.service_date)
    recent_from = slot.service_date - timedelta(days=60)
    history = sch.published_assignments_between(db, company_id, recent_from, slot.service_date)
    site_shifts: dict[int, int] = {}
    for h in history:
        if h.site_id == slot.site_id:
            site_shifts[h.employee_id] = site_shifts.get(h.employee_id, 0) + 1
    eligible, rejected = [], []
    active_ids = set(db.scalars(select(Employee.id).where(Employee.company_id == company_id, Employee.is_active.is_(True))))
    for emp in vc.employees.values():
        if emp.id == exclude_employee or emp.id not in active_ids:
            continue
        bl = rules.candidate_blockers(emp, slot, vc.sites.get(slot.site_id), current, vc.fixed)
        if bl:
            rejected.append({"employee_id": emp.id, "name": emp.name, "reasons": sorted({b.label for b in bl})})
            continue
        mine = sorted((s for e, s in current if e == emp.id), key=lambda s: s.start)
        wk = iso_week_key(slot.service_date)
        week_min = sum(s.minutes for s in mine if iso_week_key(s.service_date) == wk)
        month_min = sum(s.minutes for s in mine if (s.service_date.year, s.service_date.month) == (slot.service_date.year, slot.service_date.month))
        before = [s for s in mine if s.end <= slot.start]
        after = [s for s in mine if s.start >= slot.end]
        rest_before = round((slot.start - before[-1].end).total_seconds() / 3600, 1) if before else None
        rest_after = round((after[0].start - slot.end).total_seconds() / 3600, 1) if after else None
        target_week = emp.contract_weekly_hours * 60
        overload = max(0, week_min + slot.minutes - target_week)
        c = cost.get((emp.id, slot.key), 0)
        familiarity = site_shifts.get(emp.id, 0)
        pref_pen = (1 if (slot.is_night and emp.night_preference == "avoid") else 0) + (1 if (slot.is_weekend and emp.avoid_weekends) else 0)
        score = c / 100 + overload / 60 * 5 - min(familiarity, 10) * 3 + pref_pen * 15 + (5 if emp.is_relief else 0)
        reasons = []
        if overload == 0:
            reasons.append("εντός συμβατικών ωρών εβδομάδας")
        else:
            reasons.append(f"+{overload / 60:.1f}ω πάνω από συμβατικές ώρες εβδομάδας")
        if familiarity:
            reasons.append(f"γνωρίζει το σημείο ({familiarity} βάρδιες τελευταίες 60 ημ.)")
        if pref_pen:
            reasons.append("αντίθετο με δηλωμένη προτίμηση")
        if emp.is_relief:
            reasons.append("εργαζόμενος αναπλήρωσης")
        eligible.append({
            "employee_id": emp.id, "name": emp.name, "score": round(score, 1), "cost_eur": round(c / 100, 2),
            "week_hours_after": round((week_min + slot.minutes) / 60, 1), "month_hours_after": round((month_min + slot.minutes) / 60, 1),
            "rest_before_h": rest_before, "rest_after_h": rest_after, "site_familiarity": familiarity, "reasons": reasons,
        })
    eligible.sort(key=lambda c: (c["score"], c["name"]))
    return {"slot": slot, "target_version": target, "eligible": eligible, "rejected": rejected}


def apply(db: Session, company_id: int, user_id: int, assignment_id: int, new_employee_id: int | None, reason: str) -> ScheduleVersion:
    """Stage a replacement (or explicit un-assignment) in the period draft. Publication is a separate approval."""
    a = get_owned(db, Assignment, assignment_id, company_id)
    v = db.get(ScheduleVersion, a.version_id)
    p = v.period
    draft = sch.ensure_draft(db, company_id, user_id, p.year, p.month)
    db.flush()
    in_draft = db.scalar(select(Assignment).where(Assignment.version_id == draft.id, Assignment.slot_key == a.slot_key))
    if in_draft is not None and in_draft.locked:
        in_draft.locked = False  # the manager explicitly replaces this shift
        audit.log(db, company_id, user_id, "schedule.unlock", "schedule_version", draft.id, slot=a.slot_key, why="replacement")
        db.flush()
    db.refresh(draft)
    sch.assign(db, company_id, user_id, draft.id, a.slot_key, new_employee_id, draft.revision,
               source="replacement", note=reason or None, commit=False)
    audit.log(db, company_id, user_id, "replacement.staged", "assignment", a.id, draft=draft.id,
              slot=a.slot_key, from_employee=a.employee_id, to_employee=new_employee_id, reason=reason)
    db.commit()
    return draft
