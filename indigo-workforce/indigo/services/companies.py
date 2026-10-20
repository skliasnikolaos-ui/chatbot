from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..holidays import greek_holidays
from ..models import ROLES, Company, Holiday, PayrollRuleSet, User
from ..payroll_engine import DEFAULT_RULES
from ..security import hash_password
from . import audit


def seed_holidays(db: Session, company_id: int, years: list[int]) -> int:
    n = 0
    for y in years:
        for d, name in greek_holidays(y):
            if db.scalar(select(Holiday.id).where(Holiday.company_id == company_id, Holiday.date == d)) is None:
                db.add(Holiday(company_id=company_id, date=d, name=name))
                n += 1
    return n


def create_company(db: Session, name: str, *, is_demo: bool = False, years: list[int] | None = None) -> Company:
    c = Company(name=name, is_demo=is_demo, settings={})
    db.add(c)
    db.flush()
    years = years or [date.today().year, date.today().year + 1]
    seed_holidays(db, c.id, years)
    db.add(PayrollRuleSet(company_id=c.id, name="Προεπιλεγμένοι παράμετροι (ΜΗ ΕΠΑΛΗΘΕΥΜΕΝΟΙ)", effective_from=date(2000, 1, 1),
                          rules=dict(DEFAULT_RULES), verified=False,
                          source_notes="Ενδεικτικές τιμές. Πρέπει να επιβεβαιωθούν από λογιστή/ισχύουσα νομοθεσία πριν από χρήση."))
    audit.log(db, c.id, None, "company.create", "company", c.id, demo=is_demo)
    return c


def create_user(db: Session, company_id: int, email: str, full_name: str, role: str, password: str, actor_id: int | None = None) -> User:
    if role not in ROLES:
        raise ValueError("Μη έγκυρος ρόλος")
    email = email.strip().lower()
    if "@" not in email:
        raise ValueError("Μη έγκυρο email")
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise ValueError("Το email χρησιμοποιείται ήδη")
    u = User(company_id=company_id, email=email, full_name=full_name.strip(), role=role, password_hash=hash_password(password))
    db.add(u)
    db.flush()
    audit.log(db, company_id, actor_id, "user.create", "user", u.id, role=role)
    return u
