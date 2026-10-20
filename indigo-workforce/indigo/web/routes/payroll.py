from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select

from ...models import PayrollEmployeeTotal, PayrollLine, PayrollRun
from ...payroll_engine import CATEGORY_LABELS, RULE_LABELS
from ...services import payroll as pay, reports, scheduling as sch
from ...services.attendance import LockedPeriod
from ...services.scope import get_owned
from ..app import back, render
from ..deps import Ctx, form, i, need, s, ym

router = APIRouter(prefix="/payroll")


@router.get("")
def page(request: Request, ctx: Ctx = Depends(need("payroll.read")), year: str = "", month: str = ""):
    y, m = ym(year, month)
    period = pay.get_period(ctx.db, ctx.cid, y, m)
    ctx.db.commit()
    run = pay.current_run(ctx.db, period)
    totals = list(ctx.db.scalars(select(PayrollEmployeeTotal).where(PayrollEmployeeTotal.run_id == run.id))) if run else []
    totals.sort(key=lambda t: t.employee.full_name)
    runs = list(ctx.db.scalars(select(PayrollRun).where(PayrollRun.period_id == period.id).order_by(PayrollRun.version_no.desc())))
    rs, rules = sch.active_ruleset(ctx.db, ctx.cid, sch.month_range(y, m)[0])
    return render(request, ctx, "payroll.html", y=y, m=m, period=period, run=run, totals=totals, runs=runs, rs=rs, rules=rules,
                  RULE_LABELS=RULE_LABELS)


@router.get("/runs/{rid}")
def run_detail(rid: int, request: Request, ctx: Ctx = Depends(need("payroll.read")), employee: int = 0):
    run = get_owned(ctx.db, PayrollRun, rid, ctx.cid)
    q = select(PayrollLine).where(PayrollLine.run_id == run.id)
    if employee:
        q = q.where(PayrollLine.employee_id == employee)
    lines = list(ctx.db.scalars(q.order_by(PayrollLine.employee_id, PayrollLine.id)))
    totals = {t.employee_id: t for t in ctx.db.scalars(select(PayrollEmployeeTotal).where(PayrollEmployeeTotal.run_id == run.id))}
    period = ctx.db.get(pay.PayrollPeriod, run.period_id)
    return render(request, ctx, "payroll_run.html", run=run, lines=lines, totals=totals, period=period, CATEGORY_LABELS=CATEGORY_LABELS)


def _url(y, m):
    return f"/payroll?year={y}&month={m}"


@router.post("/run")
async def run(request: Request, ctx: Ctx = Depends(need("payroll.run"))):
    f = await form(request, ctx)
    y, m = ym(f.get("year"), f.get("month"))
    try:
        r = pay.run(ctx.db, ctx.cid, ctx.user.id, y, m, i(f.get("revision"), True))
    except (LockedPeriod, sch.ConflictError) as exc:
        return back(_url(y, m), err=str(exc))
    return back(_url(y, m), f"Υπολογισμός έκδοσης {r.version_no} ολοκληρώθηκε")


@router.post("/lock")
async def lock(request: Request, ctx: Ctx = Depends(need("payroll.lock"))):
    f = await form(request, ctx)
    y, m = ym(f.get("year"), f.get("month"))
    try:
        pay.lock(ctx.db, ctx.cid, ctx.user.id, y, m, i(f.get("revision"), True))
    except (ValueError, sch.ConflictError) as exc:
        return back(_url(y, m), err=str(exc))
    return back(_url(y, m), "Η περίοδος κλειδώθηκε")


@router.post("/reopen")
async def reopen(request: Request, ctx: Ctx = Depends(need("payroll.reopen"))):
    f = await form(request, ctx)
    y, m = ym(f.get("year"), f.get("month"))
    try:
        pay.reopen(ctx.db, ctx.cid, ctx.user.id, y, m, s(f.get("reason"), 1000) or "", i(f.get("revision"), True))
    except (ValueError, sch.ConflictError) as exc:
        return back(_url(y, m), err=str(exc))
    return back(_url(y, m), "Η περίοδος άνοιξε ξανά (καταγράφηκε στο ιστορικό)")


@router.get("/runs/{rid}/export.csv")
def export(rid: int, ctx: Ctx = Depends(need("payroll.read"))):
    ctx.require("reports.export")
    run = get_owned(ctx.db, PayrollRun, rid, ctx.cid)
    period = ctx.db.get(pay.PayrollPeriod, run.period_id)
    return Response(reports.export_payroll(ctx.db, ctx.cid, run), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="payroll_estimate_{period.year}_{period.month:02d}_v{run.version_no}.csv"'})
