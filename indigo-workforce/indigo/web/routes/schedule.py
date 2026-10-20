from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select

from ...engine.rules import LABELS
from ...engine.solver import CAUSE_HINTS, CAUSE_LABELS
from ...engine.types import Weights
from ...models import VERSION_STATUS, CoverageRequirement, Employee, GenerationRun, ScheduleVersion, Site
from ...services import replacements as rep, reports, scheduling as sch
from ...services.scope import get_owned
from ...timeutil import month_dates
from ..app import back, render
from ..deps import BadInput, Ctx, d, form, i, need, ym

router = APIRouter()


def _versions(db, period):
    if period is None:
        return []
    return list(db.scalars(select(ScheduleVersion).where(ScheduleVersion.period_id == period.id).order_by(ScheduleVersion.number.desc())))


@router.get("/schedule")
def board(request: Request, ctx: Ctx = Depends(need("schedule.read")), year: str = "", month: str = "", v: str = "", view: str = "sites"):
    db, cid = ctx.db, ctx.cid
    y, m = ym(year, month)
    period = sch.get_period(db, cid, y, m, create=False)
    versions = _versions(db, period)
    version = None
    if v:
        version = get_owned(db, ScheduleVersion, v, cid)
    elif period:
        version = sch.version_with_status(db, period.id, "draft") or sch.version_with_status(db, period.id, "published")
    days = month_dates(y, m)
    check = sch.validate_version(db, version) if version else None
    vc = check["context"] if check else sch.context_for(db, cid, y, m)
    assigns = {a.slot_key: a for a in check["assignments"]} if check else {}
    bad_keys = defaultdict(list)
    for viol in (check["violations"] if check else []):
        for k in viol.slot_keys:
            bad_keys[k].append(viol.label)
    reqs = {r.id: r for r in db.scalars(select(CoverageRequirement).where(CoverageRequirement.company_id == cid))}
    sites = {s_.id: s_ for s_ in db.scalars(select(Site).where(Site.company_id == cid))}
    # rows: (requirement, segment, position); cells by date
    rows: dict[tuple, dict] = {}
    for sl in vc.slots:
        rk = (sites[sl.site_id].name, reqs[sl.requirement_id].name, sl.segment, sl.position, sl.requirement_id)
        rows.setdefault(rk, {})[sl.service_date] = sl
    emp_rows: dict[int, dict] = defaultdict(lambda: defaultdict(list))
    for k, a in assigns.items():
        emp_rows[a.employee_id][a.service_date].append(a)
    emps = {e.id: e for e in db.scalars(select(Employee).where(Employee.company_id == cid))}
    unc = len(check["uncovered"]) if check else len(vc.slots)
    return render(request, ctx, "schedule.html", y=y, m=m, period=period, versions=versions, version=version, days=days,
                  rows=sorted(rows.items(), key=lambda kv: kv[0][:4]), assigns=assigns, bad_keys=bad_keys, view=view,
                  emp_rows=emp_rows, emps=emps, check=check, uncovered_count=unc, VERSION_STATUS=VERSION_STATUS,
                  holidays=sch.holidays_set(db, cid, days[0], days[-1]))


@router.post("/schedule/draft")
async def make_draft(request: Request, ctx: Ctx = Depends(need("schedule.write"))):
    f = await form(request, ctx)
    y, m = ym(f.get("year"), f.get("month"))
    try:
        dr = sch.ensure_draft(ctx.db, ctx.cid, ctx.user.id, y, m)
        ctx.db.commit()
    except sch.ConflictError as exc:
        return back(f"/schedule?year={y}&month={m}", err=str(exc))
    return back(f"/schedule?year={y}&month={m}&v={dr.id}", "Δημιουργήθηκε πρόχειρο για επεξεργασία")


@router.get("/schedule/slot")
def slot_page(request: Request, ctx: Ctx = Depends(need("schedule.read")), v: int = 0, key: str = ""):
    db = ctx.db
    version = get_owned(db, ScheduleVersion, v, ctx.cid)
    p = version.period
    vc = sch.context_for(db, ctx.cid, p.year, p.month)
    a = next((x for x in sch.assignments_of(db, version.id) if x.slot_key == key), None)
    slot = vc.slot_map.get(key) or (sch.slot_for_assignment(a, vc.slot_map, vc.req_quals) if a else None)
    if slot is None:
        raise BadInput("Άγνωστη θέση")
    cands = rep.slot_candidates(db, ctx.cid, version, slot, vc, exclude_employee=a.employee_id if a else None)
    req = db.get(CoverageRequirement, slot.requirement_id)
    return render(request, ctx, "slot.html", version=version, slot=slot, a=a, cands=cands, req=req,
                  site=db.get(Site, slot.site_id), in_demand=key in vc.slot_map)


