from datetime import timedelta

from fastapi import APIRouter, Depends, Request, UploadFile
from sqlalchemy import or_, select

from ...models import (ABSENCE_KINDS, ABSENCE_STATUS, Absence, CompensationTerm, CoverageRequirement, Employee,
                       EmployeeQualification, EmployeeSiteRule, FixedPost, Qualification, Site, WeeklyUnavailability)
from ...services import audit, importer
from ...services.scope import get_owned
from ..app import back, render
from ..deps import BadInput, Ctx, d, dec, form, i, need, s, t

router = APIRouter(prefix="/employees")

EMP_FIELDS = ["code", "first_name", "last_name", "phone", "email", "hire_date", "termination_date", "contract_type",
              "weekly_contract_hours", "max_hours_week", "max_hours_month", "max_shift_hours", "min_rest_hours",
              "max_consecutive_days", "is_relief", "night_preference", "avoid_weekends", "is_active", "notes"]


def _apply(e: Employee, f: dict) -> None:
    e.code = s(f.get("code"), 40, True, "κωδικός")
    e.first_name = s(f.get("first_name"), 100, True, "όνομα")
    e.last_name = s(f.get("last_name"), 100, True, "επώνυμο")
    e.phone = s(f.get("phone"), 40)
    e.email = s(f.get("email"), 200)
    e.hire_date = d(f.get("hire_date"))
    e.termination_date = d(f.get("termination_date"))
    if e.hire_date and e.termination_date and e.termination_date < e.hire_date:
        raise BadInput("Η λήξη απασχόλησης είναι πριν την πρόσληψη")
    ct = f.get("contract_type") or "full_time"
    if ct not in ("full_time", "part_time", "fixed_term"):
        raise BadInput("Μη έγκυρος τύπος σύμβασης")
    e.contract_type = ct
    e.weekly_contract_hours = dec(f.get("weekly_contract_hours"), True, "συμβατικές ώρες", hi=60)
    e.max_hours_week = dec(f.get("max_hours_week"), label="μέγιστες ώρες εβδομάδας", hi=80)
    e.max_hours_month = dec(f.get("max_hours_month"), label="μέγιστες ώρες μήνα", hi=320)
    e.max_shift_hours = dec(f.get("max_shift_hours"), label="μέγιστη βάρδια", hi=24)
    e.min_rest_hours = dec(f.get("min_rest_hours"), True, "ελάχιστη ανάπαυση", hi=48)
    e.max_consecutive_days = i(f.get("max_consecutive_days"), label="συνεχόμενες ημέρες", lo=1, hi=31)
    e.is_relief = f.get("is_relief") == "1"
    np_ = f.get("night_preference") or "neutral"
    if np_ not in ("avoid", "neutral", "prefer"):
        raise BadInput("Μη έγκυρη προτίμηση")
    e.night_preference = np_
    e.avoid_weekends = f.get("avoid_weekends") == "1"
    e.is_active = f.get("is_active", "1") == "1"
    e.notes = s(f.get("notes"), 2000)


@router.get("")
def list_(request: Request, ctx: Ctx = Depends(need("employees.read")), q: str = "", show: str = "active"):
    stmt = select(Employee).where(Employee.company_id == ctx.cid)
    if show == "active":
        stmt = stmt.where(Employee.is_active.is_(True))
    if q:
        like = f"%{q[:50]}%"
        stmt = stmt.where(or_(Employee.last_name.ilike(like), Employee.first_name.ilike(like), Employee.code.ilike(like)))
    emps = list(ctx.db.scalars(stmt.order_by(Employee.last_name, Employee.first_name)))
    quals = {}
    for eq in ctx.db.scalars(select(EmployeeQualification).where(EmployeeQualification.company_id == ctx.cid)):
        quals.setdefault(eq.employee_id, []).append(eq)
    return render(request, ctx, "employees.html", emps=emps, quals=quals, q=q, show=show)


@router.get("/new")
def new(request: Request, ctx: Ctx = Depends(need("employees.write"))):
    return render(request, ctx, "employee_form.html", e=None)


@router.post("/new")
async def create(request: Request, ctx: Ctx = Depends(need("employees.write"))):
    f = await form(request, ctx)
    e = Employee(company_id=ctx.cid)
    _apply(e, f)
    if ctx.db.scalar(select(Employee.id).where(Employee.company_id == ctx.cid, Employee.code == e.code)):
        return back("/employees/new", err="Ο κωδικός υπάρχει ήδη")
    ctx.db.add(e)
    ctx.db.flush()
    audit.log(ctx.db, ctx.cid, ctx.user.id, "employee.create", "employee", e.id)
    ctx.db.commit()
    return back(f"/employees/{e.id}", "Ο εργαζόμενος δημιουργήθηκε")


