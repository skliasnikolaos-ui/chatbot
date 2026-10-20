from datetime import datetime

from fastapi import APIRouter, Depends, Request, UploadFile
from sqlalchemy import select

from ...engine import demand
from ...models import HOLIDAY_MODES, Client, Contract, CoverageRequirement, Qualification, Site
from ...services import audit, importer, scheduling as sch
from ...services.scope import get_owned
from ...timeutil import ATHENS
from ..app import back, render
from ..deps import BadInput, Ctx, d, dec, form, i, need, s, t

router = APIRouter()


@router.get("/clients")
def clients(request: Request, ctx: Ctx = Depends(need("sites.read"))):
    db = ctx.db
    cl = list(db.scalars(select(Client).where(Client.company_id == ctx.cid).order_by(Client.name)))
    sites = list(db.scalars(select(Site).where(Site.company_id == ctx.cid).order_by(Site.name)))
    contracts = list(db.scalars(select(Contract).where(Contract.company_id == ctx.cid)))
    reqs = list(db.scalars(select(CoverageRequirement).where(CoverageRequirement.company_id == ctx.cid, CoverageRequirement.is_active.is_(True))))
    return render(request, ctx, "clients.html", clients=cl, sites=sites, contracts=contracts, reqs=reqs)


@router.post("/clients")
async def client_create(request: Request, ctx: Ctx = Depends(need("sites.write"))):
    f = await form(request, ctx)
    c = Client(company_id=ctx.cid, name=s(f.get("name"), 200, True, "επωνυμία"), vat_number=s(f.get("vat_number"), 20),
               contact=s(f.get("contact"), 200))
    ctx.db.add(c)
    ctx.db.flush()
    audit.log(ctx.db, ctx.cid, ctx.user.id, "client.create", "client", c.id)
    ctx.db.commit()
    return back("/clients", "Ο πελάτης δημιουργήθηκε")


@router.post("/clients/{cid_}/contracts")
async def contract_create(cid_: int, request: Request, ctx: Ctx = Depends(need("sites.write"))):
    f = await form(request, ctx)
    c = get_owned(ctx.db, Client, cid_, ctx.cid)
    k = Contract(company_id=ctx.cid, client_id=c.id, reference=s(f.get("reference"), 100, True, "αριθμός σύμβασης"),
                 start_date=d(f.get("start_date"), True, "έναρξη"), end_date=d(f.get("end_date")), notes=s(f.get("notes"), 2000))
    if k.end_date and k.end_date < k.start_date:
        raise BadInput("Η λήξη είναι πριν την έναρξη")
    ctx.db.add(k)
    ctx.db.flush()
    audit.log(ctx.db, ctx.cid, ctx.user.id, "contract.create", "contract", k.id)
    ctx.db.commit()
    return back("/clients", "Η σύμβαση δημιουργήθηκε")


@router.post("/contracts/{kid}")
async def contract_update(kid: int, request: Request, ctx: Ctx = Depends(need("sites.write"))):
    f = await form(request, ctx)
    k = get_owned(ctx.db, Contract, kid, ctx.cid)
    k.start_date = d(f.get("start_date"), True, "έναρξη")
    k.end_date = d(f.get("end_date"))
    if k.end_date and k.end_date < k.start_date:
        raise BadInput("Η λήξη είναι πριν την έναρξη")
    audit.log(ctx.db, ctx.cid, ctx.user.id, "contract.update", "contract", k.id, fields=["start_date", "end_date"])
    ctx.db.commit()
    return back("/clients", "Η σύμβαση ενημερώθηκε")


@router.post("/sites")
async def site_create(request: Request, ctx: Ctx = Depends(need("sites.write"))):
    f = await form(request, ctx)
    c = get_owned(ctx.db, Client, f.get("client_id"), ctx.cid)
    contract_id = None
    if f.get("contract_id"):
        k = get_owned(ctx.db, Contract, f.get("contract_id"), ctx.cid)
        if k.client_id != c.id:
            raise BadInput("Η σύμβαση δεν ανήκει στον πελάτη")
        contract_id = k.id
    site = Site(company_id=ctx.cid, client_id=c.id, contract_id=contract_id, name=s(f.get("name"), 200, True, "ονομασία"),
                address=s(f.get("address"), 300), requires_explicit_eligibility=f.get("requires_explicit_eligibility") == "1",
                restrictions=s(f.get("restrictions"), 2000))
    ctx.db.add(site)
    ctx.db.flush()
    audit.log(ctx.db, ctx.cid, ctx.user.id, "site.create", "site", site.id)
    ctx.db.commit()
    return back(f"/sites/{site.id}", "Το σημείο δημιουργήθηκε")


@router.get("/sites/import")
def import_page(request: Request, ctx: Ctx = Depends(need("import.run"))):
    return render(request, ctx, "import.html", kind="sites", columns=importer.SITE_COLUMNS, result=None)


@router.post("/sites/import")
async def import_run(request: Request, ctx: Ctx = Depends(need("import.run"))):
    f = await form(request, ctx)
    up: UploadFile | None = f.get("file") if hasattr(f.get("file"), "read") else None
    text = (await up.read()).decode("utf-8-sig") if up else (f.get("text") or "")
    if len(text) > 2_000_000:
        raise BadInput("Πολύ μεγάλο αρχείο")
    res = importer.import_sites(ctx.db, ctx.cid, ctx.user.id, text, commit=f.get("commit") == "1")
    return render(request, ctx, "import.html", kind="sites", columns=importer.SITE_COLUMNS, result=res, text=text,
                  committed=f.get("commit") == "1" and not res["errors"])