@router.post("/schedule/slot")
async def slot_save(request: Request, ctx: Ctx = Depends(need("schedule.write"))):
    f = await form(request, ctx)
    vid = i(f.get("version_id"), True)
    key = f.get("slot_key") or ""
    rev = i(f.get("revision"), True)
    action = f.get("action")
    url = f"/schedule/slot?v={vid}&key={key}"
    try:
        if action == "assign":
            sch.assign(ctx.db, ctx.cid, ctx.user.id, vid, key, i(f.get("employee_id"), True, "εργαζόμενος"), rev,
                       note=(f.get("note") or None))
        elif action == "clear":
            sch.assign(ctx.db, ctx.cid, ctx.user.id, vid, key, None, rev)
        elif action in ("lock", "unlock"):
            sch.set_lock(ctx.db, ctx.cid, ctx.user.id, vid, key, action == "lock", rev)
        else:
            raise BadInput("Άγνωστη ενέργεια")
    except sch.RuleViolation as exc:
        return back(url, err="Παραβίαση σκληρών κανόνων: " + "; ".join(sorted({x.label + (f" ({x.detail})" if x.detail else "") for x in exc.violations})))
    except sch.ConflictError as exc:
        return back(url, err=str(exc))
    return back(url, "Αποθηκεύτηκε")


@router.get("/schedule/generate")
def generate_page(request: Request, ctx: Ctx = Depends(need("schedule.write")), year: str = "", month: str = ""):
    y, m = ym(year, month)
    period = sch.get_period(ctx.db, ctx.cid, y, m)
    ctx.db.commit()
    sites = list(ctx.db.scalars(select(Site).where(Site.company_id == ctx.cid, Site.is_active.is_(True)).order_by(Site.name)))
    feas = reports.feasibility(ctx.db, ctx.cid, y, m)
    runs = list(ctx.db.scalars(select(GenerationRun).where(GenerationRun.period_id == period.id).order_by(GenerationRun.id.desc()).limit(10)))
    return render(request, ctx, "generate.html", y=y, m=m, period=period, sites=sites, weights=Weights(), units=Weights.UNITS,
                  feas=feas, runs=runs, draft=sch.version_with_status(ctx.db, period.id, "draft"),
                  published=sch.version_with_status(ctx.db, period.id, "published"))


@router.post("/schedule/generate")
async def generate_run(request: Request, ctx: Ctx = Depends(need("schedule.write"))):
    f = await form(request, ctx)
    y, m = ym(f.get("year"), f.get("month"))
    w = Weights(**{k: i(f.get(f"w_{k}"), True, k, 0, 10) for k in Weights.UNITS})
    site_ids = f.get("site_ids[]") or []
    site_ids = [site_ids] if isinstance(site_ids, str) else site_ids
    site_set = {get_owned(ctx.db, Site, x, ctx.cid).id for x in site_ids} or None
    d0, d1 = d(f.get("date_from")), d(f.get("date_to"))
    dates = None
    if d0 or d1:
        start, end = sch.month_range(y, m)
        d0, d1 = max(d0 or start, start), min(d1 or end, end)
        if d1 < d0:
            raise BadInput("Μη έγκυρο εύρος ημερομηνιών")
        dates = {d0 + timedelta(days=k) for k in range((d1 - d0).days + 1)}
    limit = i(f.get("time_limit"), True, "χρόνος", 5, 300)
    try:
        run = sch.generate(ctx.db, ctx.cid, ctx.user.id, y, m, site_ids=site_set, dates=dates, weights=w, time_limit_s=limit,
                           expected_period_revision=i(f.get("period_revision"), True))
    except sch.ConflictError as exc:
        return back(f"/schedule/generate?year={y}&month={m}", err=str(exc))
    return back(f"/schedule/runs/{run.id}")


@router.get("/schedule/runs/{rid}")
def run_page(rid: int, request: Request, ctx: Ctx = Depends(need("schedule.read"))):
    run = get_owned(ctx.db, GenerationRun, rid, ctx.cid)
    version = ctx.db.get(ScheduleVersion, run.version_id) if run.version_id else None
    period = ctx.db.get(sch.SchedulePeriod, run.period_id)
    pub = sch.version_with_status(ctx.db, period.id, "published")
    diff = sch.diff_versions(ctx.db, version, pub) if version else []
    return render(request, ctx, "run.html", run=run, rep=run.report, version=version, period=period, diff=diff, pub=pub,
                  VERSION_STATUS=VERSION_STATUS, units=Weights.UNITS)


