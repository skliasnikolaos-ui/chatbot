"""Planned work vs actual attendance vs approved payable hours.

* Planned  = assignments in the published schedule.
* Actual   = AttendanceRecord (append-only; a correction supersedes, never overwrites).
* Approved = ApprovedHours (append-only; explicit approval by an authorised user).
Nothing here copies the plan into attendance. "Approve as planned" is an
explicit action that creates an ApprovedHours row with basis="plan" and is
shown as such in reconciliation.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ApprovedHours, Assignment, AttendanceRecord, Employee, PayrollPeriod
from ..timeutil import ATHENS, minutes_between
from . import audit, scheduling as sch
from .scope import get_owned

TOLERANCE_MIN = 10


class LockedPeriod(Exception):
    pass


def ensure_open(db: Session, company_id: int, d: date) -> None:
    p = db.scalar(select(PayrollPeriod).where(PayrollPeriod.company_id == company_id, PayrollPeriod.year == d.year,
                                              PayrollPeriod.month == d.month))
    if p is not None and p.status == "locked":
        raise LockedPeriod(f"Η μισθολογική περίοδος {d.month:02d}/{d.year} είναι κλειδωμένη.")


def _validate_interval(start: datetime, end: datetime) -> None:
    if end <= start:
        raise ValueError("Η λήξη πρέπει να είναι μετά την έναρξη.")
    if end - start > timedelta(hours=24):
        raise ValueError("Διάστημα μεγαλύτερο των 24 ωρών.")


def record_attendance(db: Session, company_id: int, user_id: int, employee_id: int, clock_in: datetime, clock_out: datetime,
                      assignment_id: int | None = None, site_id: int | None = None, reason: str | None = None,
                      supersedes_id: int | None = None) -> AttendanceRecord:
    get_owned(db, Employee, employee_id, company_id)
    _validate_interval(clock_in, clock_out)
    work_date = clock_in.astimezone(ATHENS).date()
    ensure_open(db, company_id, work_date)
    if assignment_id is not None:
        a = get_owned(db, Assignment, assignment_id, company_id)
        if a.employee_id != employee_id:
            raise ValueError("Η βάρδια ανήκει σε άλλον εργαζόμενο· καταχωρίστε την ως παρουσία χωρίς προγραμματισμό.")
        site_id = site_id or a.site_id
    old = None
    if supersedes_id is not None:
        old = get_owned(db, AttendanceRecord, supersedes_id, company_id)
        if old.status != "active":
            raise ValueError("Η εγγραφή έχει ήδη διορθωθεί.")
        if not reason:
            raise ValueError("Απαιτείται αιτιολογία διόρθωσης.")
        ensure_open(db, company_id, old.work_date)
        old.status = "superseded"
        assignment_id = assignment_id or old.assignment_id
        site_id = site_id or old.site_id
    rec = AttendanceRecord(company_id=company_id, employee_id=employee_id, assignment_id=assignment_id, site_id=site_id,
                           work_date=work_date, clock_in=clock_in, clock_out=clock_out, reason=reason,
                           supersedes_id=supersedes_id, created_by=user_id)
    db.add(rec)
    db.flush()
    audit.log(db, company_id, user_id, "attendance.correct" if old else "attendance.record", "attendance", rec.id,
              supersedes=supersedes_id, employee=employee_id, reason=reason)
    db.commit()
    return rec


def approve(db: Session, company_id: int, user_id: int, *, employee_id: int, start: datetime, end: datetime, basis: str,
            attendance_id: int | None = None, assignment_id: int | None = None, site_id: int | None = None,
            reason: str | None = None, supersedes_id: int | None = None) -> ApprovedHours:
    get_owned(db, Employee, employee_id, company_id)
    if basis not in ("attendance", "plan", "manual"):
        raise ValueError("basis")
    _validate_interval(start, end)
    work_date = start.astimezone(ATHENS).date()
    ensure_open(db, company_id, work_date)
    if attendance_id is not None:
        att = get_owned(db, AttendanceRecord, attendance_id, company_id)
        if att.status != "active" or att.employee_id != employee_id:
            raise ValueError("Μη έγκυρη εγγραφή παρουσίας.")
        site_id = site_id or att.site_id
        assignment_id = assignment_id or att.assignment_id
    if assignment_id is not None:
        a = get_owned(db, Assignment, assignment_id, company_id)
        if a.employee_id != employee_id:
            raise ValueError("Η βάρδια ανήκει σε άλλον εργαζόμενο.")
        site_id = site_id or a.site_id
    if basis == "manual" and not reason:
        raise ValueError("Απαιτείται αιτιολογία για χειροκίνητη έγκριση.")
    if supersedes_id is not None:
        old = get_owned(db, ApprovedHours, supersedes_id, company_id)
        if old.status != "active":
            raise ValueError("Η έγκριση έχει ήδη αντικατασταθεί.")
        if not reason:
            raise ValueError("Απαιτείται αιτιολογία διόρθωσης.")
        ensure_open(db, company_id, old.work_date)
        old.status = "superseded"
    else:
        dup = db.scalar(select(ApprovedHours).where(ApprovedHours.company_id == company_id, ApprovedHours.status == "active",
                                                    ApprovedHours.employee_id == employee_id,
                                                    ApprovedHours.start_utc < end, ApprovedHours.end_utc > start))
        if dup is not None:
            raise ValueError("Υπάρχουν ήδη εγκεκριμένες ώρες που επικαλύπτονται· χρησιμοποιήστε διόρθωση.")
    ah = ApprovedHours(company_id=company_id, employee_id=employee_id, attendance_id=attendance_id, assignment_id=assignment_id,
                       site_id=site_id, work_date=work_date, start_utc=start, end_utc=end, basis=basis, reason=reason,
                       supersedes_id=supersedes_id, approved_by=user_id)
    db.add(ah)
    db.flush()
    audit.log(db, company_id, user_id, "hours.correct" if supersedes_id else "hours.approve", "approved_hours", ah.id,
              basis=basis, employee=employee_id, supersedes=supersedes_id, reason=reason)
    db.commit()
    return ah


def void_approval(db: Session, company_id: int, user_id: int, approved_id: int, reason: str) -> None:
    ah = get_owned(db, ApprovedHours, approved_id, company_id)
    if not reason:
        raise ValueError("Απαιτείται αιτιολογία.")
    ensure_open(db, company_id, ah.work_date)
    if ah.status != "active":
        raise ValueError("Η έγκριση δεν είναι ενεργή.")
    ah.status = "voided"
    audit.log(db, company_id, user_id, "hours.void", "approved_hours", ah.id, reason=reason)
    db.commit()


def reconcile(db: Session, company_id: int, start: date, end: date) -> list[dict]:
    """One row per planned assignment plus rows for unplanned attendance/approvals, with discrepancies."""
    planned = sch.published_assignments_between(db, company_id, start, end)
    atts = list(db.scalars(select(AttendanceRecord).where(AttendanceRecord.company_id == company_id, AttendanceRecord.status == "active",
                                                          AttendanceRecord.work_date >= start, AttendanceRecord.work_date <= end)))
    apps = list(db.scalars(select(ApprovedHours).where(ApprovedHours.company_id == company_id, ApprovedHours.status == "active",
                                                       ApprovedHours.work_date >= start, ApprovedHours.work_date <= end)))
    att_by_asg = {a.assignment_id: a for a in atts if a.assignment_id}
    app_by_asg = {a.assignment_id: a for a in apps if a.assignment_id}
    app_by_att = {a.attendance_id: a for a in apps if a.attendance_id}
    used_att, used_app = set(), set()
    rows = []

    def flags(plan, att, app) -> list[str]:
        f = []
        if plan and not att:
            f.append("Χωρίς καταγραφή παρουσίας")
        if att and not plan:
            f.append("Παρουσία χωρίς προγραμματισμό")
        if plan and att:
            if abs(minutes_between(plan.start_utc, att.clock_in)) > TOLERANCE_MIN:
                f.append(f"Διαφορά έναρξης {minutes_between(plan.start_utc, att.clock_in):+d}′")
            if abs(minutes_between(plan.end_utc, att.clock_out)) > TOLERANCE_MIN:
                f.append(f"Διαφορά λήξης {minutes_between(plan.end_utc, att.clock_out):+d}′")
        if app is None and (att or plan):
            f.append("Εκκρεμεί έγκριση ωρών")
        if app and att and (app.start_utc != att.clock_in or app.end_utc != att.clock_out):
            f.append("Εγκεκριμένες ≠ πραγματικές")
        if app and app.basis == "plan":
            f.append("Εγκρίθηκε βάσει προγράμματος")
        return f

    for p in planned:
        att = att_by_asg.get(p.id)
        app = app_by_asg.get(p.id) or (app_by_att.get(att.id) if att else None)
        if att:
            used_att.add(att.id)
        if app:
            used_app.add(app.id)
        rows.append({"date": p.service_date, "employee": p.employee, "plan": p, "att": att, "app": app, "flags": flags(p, att, app)})
    for att in atts:
        if att.id in used_att:
            continue
        app = app_by_att.get(att.id)
        if app:
            used_app.add(app.id)
        rows.append({"date": att.work_date, "employee": att.employee, "plan": None, "att": att, "app": app, "flags": flags(None, att, app)})
    for app in apps:
        if app.id not in used_app:
            rows.append({"date": app.work_date, "employee": app.employee, "plan": None, "att": None, "app": app,
                         "flags": ["Έγκριση χωρίς πρόγραμμα/παρουσία"] + (["Χειροκίνητη έγκριση"] if app.basis == "manual" else [])})
    rows.sort(key=lambda r: (r["date"], r["employee"].full_name))
    return rows
