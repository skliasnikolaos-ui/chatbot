from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from ...services import reports
from ..app import render
from ..deps import Ctx, need, ym

router = APIRouter(prefix="/reports")


def _csv(data: bytes, name: str) -> Response:
    return Response(data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("")
def page(request: Request, ctx: Ctx = Depends(need("reports.read")), year: str = "", month: str = ""):
    y, m = ym(year, month)
    cov = reports.coverage(ctx.db, ctx.cid, y, m)
    hrs = reports.hours(ctx.db, ctx.cid, y, m)
    costs = reports.costs(ctx.db, ctx.cid, y, m) if ctx.can("reports.cost") else None
    return render(request, ctx, "reports.html", y=y, m=m, cov=cov, hrs=hrs, costs=costs)


@router.get("/gaps.csv")
def gaps(ctx: Ctx = Depends(need("reports.export")), year: str = "", month: str = ""):
    y, m = ym(year, month)
    return _csv(reports.export_gaps(ctx.db, ctx.cid, y, m), f"coverage_gaps_{y}_{m:02d}.csv")


@router.get("/hours.csv")
def hours(ctx: Ctx = Depends(need("reports.export")), year: str = "", month: str = ""):
    y, m = ym(year, month)
    return _csv(reports.export_hours(ctx.db, ctx.cid, y, m), f"hours_{y}_{m:02d}.csv")


@router.get("/costs.csv")
def costs(ctx: Ctx = Depends(need("reports.cost")), year: str = "", month: str = ""):
    ctx.require("reports.export")
    y, m = ym(year, month)
    return _csv(reports.export_costs(ctx.db, ctx.cid, y, m), f"costs_estimate_{y}_{m:02d}.csv")
