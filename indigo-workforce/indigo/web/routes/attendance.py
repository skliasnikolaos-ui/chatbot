from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from ...models import Assignment, AttendanceRecord, ApprovedHours, Employee, Site
from ...services import attendance as att
from ...services.scope import get_owned
from ...timeutil import ATHENS
from ..app import back, render
from ..deps import BadInput, Ctx, d, form, i, local_dt, need, s

router = APIRouter(prefix="/attendance")


@router.get("")
def page(request: Request, ctx: Ctx = Depends(need("attendance.read")), date_from: str = "", date_to: str = "", only: str = ""):
    today = datetime.now(ATHENS).date()
    d0 = d(date_from) or today - timedelta(days=7)
    d1 = d(date_to) or today
    if (d1 - d0).days > 62:
        raise BadInput("Μέγιστο εύρος 62 ημέρες")
    rows = att.reconcile(ctx.db, ctx.cid, d0, d1)
    if only == "issues":
        rows = [r for r in rows if any(not f.startswith("Εγκρίθηκε βάσει") for f in r["flags"])]
    emps = list(ctx.db.scalars(select(Employee).where(Employee.company_id == ctx.cid, Employee.is_active.is_(True)).order_by(Employee.last_name)))
    sites = list(ctx.db.scalars(select(Site).where(Site.company_id == ctx.cid).order_by(Site.name)))
    return render(request, ctx, "attendance.html", rows=rows, d0=d0, d1=d1, only=only, emps=emps, sites=sites)


def _ret(f: dict) -> str:
    return f"/attendance?date_from={f.get('date_from', '')}&date_to={f.get('date_to', '')}&only={f.get('only', '')}"


@router.post("/record")
async def record(request: Request, ctx: Ctx = Depends(need("attendance.write"))):
    f = await form(request, ctx)
    try:
        att.record_attendance(ctx.db, ctx.cid, ctx.user.id, i(f.get("employee_id"), True, "εργαζόμενος"),
                              local_dt(f.get("clock_in"), "προσέλευση"), local_dt(f.get("clock_out"), "αποχώρηση"),
                              assignment_id=i(f.get("assignment_id")), site_id=get_owned(ctx.db, Site, f["site_id"], ctx.cid).id if f.get("site_id") else None,
                              reason=s(f.get("reason"), 500), supersedes_id=i(f.get("supersedes_id")))
    except (ValueError, att.LockedPeriod) as exc:
        ctx.db.rollback()
        return back(_ret(f), err=str(exc))
    return back(_ret(f), "Η παρουσία καταχωρίστηκε")


@router.post("/approve")
async def approve(request: Request, ctx: Ctx = Depends(need("attendance.approve"))):
    f = await form(request, ctx)
    basis = f.get("basis")
    try:
        if basis == "attendance":
            rec = get_owned(ctx.db, AttendanceRecord, f.get("attendance_id"), ctx.cid)
            start, end = rec.clock_in, rec.clock_out
            if f.get("start") and f.get("end"):
                start, end = local_dt(f["start"]), local_dt(f["end"])
                basis_reason = s(f.get("reason"), 500)
                if (start, end) != (rec.clock_in, rec.clock_out) and not basis_reason:
                    raise ValueError("Απαιτείται αιτιολογία όταν οι εγκεκριμένες ώρες διαφέρουν από την παρουσία.")
            att.approve(ctx.db, ctx.cid, ctx.user.id, employee_id=rec.employee_id, start=start, end=end, basis="attendance",
                        attendance_id=rec.id, reason=s(f.get("reason"), 500), supersedes_id=i(f.get("supersedes_id")))
        elif basis == "plan":
            a = get_owned(ctx.db, Assignment, f.get("assignment_id"), ctx.cid)
            att.approve(ctx.db, ctx.cid, ctx.user.id, employee_id=a.employee_id, start=a.start_utc, end=a.end_utc, basis="plan",
                        assignment_id=a.id, reason=s(f.get("reason"), 500) or "Έγκριση βάσει προγράμματος (χωρίς καταγραφή παρουσίας)",
                        supersedes_id=i(f.get("supersedes_id")))
        elif basis == "manual":
            att.approve(ctx.db, ctx.cid, ctx.user.id, employee_id=get_owned(ctx.db, Employee, f.get("employee_id"), ctx.cid).id,
                        start=local_dt(f.get("start")), end=local_dt(f.get("end")), basis="manual",
                        site_id=get_owned(ctx.db, Site, f["site_id"], ctx.cid).id if f.get("site_id") else None,
                        reason=s(f.get("reason"), 500), supersedes_id=i(f.get("supersedes_id")))
        else:
            raise BadInput("Μη έγκυρη βάση έγκρισης")
    except (ValueError, att.LockedPeriod) as exc:
        ctx.db.rollback()
        return back(_ret(f), err=str(exc))
    return back(_ret(f), "Οι ώρες εγκρίθηκαν")


@router.post("/void")
async def void(request: Request, ctx: Ctx = Depends(need("attendance.approve"))):
    f = await form(request, ctx)
    try:
        att.void_approval(ctx.db, ctx.cid, ctx.user.id, i(f.get("approved_id"), True), s(f.get("reason"), 500) or "")
    except (ValueError, att.LockedPeriod) as exc:
        ctx.db.rollback()
        return back(_ret(f), err=str(exc))
    return back(_ret(f), "Η έγκριση ακυρώθηκε")


@router.get("/history/{kind}/{rid}")
def history(kind: str, rid: int, request: Request, ctx: Ctx = Depends(need("attendance.read"))):
    model = {"attendance": AttendanceRecord, "approved": ApprovedHours}.get(kind)
    if model is None:
        raise BadInput("Άγνωστος τύπος")
    chain = []
    cur = get_owned(ctx.db, model, rid, ctx.cid)
    while cur is not None and len(chain) < 50:
        chain.append(cur)
        cur = get_owned(ctx.db, model, cur.supersedes_id, ctx.cid) if cur.supersedes_id else None
    # later corrections of this record
    nxt = ctx.db.scalar(select(model).where(model.supersedes_id == rid, model.company_id == ctx.cid))
    while nxt is not None and len(chain) < 100:
        chain.insert(0, nxt)
        nxt = ctx.db.scalar(select(model).where(model.supersedes_id == nxt.id, model.company_id == ctx.cid))
    return render(request, ctx, "history.html", chain=chain, kind=kind)