@router.get("/import")
def import_page(request: Request, ctx: Ctx = Depends(need("import.run"))):
    return render(request, ctx, "import.html", kind="employees", columns=importer.EMPLOYEE_COLUMNS, result=None)


@router.post("/import")
async def import_run(request: Request, ctx: Ctx = Depends(need("import.run"))):
    f = await form(request, ctx)
    up: UploadFile | None = f.get("file") if hasattr(f.get("file"), "read") else None
    text = (await up.read()).decode("utf-8-sig") if up else (f.get("text") or "")
    if len(text) > 2_000_000:
        raise BadInput("Πολύ μεγάλο αρχείο")
    res = importer.import_employees(ctx.db, ctx.cid, ctx.user.id, text, commit=f.get("commit") == "1")
    return render(request, ctx, "import.html", kind="employees", columns=importer.EMPLOYEE_COLUMNS, result=res,
                  text=text, committed=f.get("commit") == "1" and not res["errors"])


@router.get("/{eid}")
def detail(eid: int, request: Request, ctx: Ctx = Depends(need("employees.read"))):
    db = ctx.db
    e = get_owned(db, Employee, eid, ctx.cid)
    data = {
        "e": e,
        "quals": list(db.scalars(select(EmployeeQualification).where(EmployeeQualification.employee_id == e.id))),
        "all_quals": list(db.scalars(select(Qualification).where(Qualification.company_id == ctx.cid).order_by(Qualification.code))),
        "weekly": list(db.scalars(select(WeeklyUnavailability).where(WeeklyUnavailability.employee_id == e.id).order_by(WeeklyUnavailability.weekday))),
        "site_rules": list(db.scalars(select(EmployeeSiteRule).where(EmployeeSiteRule.employee_id == e.id))),
        "sites": list(db.scalars(select(Site).where(Site.company_id == ctx.cid, Site.is_active.is_(True)).order_by(Site.name))),
        "fixed": list(db.scalars(select(FixedPost).where(FixedPost.employee_id == e.id))),
        "reqs": list(db.scalars(select(CoverageRequirement).where(CoverageRequirement.company_id == ctx.cid, CoverageRequirement.is_active.is_(True)))),
        "absences": list(db.scalars(select(Absence).where(Absence.employee_id == e.id).order_by(Absence.start_date.desc()).limit(30))),
        "comp": list(db.scalars(select(CompensationTerm).where(CompensationTerm.employee_id == e.id).order_by(CompensationTerm.effective_from.desc())))
        if ctx.can("compensation.read") else None,
        "ABSENCE_KINDS": ABSENCE_KINDS, "ABSENCE_STATUS": ABSENCE_STATUS,
    }
    return render(request, ctx, "employee_detail.html", **data)


@router.post("/{eid}")
async def update(eid: int, request: Request, ctx: Ctx = Depends(need("employees.write"))):
    f = await form(request, ctx)
    e = get_owned(ctx.db, Employee, eid, ctx.cid)
    before = {k: getattr(e, k) for k in EMP_FIELDS}
    _apply(e, f)
    dup = ctx.db.scalar(select(Employee.id).where(Employee.company_id == ctx.cid, Employee.code == e.code, Employee.id != e.id))
    if dup:
        ctx.db.rollback()
        return back(f"/employees/{eid}", err="Ο κωδικός υπάρχει ήδη")
    changed = [k for k in EMP_FIELDS if getattr(e, k) != before[k]]
    audit.log(ctx.db, ctx.cid, ctx.user.id, "employee.update", "employee", e.id, fields=changed)
    ctx.db.commit()
    return back(f"/employees/{eid}", "Αποθηκεύτηκε")


