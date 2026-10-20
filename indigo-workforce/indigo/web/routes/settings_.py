import json
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from ...holidays import greek_holidays
from ...models import ROLES, AuditLog, Holiday, PayrollRuleSet, Qualification, User
from ...payroll_engine import DEFAULT_RULES, RULE_LABELS
from ...security import hash_password, revoke_all_for_user
from ...services import audit
from ...services.companies import create_user, seed_holidays
from ...services.scope import get_owned
from ..app import back, render
from ..deps import BadInput, Ctx, d, dec, form, i, need, s

router = APIRouter(prefix="/settings")


@router.get("")
def settings_page(request: Request, ctx: Ctx = Depends(need("settings.manage"))):
    db, cid = ctx.db, ctx.cid
    users = list(db.scalars(select(User).where(User.company_id == cid).order_by(User.full_name)))
    hols = list(db.scalars(select(Holiday).where(Holiday.company_id == cid).order_by(Holiday.date)))
    rulesets = list(db.scalars(select(PayrollRuleSet).where(PayrollRuleSet.company_id == cid).order_by(PayrollRuleSet.effective_from.desc())))
    quals = list(db.scalars(select(Qualification).where(Qualification.company_id == cid).order_by(Qualification.code)))
    return render(request, ctx, "settings.html", users=users, hols=hols, rulesets=rulesets, quals=quals,
                  RULE_LABELS=RULE_LABELS, DEFAULT_RULES=DEFAULT_RULES)


@router.post("/company")
async def company_save(request: Request, ctx: Ctx = Depends(need("settings.manage"))):
    f = await form(request, ctx)
    ctx.company.name = s(f.get("name"), 200, True, "επωνυμία")
    audit.log(ctx.db, ctx.cid, ctx.user.id, "company.update", "company", ctx.cid, fields=["name"])
    ctx.db.commit()
    return back("/settings", "Αποθηκεύτηκε")


@router.post("/users")
async def user_create(request: Request, ctx: Ctx = Depends(need("users.manage"))):
    f = await form(request, ctx)
    role = f.get("role")
    if role not in ROLES:
        raise BadInput("Μη έγκυρος ρόλος")
    try:
        create_user(ctx.db, ctx.cid, s(f.get("email"), 200, True, "email"), s(f.get("full_name"), 200, True, "ονοματεπώνυμο"),
                    role, f.get("password") or "", ctx.user.id)
    except ValueError as exc:
        ctx.db.rollback()
        return back("/settings", err=str(exc))
    ctx.db.commit()
    return back("/settings", "Ο χρήστης δημιουργήθηκε")


@router.post("/users/{uid}")
async def user_update(uid: int, request: Request, ctx: Ctx = Depends(need("users.manage"))):
    f = await form(request, ctx)
    u = get_owned(ctx.db, User, uid, ctx.cid)
    changed = []
    role = f.get("role")
    if role and role != u.role:
        if role not in ROLES:
            raise BadInput("Μη έγκυρος ρόλος")
        if u.id == ctx.user.id and role != "admin":
            return back("/settings", err="Δεν μπορείτε να αφαιρέσετε τον ρόλο διαχειριστή από τον εαυτό σας.")
        u.role = role
        changed.append("role")
    active = f.get("is_active") == "1"
    if active != u.is_active:
        if u.id == ctx.user.id:
            return back("/settings", err="Δεν μπορείτε να απενεργοποιήσετε τον εαυτό σας.")
        u.is_active = active
        changed.append("is_active")
    if f.get("password"):
        try:
            u.password_hash = hash_password(f["password"])
        except ValueError as exc:
            return back("/settings", err=str(exc))
        changed.append("password")
    if changed:
        if {"role", "is_active", "password"} & set(changed):
            revoke_all_for_user(ctx.db, u.id)
        audit.log(ctx.db, ctx.cid, ctx.user.id, "user.update", "user", u.id, fields=changed)
        ctx.db.commit()
    return back("/settings", "Αποθηκεύτηκε")


