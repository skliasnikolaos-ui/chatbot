"""CSV import of employees and sites/coverage requirements (pilot onboarding).

All-or-nothing: rows are validated first; nothing is written if any row has
an error. `commit=False` gives a dry-run preview.
Delimiter: ';' (Greek Excel default) or ','. Dates: YYYY-MM-DD or DD/MM/YYYY.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (Client, CompensationTerm, Contract, CoverageRequirement, Employee, EmployeeQualification,
                      Qualification, Site)
from . import audit

EMPLOYEE_COLUMNS = ["code", "last_name", "first_name", "phone", "email", "hire_date", "contract_type", "weekly_hours",
                    "max_hours_week", "is_relief", "qualifications", "pay_basis", "hourly_rate", "monthly_salary"]
SITE_COLUMNS = ["client", "site", "address", "requires_eligibility", "requirement", "weekdays", "start", "end", "shift_hours",
                "headcount", "qualifications", "holiday_mode", "billing_rate", "contract_ref", "contract_start", "contract_end"]


class ImportError_(Exception):
    pass


def _rows(text: str) -> list[dict]:
    text = text.lstrip("﻿")
    sample = text.splitlines()[0] if text else ""
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(io.StringIO(text), delimiter=delim)]


def _date(v: str) -> date | None:
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"μη έγκυρη ημερομηνία «{v}»")


def _dec(v: str) -> Decimal | None:
    if not v:
        return None
    try:
        d = Decimal(v.replace(",", "."))
    except InvalidOperation:
        raise ValueError(f"μη έγκυρος αριθμός «{v}»")
    if d < 0:
        raise ValueError(f"αρνητική τιμή «{v}»")
    return d


def _time(v: str) -> time:
    try:
        return datetime.strptime(v, "%H:%M").time()
    except ValueError:
        raise ValueError(f"μη έγκυρη ώρα «{v}» (ΩΩ:ΛΛ)")


def _bool(v: str) -> bool:
    return v.lower() in ("1", "ναι", "yes", "true", "x", "ν")


def _quals(db: Session, company_id: int, spec: str) -> list[tuple[Qualification, date | None]]:
    """Resolve 'CODE[:YYYY-MM-DD]|CODE…', creating unknown qualification codes."""
    out = []
    for part in [p.strip() for p in spec.split("|") if p.strip()]:
        code, _, until = part.partition(":")
        q = db.scalar(select(Qualification).where(Qualification.company_id == company_id, Qualification.code == code.strip()))
        if q is None:
            q = Qualification(company_id=company_id, code=code.strip(), name=code.strip())
            db.add(q)
            db.flush()
        out.append((q, _date(until.strip()) if until else None))
    return out


def import_employees(db: Session, company_id: int, user_id: int, text: str, commit: bool) -> dict:
    rows = _rows(text)
    errors, parsed = [], []
    missing = [c for c in ("code", "last_name", "first_name") if rows and c not in rows[0]]
    if missing:
        return {"errors": [f"Λείπουν στήλες: {', '.join(missing)}"], "created": 0, "updated": 0, "rows": 0}
    seen = set()
    for i, r in enumerate(rows, start=2):
        try:
            if not r["code"] or not r["last_name"] or not r["first_name"]:
                raise ValueError("κωδικός, επώνυμο και όνομα είναι υποχρεωτικά")
            if r["code"] in seen:
                raise ValueError("διπλός κωδικός στο αρχείο")
            seen.add(r["code"])
            ct = r.get("contract_type") or "full_time"
            if ct not in ("full_time", "part_time", "fixed_term"):
                raise ValueError("contract_type: full_time | part_time | fixed_term")
            pb = r.get("pay_basis") or ""
            if pb and pb not in ("hourly", "monthly"):
                raise ValueError("pay_basis: hourly | monthly")
            item = {"code": r["code"], "last_name": r["last_name"], "first_name": r["first_name"],
                    "phone": r.get("phone") or None, "email": r.get("email") or None, "hire_date": _date(r.get("hire_date", "")),
                    "contract_type": ct, "weekly_hours": _dec(r.get("weekly_hours", "")) or Decimal(40),
                    "max_hours_week": _dec(r.get("max_hours_week", "")) or Decimal(48), "is_relief": _bool(r.get("is_relief", "")),
                    "quals": r.get("qualifications", ""), "pay_basis": pb, "hourly_rate": _dec(r.get("hourly_rate", "")),
                    "monthly_salary": _dec(r.get("monthly_salary", ""))}
            for q in item["quals"].split("|"):
                if ":" in q:
                    _date(q.split(":", 1)[1].strip())
            if pb == "hourly" and not item["hourly_rate"]:
                raise ValueError("hourly_rate απαιτείται για pay_basis=hourly")
            if pb == "monthly" and not item["monthly_salary"]:
                raise ValueError("monthly_salary απαιτείται για pay_basis=monthly")
            parsed.append(item)
        except (ValueError, KeyError) as exc:
            errors.append(f"Γραμμή {i}: {exc}")
    result = {"errors": errors, "rows": len(rows), "created": 0, "updated": 0}
    if errors or not commit:
        for it in parsed:
            exists = db.scalar(select(Employee.id).where(Employee.company_id == company_id, Employee.code == it["code"]))
            result["updated" if exists else "created"] += 1
        return result
    for it in parsed:
        e = db.scalar(select(Employee).where(Employee.company_id == company_id, Employee.code == it["code"]))
        if e is None:
            e = Employee(company_id=company_id, code=it["code"])
            db.add(e)
            result["created"] += 1
        else:
            result["updated"] += 1
        e.last_name, e.first_name, e.phone, e.email = it["last_name"], it["first_name"], it["phone"], it["email"]
        e.hire_date, e.contract_type, e.weekly_contract_hours = it["hire_date"], it["contract_type"], it["weekly_hours"]
        e.max_hours_week, e.is_relief = it["max_hours_week"], it["is_relief"]
        db.flush()
        for q, until in _quals(db, company_id, it["quals"]):
            eq = db.scalar(select(EmployeeQualification).where(EmployeeQualification.employee_id == e.id,
                                                               EmployeeQualification.qualification_id == q.id))
            if eq is None:
                db.add(EmployeeQualification(company_id=company_id, employee_id=e.id, qualification_id=q.id, valid_until=until))
            else:
                eq.valid_until = until
        if it["pay_basis"]:
            db.add(CompensationTerm(company_id=company_id, employee_id=e.id, effective_from=it["hire_date"] or date(2000, 1, 1),
                                    pay_basis=it["pay_basis"], hourly_rate=it["hourly_rate"], monthly_salary=it["monthly_salary"],
                                    notes="Εισαγωγή CSV"))
    audit.log(db, company_id, user_id, "import.employees", "employee", None, created=result["created"], updated=result["updated"])
    db.commit()
    return result


WEEKDAY_ALIASES = {"δε": 0, "τρ": 1, "τε": 2, "πε": 3, "πα": 4, "σα": 5, "κυ": 6,
                   "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_weekdays(v: str) -> str:
    v = (v or "").strip().lower()
    if v in ("", "all", "όλες", "ολες", "0-6"):
        return "0,1,2,3,4,5,6"
    if v in ("weekdays", "καθημερινές", "0-4"):
        return "0,1,2,3,4"
    out = set()
    for p in v.replace("|", ",").split(","):
        p = p.strip()
        if p.isdigit() and 0 <= int(p) <= 6:
            out.add(int(p))
        elif p[:3] in WEEKDAY_ALIASES or p[:2] in WEEKDAY_ALIASES:
            out.add(WEEKDAY_ALIASES.get(p[:3], WEEKDAY_ALIASES.get(p[:2])))
        else:
            raise ValueError(f"μη έγκυρη ημέρα «{p}»")
    return ",".join(str(x) for x in sorted(out))


def import_sites(db: Session, company_id: int, user_id: int, text: str, commit: bool) -> dict:
    rows = _rows(text)
    errors, parsed = [], []
    missing = [c for c in ("client", "site") if rows and c not in rows[0]]
    if missing:
        return {"errors": [f"Λείπουν στήλες: {', '.join(missing)}"], "rows": 0, "sites": 0, "requirements": 0}
    for i, r in enumerate(rows, start=2):
        try:
            if not r["client"] or not r["site"]:
                raise ValueError("πελάτης και σημείο είναι υποχρεωτικά")
            item = {"client": r["client"], "site": r["site"], "address": r.get("address") or None,
                    "elig": _bool(r.get("requires_eligibility", "")), "req": None,
                    "contract_ref": r.get("contract_ref") or None, "contract_start": _date(r.get("contract_start", "")),
                    "contract_end": _date(r.get("contract_end", ""))}
            if item["contract_ref"] and not item["contract_start"]:
                raise ValueError("contract_start απαιτείται όταν υπάρχει contract_ref")
            if r.get("requirement"):
                hc = int(r.get("headcount") or 1)
                if not 1 <= hc <= 50:
                    raise ValueError("headcount 1–50")
                hm = r.get("holiday_mode") or "normal"
                if hm not in ("normal", "skip", "sunday_rule"):
                    raise ValueError("holiday_mode: normal | skip | sunday_rule")
                sh = _dec(r.get("shift_hours", ""))
                if sh is not None and not (Decimal(1) <= sh <= Decimal(24)):
                    raise ValueError("shift_hours 1–24")
                item["req"] = {"name": r["requirement"], "weekdays": parse_weekdays(r.get("weekdays", "")),
                               "start": _time(r.get("start", "")), "end": _time(r.get("end", "")), "shift_hours": sh,
                               "headcount": hc, "quals": r.get("qualifications", ""), "holiday_mode": hm,
                               "billing_rate": _dec(r.get("billing_rate", ""))}
            parsed.append(item)
        except (ValueError, KeyError) as exc:
            errors.append(f"Γραμμή {i}: {exc}")
    result = {"errors": errors, "rows": len(rows), "sites": len({(p["client"], p["site"]) for p in parsed}),
              "requirements": sum(1 for p in parsed if p["req"])}
    if errors or not commit:
        return result
    for it in parsed:
        c = db.scalar(select(Client).where(Client.company_id == company_id, Client.name == it["client"]))
        if c is None:
            c = Client(company_id=company_id, name=it["client"])
            db.add(c)
            db.flush()
        contract = None
        if it["contract_ref"]:
            contract = db.scalar(select(Contract).where(Contract.company_id == company_id, Contract.client_id == c.id,
                                                        Contract.reference == it["contract_ref"]))
            if contract is None:
                contract = Contract(company_id=company_id, client_id=c.id, reference=it["contract_ref"],
                                    start_date=it["contract_start"], end_date=it["contract_end"])
                db.add(contract)
                db.flush()
        s = db.scalar(select(Site).where(Site.company_id == company_id, Site.client_id == c.id, Site.name == it["site"]))
        if s is None:
            s = Site(company_id=company_id, client_id=c.id, name=it["site"])
            db.add(s)
        s.address = it["address"] or s.address
        s.requires_explicit_eligibility = it["elig"]
        if contract:
            s.contract_id = contract.id
        db.flush()
        if it["req"]:
            rq = it["req"]
            qids = [q.id for q, _ in _quals(db, company_id, rq["quals"])]
            db.add(CoverageRequirement(company_id=company_id, site_id=s.id, name=rq["name"], weekdays=rq["weekdays"],
                                       start_time=rq["start"], end_time=rq["end"], shift_hours=rq["shift_hours"],
                                       headcount=rq["headcount"], holiday_mode=rq["holiday_mode"],
                                       billing_rate_hourly=rq["billing_rate"], qualification_ids=qids))
    audit.log(db, company_id, user_id, "import.sites", "site", None, sites=result["sites"], requirements=result["requirements"])
    db.commit()
    return result