@router.post("/{eid}/qualifications")
async def qual_add(eid: int, request: Request, ctx: Ctx = Depends(need("employees.write"))):
    f = await form(request, ctx)
    e = get_owned(ctx.db, Employee, eid, ctx.cid)
    q = get_owned(ctx.db, Qualification, f.get("qualification_id"), ctx.cid)
    vf, vu = d(f.get("valid_from")), d(f.get("valid_until"))
    if vf and vu and vu < vf:
        raise BadInput("Η λήξη είναι πριν την έναρξη ισχύος")
    eq = EmployeeQualification(company_id=ctx.cid, employee_id=e.id, qualification_id=q.id,
                               licence_number=s(f.get("licence_number"), 80), valid_from=vf, valid_until=vu)
    ctx.db.add(eq)
    ctx.db.flush()
    audit.log(ctx.db, ctx.cid, ctx.user.id, "employee.qualification_add", "employee", e.id, qualification=q.code,
              valid_until=vu.isoformat() if vu else None)
    ctx.db.commit()
    return back(f"/employees/{eid}", "Το προσόν προστέθηκε")


@router.post("/{eid}/qualifications/{qid}/delete")
async def qual_del(eid: int, qid: int, request: Request, ctx: Ctx = Depends(need("employees.write"))):
    await form(request, ctx)
    eq = get_owned(ctx.db, EmployeeQualification, qid, ctx.cid)
    if eq.employee_id != eid:
        raise BadInput("Μη έγκυρη αίτηση")
    audit.log(ctx.db, ctx.cid, ctx.user.id, "employee.qualification_delete", "employee", eid, qualification_id=eq.qualification_id)
    ctx.db.delete(eq)
    ctx.db.commit()
    return back(f"/employees/{eid}", "Διαγράφηκε")


@router.post("/{eid}/unavailability")
async def unav_add(eid: int, request: Request, ctx: Ctx = Depends(need("employees.write"))):
    f = await form(request, ctx)
    e = get_owned(ctx.db, Employee, eid, ctx.cid)
    w = WeeklyUnavailability(company_id=ctx.cid, employee_id=e.id, weekday=i(f.get("weekday"), True, "ημέρα", 0, 6),
                             start_time=t(f.get("start_time")), end_time=t(f.get("end_time")))
    ctx.db.add(w)
    audit.log(ctx.db, ctx.cid, ctx.user.id, "employee.unavailability_add", "employee", e.id, weekday=w.weekday)
    ctx.db.commit()
    return back(f"/employees/{eid}", "Προστέθηκε")


@router.post("/{eid}/unavailability/{wid}/delete")
async def unav_del(eid: int, wid: int, request: Request, ctx: Ctx = Depends(need("employees.write"))):
    await form(request, ctx)
    w = get_owned(ctx.db, WeeklyUnavailability, wid, ctx.cid)
    if w.employee_id != eid:
        raise BadInput("Μη έγκυρη αίτηση")
    ctx.db.delete(w)
    audit.log(ctx.db, ctx.cid, ctx.user.id, "employee.unavailability_delete", "employee", eid)
    ctx.db.commit()
    return back(f"/employees/{eid}", "Διαγράφηκε")


@router.post("/{eid}/site-rules")
async def rule_add(eid: int, request: Request, ctx: Ctx = Depends(need("employees.write"))):
    f = await form(request, ctx)
    e = get_owned(ctx.db, Employee, eid, ctx.cid)
    site = get_owned(ctx.db, Site, f.get("site_id"), ctx.cid)
    rule = f.get("rule")
    if rule not in ("eligible", "prohibited", "preferred"):
        raise BadInput("Μη έγκυρος κανόνας")
    r = ctx.db.scalar(select(EmployeeSiteRule).where(EmployeeSiteRule.employee_id == e.id, EmployeeSiteRule.site_id == site.id))
    if r is None:
        ctx.db.add(EmployeeSiteRule(company_id=ctx.cid, employee_id=e.id, site_id=site.id, rule=rule))
    else:
        r.rule = rule
    audit.log(ctx.db, ctx.cid, ctx.user.id, "employee.site_rule", "employee", e.id, site=site.id, rule=rule)
    ctx.db.commit()
    return back(f"/employees/{eid}", "Αποθηκεύτηκε")


@router.post("/{eid}/site-rules/{rid}/delete")
async def rule_del(eid: int, rid: int, request: Request, ctx: Ctx = Depends(need("employees.write"))):
    await form(request, ctx)
    r = get_owned(ctx.db, EmployeeSiteRule, rid, ctx.cid)
    if r.employee_id != eid:
        raise BadInput("Μη έγκυρη αίτηση")
    ctx.db.delete(r)
    audit.log(ctx.db, ctx.cid, ctx.user.id, "employee.site_rule_delete", "employee", eid, site=r.site_id)
    ctx.db.commit()
    return back(f"/employees/{eid}", "Διαγράφηκε")