@router.post("/holidays")
async def holiday_add(request: Request, ctx: Ctx = Depends(need("settings.manage"))):
    f = await form(request, ctx)
    if f.get("seed_year"):
        n = seed_holidays(ctx.db, ctx.cid, [i(f["seed_year"], True, "έτος", 2000, 2099)])
        audit.log(ctx.db, ctx.cid, ctx.user.id, "holiday.seed", "holiday", None, year=f["seed_year"], added=n)
        ctx.db.commit()
        return back("/settings", f"Προστέθηκαν {n} αργίες")
    day = d(f.get("date"), True)
    if ctx.db.scalar(select(Holiday.id).where(Holiday.company_id == ctx.cid, Holiday.date == day)):
        return back("/settings", err="Υπάρχει ήδη αργία σε αυτή την ημερομηνία")
    h = Holiday(company_id=ctx.cid, date=day, name=s(f.get("name"), 200, True, "ονομασία"))
    ctx.db.add(h)
    audit.log(ctx.db, ctx.cid, ctx.user.id, "holiday.add", "holiday", None, date=day.isoformat())
    ctx.db.commit()
    return back("/settings", "Η αργία προστέθηκε")


@router.post("/holidays/{hid}/delete")
async def holiday_delete(hid: int, request: Request, ctx: Ctx = Depends(need("settings.manage"))):
    await form(request, ctx)
    h = get_owned(ctx.db, Holiday, hid, ctx.cid)
    audit.log(ctx.db, ctx.cid, ctx.user.id, "holiday.delete", "holiday", h.id, date=h.date.isoformat())
    ctx.db.delete(h)
    ctx.db.commit()
    return back("/settings", "Η αργία διαγράφηκε")


@router.post("/qualifications")
async def qual_add(request: Request, ctx: Ctx = Depends(need("settings.manage"))):
    f = await form(request, ctx)
    code = s(f.get("code"), 40, True, "κωδικός")
    if ctx.db.scalar(select(Qualification.id).where(Qualification.company_id == ctx.cid, Qualification.code == code)):
        return back("/settings", err="Ο κωδικός υπάρχει ήδη")
    ctx.db.add(Qualification(company_id=ctx.cid, code=code, name=s(f.get("name"), 200, True, "ονομασία")))
    audit.log(ctx.db, ctx.cid, ctx.user.id, "qualification.add", "qualification", None, code=code)
    ctx.db.commit()
    return back("/settings", "Το προσόν προστέθηκε")


@router.post("/rulesets")
async def ruleset_add(request: Request, ctx: Ctx = Depends(need("payroll.rules"))):
    f = await form(request, ctx)
    rules = {}
    for k in DEFAULT_RULES:
        rules[k] = str(dec(f.get(k), True, RULE_LABELS[k], lo=0, hi=1000))
    rs = PayrollRuleSet(company_id=ctx.cid, name=s(f.get("name"), 200, True, "ονομασία"), effective_from=d(f.get("effective_from"), True),
                        effective_to=d(f.get("effective_to")), rules=rules, verified=f.get("verified") == "1",
                        source_notes=s(f.get("source_notes"), 4000))
    if rs.verified and not rs.source_notes:
        return back("/settings", err="Για χαρακτηρισμό «επαληθευμένο» απαιτούνται πηγές/τεκμηρίωση.")
    ctx.db.add(rs)
    ctx.db.flush()
    audit.log(ctx.db, ctx.cid, ctx.user.id, "payroll.ruleset_add", "payroll_rule_set", rs.id, verified=rs.verified,
              effective_from=rs.effective_from.isoformat())
    ctx.db.commit()
    return back("/settings", "Οι παράμετροι αποθηκεύτηκαν")


@router.get("/audit")
def audit_page(request: Request, ctx: Ctx = Depends(need("audit.read")), page: int = 1, action: str = ""):
    q = select(AuditLog).where(AuditLog.company_id == ctx.cid)
    if action:
        q = q.where(AuditLog.action.like(f"{action[:40]}%"))
    rows = list(ctx.db.scalars(q.order_by(AuditLog.id.desc()).offset((max(page, 1) - 1) * 100).limit(100)))
    users = {u.id: u.full_name for u in ctx.db.scalars(select(User).where(User.company_id == ctx.cid))}
    return render(request, ctx, "audit.html", rows=rows, users=users, page=max(page, 1), action=action, json=json)
