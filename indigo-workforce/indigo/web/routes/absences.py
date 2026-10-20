from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from ...models import ABSENCE_KINDS, ABSENCE_STATUS, Absence, Assignment, Employee, ScheduleVersion
from ...services import audit, replacements as rep
from ...services.scope import get_owned
from ...services.scheduling import ConflictError, RuleViolation
from ..app import back, render
from ..deps import BadInput, Ctx, d, form, i, need, s

router = APIRouter(prefix="/absences")


@router.get("")
def list_(request: Request, ctx: Ctx = Depends(need("absences.read")), status: str = ""):
    q = select(Absence).where(Absence.company_id == ctx.cid)
    if status in ABSENCE_STATUS:
        q = q.where(Absence.status == status)
    rows = list(ctx.db.scalars(q.order_by(Absence.start_date.desc()).limit(300)))
    affected = {a.id: len(rep.affected_assignments(ctx.db, ctx.cid, a)) for a in rows if a.status == "approved"}
    emps = list(ctx.db.scalars(select(Employee).where(Employee.company_id == ctx.cid, Employee.is_active.is_(True)).order_by(Employee.last_name)))
    return render(request, ctx, "absences.html", rows=rows, affected=affected, emps=emps, status=status,
                  ABSENCE_KINDS=ABSENCE_KINDS, ABSENCE_STATUS=ABSENCE_STATUS)


@router.post("")
async def create(request: Request, ctx: Ctx = Depends(need("absences.write"))):
    f = await form(request, ctx)
    e = get_owned(ctx.db, Employee, f.get("employee_id"), ctx.cid)
    kind, st = f.get("kind"), f.get("status") or "approved"
    if kind not in ABSENCE_KINDS or st not in ("requested", "approved"):
        raise BadInput("Μη έγκυρος τύπος/κατάσταση")
    a = Absence(company_id=ctx.cid, employee_id=e.id, kind=kind, start_date=d(f.get("start_date"), True, "από"),
                end_date=d(f.get("end_date"), True, "έως"), status=st, notes=s(f.get("notes"), 1000), created_by=ctx.user.id)
    if a.end_date < a.start_date:
        raise BadInput("Η λήξη είναι πριν την έναρξη")
    ctx.db.add(a)
    ctx.db.flush()
    audit.log(ctx.db, ctx.cid, ctx.user.id, "absence.create", "absence", a.id, employee=e.id, kind=kind, status=st,
              start=a.start_date.isoformat(), end=a.end_date.isoformat())
    ctx.db.commit()
    n = len(rep.affected_assignments(ctx.db, ctx.cid, a)) if st == "approved" else 0
    if n:
        return back(f"/absences/{a.id}", f"Καταχωρίστηκε. Επηρεάζονται {n} βάρδιες — επιλέξτε αντικαταστάτες.")
    return back("/absences", "Καταχωρίστηκε")


@router.post("/{aid}/status")
async def set_status(aid: int, request: Request, ctx: Ctx = Depends(need("absences.write"))):
    f = await form(request, ctx)
    a = get_owned(ctx.db, Absence, aid, ctx.cid)
    st = f.get("status")
    if st not in ABSENCE_STATUS:
        raise BadInput("Μη έγκυρη κατάσταση")
    before = a.status
    a.status = st
    audit.log(ctx.db, ctx.cid, ctx.user.id, "absence.status", "absence", a.id, before=before, after=st)
    ctx.db.commit()
    if st == "approved" and rep.affected_assignments(ctx.db, ctx.cid, a):
        return back(f"/absences/{aid}", "Εγκρίθηκε· υπάρχουν επηρεαζόμενες βάρδιες.")
    return back("/absences", "Ενημερώθηκε")


@router.get("/{aid}")
def detail(aid: int, request: Request, ctx: Ctx = Depends(need("absences.read"))):
    a = get_owned(ctx.db, Absence, aid, ctx.cid)
    affected = rep.affected_assignments(ctx.db, ctx.cid, a) if a.status == "approved" else []
    versions = {v.id: v for v in ctx.db.scalars(select(ScheduleVersion).where(ScheduleVersion.id.in_({x.version_id for x in affected})))}
    return render(request, ctx, "absence_detail.html", a=a, affected=affected, versions=versions, ABSENCE_KINDS=ABSENCE_KINDS,
                  ABSENCE_STATUS=ABSENCE_STATUS)


@router.get("/{aid}/assignments/{asg_id}")
def candidates(aid: int, asg_id: int, request: Request, ctx: Ctx = Depends(need("absences.read"))):
    a = get_owned(ctx.db, Absence, aid, ctx.cid)
    asg = get_owned(ctx.db, Assignment, asg_id, ctx.cid)
    if asg.employee_id != a.employee_id:
        raise BadInput("Η βάρδια δεν αφορά αυτή την απουσία")
    c = rep.candidates(ctx.db, ctx.cid, asg.id)
    return render(request, ctx, "replacement.html", absence=a, asg=asg, c=c)


@router.post("/{aid}/assignments/{asg_id}")
async def apply(aid: int, asg_id: int, request: Request, ctx: Ctx = Depends(need("schedule.write"))):
    f = await form(request, ctx)
    a = get_owned(ctx.db, Absence, aid, ctx.cid)
    asg = get_owned(ctx.db, Assignment, asg_id, ctx.cid)
    if asg.employee_id != a.employee_id:
        raise BadInput("Η βάρδια δεν αφορά αυτή την απουσία")
    new_emp = i(f.get("employee_id"), label="εργαζόμενος") if f.get("employee_id") else None
    url = f"/absences/{aid}/assignments/{asg_id}"
    try:
        draft = rep.apply(ctx.db, ctx.cid, ctx.user.id, asg.id, new_emp, s(f.get("reason"), 500) or "")
    except RuleViolation as exc:
        return back(url, err="Παραβίαση σκληρών κανόνων: " + "; ".join(sorted({x.label for x in exc.violations})))
    except ConflictError as exc:
        return back(url, err=str(exc))
    return back(f"/schedule/versions/{draft.id}/diff",
                "Η αλλαγή καταχωρίστηκε σε πρόχειρο. Ελέγξτε και δημοσιεύστε για να ισχύσει.")
