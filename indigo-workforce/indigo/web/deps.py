from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import db as dbm
from ..authz import allowed
from ..models import Company, User, UserSession
from ..security import csrf_ok, lookup_session
from ..timeutil import ATHENS

SESSION_COOKIE = "indigo_session"


class LoginRequired(Exception):
    pass


def get_db():
    s = dbm.get_session()
    try:
        yield s
    finally:
        s.close()


@dataclass
class Ctx:
    user: User
    company: Company
    session: UserSession
    db: Session

    @property
    def cid(self) -> int:
        return self.company.id

    def can(self, perm: str) -> bool:
        return allowed(self.user.role, perm)

    def require(self, perm: str) -> None:
        if not self.can(perm):
            raise HTTPException(status_code=403, detail="Δεν έχετε δικαίωμα για αυτή την ενέργεια.")


def auth(request: Request, db: Session = Depends(get_db)) -> Ctx:
    found = lookup_session(db, request.cookies.get(SESSION_COOKIE))
    if not found:
        raise LoginRequired()
    user, sess = found
    return Ctx(user=user, company=db.get(Company, user.company_id), session=sess, db=db)


def need(perm: str):
    def dep(ctx: Ctx = Depends(auth)) -> Ctx:
        ctx.require(perm)
        return ctx
    return dep


async def form(request: Request, ctx: Ctx) -> dict:
    """Parse a POST form and verify its CSRF token."""
    f = await request.form()
    if not csrf_ok(ctx.session, f.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Μη έγκυρο διακριτικό ασφαλείας (CSRF). Ανανεώστε τη σελίδα.")
    out: dict = {}
    for k in f.keys():
        vals = f.getlist(k)
        out[k] = vals if len(vals) > 1 or k.endswith("[]") else vals[0]
    return out


# ---------------------------------------------------------------- input parsing
class BadInput(ValueError):
    pass


def s(v, maxlen: int = 300, required: bool = False, label: str = "πεδίο") -> str | None:
    v = (v or "").strip() if isinstance(v, str) else v
    if not v:
        if required:
            raise BadInput(f"Υποχρεωτικό: {label}")
        return None
    if len(v) > maxlen:
        raise BadInput(f"Πολύ μεγάλο κείμενο: {label}")
    return v


def d(v, required: bool = False, label: str = "ημερομηνία") -> date | None:
    if not v:
        if required:
            raise BadInput(f"Υποχρεωτικό: {label}")
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise BadInput(f"Μη έγκυρη ημερομηνία: {label}")


def t(v, label: str = "ώρα") -> time:
    try:
        return time.fromisoformat(v)
    except (TypeError, ValueError):
        raise BadInput(f"Μη έγκυρη ώρα: {label}")


def dec(v, required: bool = False, label: str = "αριθμός", lo: Decimal | None = Decimal(0), hi: Decimal | None = None) -> Decimal | None:
    if v in (None, ""):
        if required:
            raise BadInput(f"Υποχρεωτικό: {label}")
        return None
    try:
        x = Decimal(str(v).replace(",", "."))
    except InvalidOperation:
        raise BadInput(f"Μη έγκυρος αριθμός: {label}")
    if not x.is_finite() or (lo is not None and x < lo) or (hi is not None and x > hi):
        raise BadInput(f"Τιμή εκτός ορίων: {label}")
    return x


def i(v, required: bool = False, label: str = "αριθμός", lo: int | None = None, hi: int | None = None) -> int | None:
    if v in (None, ""):
        if required:
            raise BadInput(f"Υποχρεωτικό: {label}")
        return None
    try:
        x = int(v)
    except (TypeError, ValueError):
        raise BadInput(f"Μη έγκυρος ακέραιος: {label}")
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        raise BadInput(f"Τιμή εκτός ορίων: {label}")
    return x


def local_dt(v, label: str = "ημερομηνία/ώρα") -> datetime:
    """<input type=datetime-local> value interpreted in Europe/Athens → aware UTC."""
    try:
        naive = datetime.fromisoformat(v)
    except (TypeError, ValueError):
        raise BadInput(f"Μη έγκυρη ημερομηνία/ώρα: {label}")
    from ..timeutil import local_to_utc
    return local_to_utc(naive.date(), naive.time())


def ym(year, month) -> tuple[int, int]:
    today = datetime.now(ATHENS).date()
    y = i(year, label="έτος", lo=2000, hi=2100) or today.year
    m = i(month, label="μήνας", lo=1, hi=12) or today.month
    return y, m