@router.get("/sites/{sid}")
def site_detail(sid: int, request: Request, ctx: Ctx = Depends(need("sites.read"))):
    db = ctx.db
    site = get_owned(db, Site, sid, ctx.cid)
    reqs = list(db.scalars(select(CoverageRequirement).where(CoverageRequirement.site_id == site.id).order_by(CoverageRequirement.is_active.desc(), CoverageRequirement.name)))
    quals = {q.id: q for q in db.scalars(select(Qualification).where(Qualification.company_id == ctx.cid))}
    contracts = list(db.scalars(select(Contract).where(Contract.client_id == site.client_id)))
    # preview of the shifts each requirement produces in the coming week
    today = datetime.now(ATHENS).date()
    from datetime import timedelta
    specs = [r for r in sch.requirement_specs(db, ctx.cid) if r.site_id == site.id]
    preview = demand.expand(specs, today, today + timedelta(days=6), sch.holidays_set(db, ctx.cid, today, today + timedelta(days=8)))
    return render(request, ctx, "site_detail.html", site=site, reqs=reqs, quals=quals, contracts=contracts,
                  HOLIDAY_MODES=HOLIDAY_MODES, preview=preview)


@router.post("/sites/{sid}")
async def site_update(sid: int, request: Request, ctx: Ctx = Depends(need("sites.write"))):
    f = await form(request, ctx)
    site = get_owned(ctx.db, Site, sid, ctx.cid)
    site.name = s(f.get("name"), 200, True, "ονομασία")
    site.address = s(f.get("address"), 300)
    site.requires_explicit_eligibility = f.get("requires_explicit_eligibility") == "1"
    site.restrictions = s(f.get("restrictions"), 2000)
    site.is_active = f.get("is_active", "1") == "1"
    if f.get("contract_id"):
        k = get_owned(ctx.db, Contract, f.get("contract_id"), ctx.cid)
        if k.client_id != site.client_id:
            raise BadInput("Η σύμβαση δεν ανήκει στον πελάτη")
        site.contract_id = k.id
    else:
        site.contract_id = None
    audit.log(ctx.db, ctx.cid, ctx.user.id, "site.update", "site", site.id)
    ctx.db.commit()
    return back(f"/sites/{sid}", "Αποθηκεύτηκε")


def _req_apply(r: CoverageRequirement, f: dict, ctx: Ctx) -> None:
    r.name = s(f.get("name"), 200, True, "ονομασία θέσης")
    days = f.get("weekdays[]") or []
    days = [days] if isinstance(days, str) else days
    r.weekdays = ",".join(str(i(x, True, "ημέρα", 0, 6)) for x in sorted(set(days)))
    if not r.weekdays:
        raise BadInput("Επιλέξτε τουλάχιστον μία ημέρα")
    r.start_time = t(f.get("start_time"), "έναρξη")
    r.end_time = t(f.get("end_time"), "λήξη")
    r.shift_hours = dec(f.get("shift_hours"), label="διάρκεια βάρδιας", lo=1, hi=24)
    r.headcount = i(f.get("headcount"), True, "άτομα", 1, 50)
    hm = f.get("holiday_mode") or "normal"
    if hm not in HOLIDAY_MODES:
        raise BadInput("Μη έγκυρη ρύθμιση αργιών")
    r.holiday_mode = hm
    r.valid_from, r.valid_to = d(f.get("valid_from")), d(f.get("valid_to"))
    if r.valid_from and r.valid_to and r.valid_to < r.valid_from:
        raise BadInput("Η λήξη είναι πριν την έναρξη")
    r.billing_rate_hourly = dec(f.get("billing_rate_hourly"), label="τιμή χρέωσης", hi=1000)
    q = f.get("qualification_ids[]") or []
    q = [q] if isinstance(q, str) else q
    r.qualification_ids = sorted({get_owned(ctx.db, Qualification, x, ctx.cid).id for x in q})
    r.notes = s(f.get("notes"), 2000)
    r.is_active = f.get("is_active", "1") == "1"


@router.post("/sites/{sid}/requirements")
async def req_create(sid: int, request: Request, ctx: Ctx = Depends(need("sites.write"))):
    f = await form(request, ctx)
    site = get_owned(ctx.db, Site, sid, ctx.cid)
    r = CoverageRequirement(company_id=ctx.cid, site_id=site.id)
    _req_apply(r, f, ctx)
    ctx.db.add(r)
    ctx.db.flush()
    audit.log(ctx.db, ctx.cid, ctx.user.id, "requirement.create", "coverage_requirement", r.id, site=site.id)
    ctx.db.commit()
    return back(f"/sites/{sid}", "Η απαίτηση κάλυψης προστέθηκε")


@router.post("/requirements/{rid}")
async def req_update(rid: int, request: Request, ctx: Ctx = Depends(need("sites.write"))):
    f = await form(request, ctx)
    r = get_owned(ctx.db, CoverageRequirement, rid, ctx.cid)
    _req_apply(r, f, ctx)
    audit.log(ctx.db, ctx.cid, ctx.user.id, "requirement.update", "coverage_requirement", r.id)
    ctx.db.commit()
    return back(f"/sites/{r.site_id}", "Η απαίτηση ενημερώθηκε· ελέγξτε τα υπάρχοντα προγράμματα για ασυμφωνίες.")
