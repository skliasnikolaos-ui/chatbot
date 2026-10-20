from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from ...models import Absence, EmployeeQualification, Employee, PayrollPeriod
from ...services import attendance as att, replacements as rep, reports, scheduling as sch
from ...timeutil import ATHENS
from ..app import render
from ..deps import Ctx, need

router = APIRouter()


@router.get("/")
def dashboard(request: Request, ctx: Ctx = Depends(need("dashboard.read"))):
    db, cid = ctx.db, ctx.cid
    today = datetime.now(ATHENS).date()
    months = [(today.year, today.month)]
    nxt = (today.replace(day=1) + timedelta(days=32))
    months.append((nxt.year, nxt.month))
    cov = []
    for y, m in months:
        c = reports.coverage(db, cid, y, m)
        period = sch.get_period(db, cid, y, m, create=False)
        draft = sch.version_with_status(db, period.id, "draft") if period else None
        cov.append({"year": y, "month": m, "c": c, "draft": draft})
    pending_abs = list(db.scalars(select(Absence).where(Absence.company_id == cid, Absence.status == "requested")))
    recent_abs = list(db.scalars(select(Absence).where(Absence.company_id == cid, Absence.status == "approved",
                                                       Absence.end_date >= today)))
    needs_replacement = []
    for a in recent_abs:
        for asg in rep.affected_assignments(db, cid, a):
            if asg.start_utc.astimezone(ATHENS).date() >= today:
                v = ctx.db.get(sch.ScheduleVersion, asg.version_id)
                if v.status == "published":
                    needs_replacement.append((a, asg))
    expiring = list(db.scalars(select(EmployeeQualification).join(Employee, Employee.id == EmployeeQualification.employee_id)
                               .where(EmployeeQualification.company_id == cid, Employee.is_active.is_(True),
                                      EmployeeQualification.valid_until.is_not(None),
                                      EmployeeQualification.valid_until <= today + timedelta(days=30))
                               .order_by(EmployeeQualification.valid_until)))
    recon = att.reconcile(db, cid, today - timedelta(days=14), today - timedelta(days=1))
    pending_hours = sum(1 for r in recon if "Εκκρεμεί έγκριση ωρών" in r["flags"])
    discrepancies = sum(1 for r in recon if any(f.startswith(("Διαφορά", "Παρουσία χωρίς", "Χωρίς καταγραφή")) for f in r["flags"]))
    open_payroll = list(db.scalars(select(PayrollPeriod).where(PayrollPeriod.company_id == cid, PayrollPeriod.status == "open")))
    emp_names = {e.id: e.full_name for e in db.scalars(select(Employee).where(Employee.company_id == cid))}
    return render(request, ctx, "dashboard.html", cov=cov, pending_abs=pending_abs, needs_replacement=needs_replacement,
                  expiring=expiring, pending_hours=pending_hours, discrepancies=discrepancies, open_payroll=open_payroll,
                  emp_names=emp_names, today=today)