@router.get("/schedule/versions/{vid}/diff")
def diff_page(vid: int, request: Request, ctx: Ctx = Depends(need("schedule.read"))):
    v = get_owned(ctx.db, ScheduleVersion, vid, ctx.cid)
    pub = sch.version_with_status(ctx.db, v.period_id, "published")
    against = ctx.db.get(ScheduleVersion, v.base_version_id) if v.status != "draft" and v.base_version_id else pub
    if against and against.id == v.id:
        against = ctx.db.get(ScheduleVersion, v.base_version_id) if v.base_version_id else None
    check = sch.validate_version(ctx.db, v)
    return render(request, ctx, "diff.html", v=v, against=against, diff=sch.diff_versions(ctx.db, v, against), check=check,
                  VERSION_STATUS=VERSION_STATUS)


@router.post("/schedule/versions/{vid}/publish")
async def publish(vid: int, request: Request, ctx: Ctx = Depends(need("schedule.publish"))):
    f = await form(request, ctx)
    v = get_owned(ctx.db, ScheduleVersion, vid, ctx.cid)
    p = v.period
    url = f"/schedule/versions/{vid}/diff"
    try:
        out = sch.publish(ctx.db, ctx.cid, ctx.user.id, vid, i(f.get("revision"), True), accept_gaps=f.get("accept_gaps") == "1")
    except sch.RuleViolation as exc:
        return back(url, err="Δεν επιτρέπεται δημοσίευση με παραβιάσεις σκληρών κανόνων: " + "; ".join(sorted({x.label for x in exc.violations})))
    except sch.ConflictError as exc:
        return back(url, err=str(exc))
    return back(f"/schedule?year={p.year}&month={p.month}&v={vid}",
                "Το πρόγραμμα δημοσιεύθηκε" + (f" με {out['uncovered']} ακάλυπτες θέσεις" if out["uncovered"] else ""))


@router.post("/schedule/versions/{vid}/discard")
async def discard(vid: int, request: Request, ctx: Ctx = Depends(need("schedule.write"))):
    f = await form(request, ctx)
    v = get_owned(ctx.db, ScheduleVersion, vid, ctx.cid)
    try:
        sch.discard(ctx.db, ctx.cid, ctx.user.id, vid, i(f.get("revision"), True))
    except sch.ConflictError as exc:
        return back(f"/schedule/versions/{vid}/diff", err=str(exc))
    return back(f"/schedule?year={v.period.year}&month={v.period.month}", "Το πρόχειρο απορρίφθηκε")


@router.get("/coverage")
def coverage_page(request: Request, ctx: Ctx = Depends(need("schedule.read")), year: str = "", month: str = "", v: str = ""):
    y, m = ym(year, month)
    version = get_owned(ctx.db, ScheduleVersion, v, ctx.cid) if v else None
    cov = reports.coverage(ctx.db, ctx.cid, y, m, version=version)
    explanations = {}
    if cov["version"] and cov["uncovered"]:
        check = sch.validate_version(ctx.db, cov["version"])
        explanations = sch.explain_gaps(ctx.db, cov["version"], check)
    reqs = {r.id: r for r in ctx.db.scalars(select(CoverageRequirement).where(CoverageRequirement.company_id == ctx.cid))}
    names = {e.id: e.full_name for e in ctx.db.scalars(select(Employee).where(Employee.company_id == ctx.cid))}
    by_date = defaultdict(float)
    for sl in cov["uncovered"]:
        by_date[sl.service_date] += sl.minutes / 60
    return render(request, ctx, "coverage.html", y=y, m=m, cov=cov, explanations=explanations, reqs=reqs, names=names,
                  by_date=sorted(by_date.items()), CAUSE_LABELS=CAUSE_LABELS, CAUSE_HINTS=CAUSE_HINTS, LABELS=LABELS,
                  VERSION_STATUS=VERSION_STATUS)


@router.get("/schedule/versions/{vid}/export.csv")
def export(vid: int, ctx: Ctx = Depends(need("reports.export"))):
    v = get_owned(ctx.db, ScheduleVersion, vid, ctx.cid)
    data = reports.export_schedule(ctx.db, ctx.cid, v)
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="schedule_{v.period.year}_{v.period.month:02d}_v{v.number}.csv"'})