@router.post("/{eid}/fixed-posts")
async def fixed_add(eid: int, request: Request, ctx: Ctx = Depends(need("employees.write"))):
    f = await form(request, ctx)
    e = get_owned(ctx.db, Employee, eid, ctx.cid)
    req = get_owned(ctx.db, CoverageRequirement, f.get("requirement_id"), ctx.cid)
    days = f.get("weekdays[]") or []
    days = [days] if isinstance(days, str) else days
    wd = ",".join(str(i(x, True, "ημέρα", 0, 6)) for x in days)
    if not wd:
        raise BadInput("Επιλέξτε ημέρες")
    ctx.db.add(FixedPost(company_id=ctx.cid, employee_id=e.id, requirement_id=req.id, weekdays=wd))
    audit.log(ctx.db, ctx.cid, ctx.user.id, "employee.fixed_post_add", "employee", e.id, requirement=req.id)
    ctx.db.commit()
    return back(f"/employees/{eid}", "Προστέθηκε σταθερή θέση")


@router.post("/{eid}/fixed-posts/{fid}/delete")
async def fixed_del(eid: int, fid: int, request: Request, ctx: Ctx = Depends(need("employees.write"))):
    await form(request, ctx)
    fp = get_owned(ctx.db, FixedPost, fid, ctx.cid)
    if fp.employee_id != eid:
        raise BadInput("Μη έγκυρη αίτηση")
    ctx.db.delete(fp)
    audit.log(ctx.db, ctx.cid, ctx.user.id, "employee.fixed_post_delete", "employee", eid)
    ctx.db.commit()
    return back(f"/employees/{eid}", "Διαγράφηκε")


@router.post("/{eid}/compensation")
async def comp_add(eid: int, request: Request, ctx: Ctx = Depends(need("compensation.write"))):
    f = await form(request, ctx)
    e = get_owned(ctx.db, Employee, eid, ctx.cid)
    basis = f.get("pay_basis")
    if basis not in ("hourly", "monthly"):
        raise BadInput("Μη έγκυρη βάση αμοιβής")
    eff = d(f.get("effective_from"), True, "ισχύει από")
    term = CompensationTerm(
        company_id=ctx.cid, employee_id=e.id, effective_from=eff, effective_to=d(f.get("effective_to")), pay_basis=basis,
        hourly_rate=dec(f.get("hourly_rate"), basis == "hourly", "ωρομίσθιο", hi=1000),
        monthly_salary=dec(f.get("monthly_salary"), basis == "monthly", "μηνιαίος μισθός", hi=100000),
        night_premium_pct=dec(f.get("night_premium_pct"), label="νυχτερινή %", hi=500),
        sunday_holiday_premium_pct=dec(f.get("sunday_holiday_premium_pct"), label="Κυριακή/αργία %", hi=500),
        overwork_premium_pct=dec(f.get("overwork_premium_pct"), label="υπερεργασία %", hi=500),
        overtime_premium_pct=dec(f.get("overtime_premium_pct"), label="υπερωρία %", hi=500),
        notes=s(f.get("notes"), 1000))
    if term.effective_to and term.effective_to < eff:
        raise BadInput("Η λήξη είναι πριν την έναρξη ισχύος")
    closed = []
    for old in ctx.db.scalars(select(CompensationTerm).where(CompensationTerm.employee_id == e.id,
                                                             CompensationTerm.effective_from < eff,
                                                             (CompensationTerm.effective_to.is_(None)) | (CompensationTerm.effective_to >= eff))):
        old.effective_to = eff - timedelta(days=1)  # explicit, audited close of the previous term
        closed.append(old.id)
    clash = ctx.db.scalar(select(CompensationTerm.id).where(CompensationTerm.employee_id == e.id, CompensationTerm.effective_from == eff))
    if clash:
        ctx.db.rollback()
        return back(f"/employees/{eid}", err="Υπάρχουν ήδη όροι με την ίδια ημερομηνία έναρξης")
    ctx.db.add(term)
    ctx.db.flush()
    audit.log(ctx.db, ctx.cid, ctx.user.id, "compensation.add", "employee", e.id, term=term.id, closed_terms=closed,
              effective_from=eff.isoformat())
    ctx.db.commit()
    return back(f"/employees/{eid}", "Οι όροι αμοιβής αποθηκεύτηκαν")
