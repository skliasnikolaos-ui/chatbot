"""Synthetic pilot data. Every name and number here is invented.

The company is flagged is_demo=True and the UI shows a permanent banner.
"""
from __future__ import annotations

import random
from datetime import date, time, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import (Absence, Client, CompensationTerm, Contract, CoverageRequirement, Employee, EmployeeQualification,
                      EmployeeSiteRule, FixedPost, Qualification, Site, WeeklyUnavailability)
from .companies import create_company, create_user

FIRST = ["Γιώργος", "Μαρία", "Νίκος", "Ελένη", "Κώστας", "Αννα", "Δημήτρης", "Σοφία", "Γιάννης", "Κατερίνα", "Παναγιώτης",
         "Δέσποινα", "Βασίλης", "Χριστίνα", "Θανάσης", "Ιωάννα", "Μιχάλης", "Αγγελική", "Στέλιος", "Βάσω", "Αντώνης",
         "Ειρήνη", "Λευτέρης", "Φωτεινή", "Σπύρος", "Νίκη", "Τάσος", "Ζωή"]
LAST = ["Παπαδόπουλος", "Ιωάννου", "Γεωργίου", "Νικολάου", "Δημητρίου", "Αθανασίου", "Κωνσταντίνου", "Οικονόμου", "Μακρής",
        "Βλάχος", "Αλεξίου", "Σταθόπουλος", "Πετρίδης", "Καραγιάννης", "Λαμπρόπουλος", "Χατζής", "Μαυρίδης", "Ζαχαρίου",
        "Αντωνίου", "Σαββίδης", "Τσιάρας", "Ραφτόπουλος", "Κυριακίδης", "Φωτίου", "Μπακάλης", "Δούκας", "Ρήγας", "Ξένος"]


def seed(db: Session, year: int, month: int, password: str, n_employees: int = 24, email_domain: str = "demo.invalid") -> dict:
    rnd = random.Random(42)
    c = create_company(db, "DEMO – Συνθετικά δεδομένα", is_demo=True, years=[year - 1, year, year + 1])
    users = {}
    for role, name in (("admin", "Διαχειριστής Demo"), ("scheduler", "Προγραμματιστής Demo"),
                       ("payroll", "Μισθοδοσία Demo"), ("reviewer", "Αναγνώστης Demo")):
        users[role] = create_user(db, c.id, f"{role}@{email_domain}", name, role, password).email
    q_lic = Qualification(company_id=c.id, code="ΑΦΑ", name="Άδεια εργασίας προσωπικού ασφαλείας")
    q_fire = Qualification(company_id=c.id, code="ΠΥΡ", name="Πυρασφάλεια")
    q_fa = Qualification(company_id=c.id, code="ΑΒ", name="Πρώτες βοήθειες")
    db.add_all([q_lic, q_fire, q_fa])
    db.flush()
    first = date(year, month, 1)
    clients = [Client(company_id=c.id, name=n) for n in ("ΔΕΙΓΜΑ Εμπορικό Κέντρο Α.Ε.", "ΔΕΙΓΜΑ Logistics Ο.Ε.", "ΔΕΙΓΜΑ Γραφεία Α.Ε.")]
    db.add_all(clients)
    db.flush()
    contracts = [Contract(company_id=c.id, client_id=cl.id, reference=f"ΣΥΜ-{i + 1:03d}", start_date=first - timedelta(days=200))
                 for i, cl in enumerate(clients)]
    db.add_all(contracts)
    db.flush()
    sites = [
        Site(company_id=c.id, client_id=clients[0].id, contract_id=contracts[0].id, name="Εμπορικό – Κεντρική είσοδος", address="Οδός Δείγματος 1"),
        Site(company_id=c.id, client_id=clients[0].id, contract_id=contracts[0].id, name="Εμπορικό – Parking", address="Οδός Δείγματος 1"),
        Site(company_id=c.id, client_id=clients[1].id, contract_id=contracts[1].id, name="Αποθήκη Ασπροπύργου (ΔΕΙΓΜΑ)",
             requires_explicit_eligibility=True, restrictions="Μόνο εγκεκριμένο προσωπικό"),
        Site(company_id=c.id, client_id=clients[2].id, contract_id=contracts[2].id, name="Γραφεία Μαρουσίου (ΔΕΙΓΜΑ)"),
    ]
    db.add_all(sites)
    db.flush()
    R = lambda **kw: CoverageRequirement(company_id=c.id, **kw)  # noqa: E731
    reqs = [
        R(site_id=sites[0].id, name="Είσοδος 24ωρη", weekdays="0,1,2,3,4,5,6", start_time=time(6), end_time=time(6), shift_hours=Decimal(8),
          headcount=1, qualification_ids=[q_lic.id], billing_rate_hourly=Decimal("14.50"), holiday_mode="normal"),
        R(site_id=sites[0].id, name="Ενίσχυση ωραρίου καταστημάτων", weekdays="0,1,2,3,4,5", start_time=time(10), end_time=time(18),
          headcount=1, qualification_ids=[q_lic.id], billing_rate_hourly=Decimal("13.00"), holiday_mode="skip"),
        R(site_id=sites[1].id, name="Parking βράδυ", weekdays="0,1,2,3,4,5,6", start_time=time(18), end_time=time(2),
          headcount=1, qualification_ids=[q_lic.id], billing_rate_hourly=Decimal("13.50")),
        R(site_id=sites[2].id, name="Αποθήκη 24ωρη (2 φύλακες)", weekdays="0,1,2,3,4,5,6", start_time=time(7), end_time=time(7),
          shift_hours=Decimal(12), headcount=2, qualification_ids=[q_lic.id, q_fire.id], billing_rate_hourly=Decimal("15.00")),
        R(site_id=sites[3].id, name="Υποδοχή γραφείων", weekdays="0,1,2,3,4", start_time=time(8), end_time=time(16), headcount=1,
          qualification_ids=[q_lic.id, q_fa.id], billing_rate_hourly=Decimal("13.00"), holiday_mode="skip"),
    ]
    db.add_all(reqs)
    db.flush()
    emps = []
    for i in range(n_employees):
        part = i % 6 == 5
        relief = i >= n_employees - 2
        e = Employee(company_id=c.id, code=f"D{i + 1:03d}", first_name=FIRST[i % len(FIRST)], last_name=LAST[i % len(LAST)],
                     hire_date=first - timedelta(days=rnd.randint(60, 1500)), contract_type="part_time" if part else "full_time",
                     weekly_contract_hours=Decimal(24 if part else 40), max_hours_week=Decimal(30 if part else 48),
                     max_hours_month=None, min_rest_hours=Decimal(11), max_consecutive_days=6, is_relief=relief,
                     night_preference=rnd.choice(["neutral", "neutral", "avoid", "prefer"]), avoid_weekends=rnd.random() < 0.15,
                     notes="ΣΥΝΘΕΤΙΚΟΣ ΕΡΓΑΖΟΜΕΝΟΣ")
        db.add(e)
        emps.append(e)
    db.flush()
    for i, e in enumerate(emps):
        exp = first + timedelta(days=12) if i == 3 else first + timedelta(days=rnd.randint(200, 900))  # one licence expires mid-month
        db.add(EmployeeQualification(company_id=c.id, employee_id=e.id, qualification_id=q_lic.id,
                                     licence_number=f"ΔΕΙΓΜΑ-{1000 + i}", valid_until=exp))
        if i % 2 == 0:
            db.add(EmployeeQualification(company_id=c.id, employee_id=e.id, qualification_id=q_fire.id))
        if i % 4 == 1:
            db.add(EmployeeQualification(company_id=c.id, employee_id=e.id, qualification_id=q_fa.id))
        if i % 2 == 0 and i < 16:
            db.add(EmployeeSiteRule(company_id=c.id, employee_id=e.id, site_id=sites[2].id, rule="eligible"))
        if i == 7:
            db.add(EmployeeSiteRule(company_id=c.id, employee_id=e.id, site_id=sites[1].id, rule="prohibited"))
        if i % 5 == 2:
            db.add(WeeklyUnavailability(company_id=c.id, employee_id=e.id, weekday=rnd.randint(0, 6), start_time=time(0), end_time=time(23, 59)))
        rate = Decimal("7.20") + Decimal(rnd.randint(0, 120)) / 100
        if i % 7 == 0:
            db.add(CompensationTerm(company_id=c.id, employee_id=e.id, effective_from=e.hire_date, pay_basis="monthly",
                                    monthly_salary=Decimal(1100 + rnd.randint(0, 200)), notes="ΣΥΝΘΕΤΙΚΟ"))
        else:
            db.add(CompensationTerm(company_id=c.id, employee_id=e.id, effective_from=e.hire_date, pay_basis="hourly",
                                    hourly_rate=rate, notes="ΣΥΝΘΕΤΙΚΟ"))
    db.add(FixedPost(company_id=c.id, employee_id=emps[1].id, requirement_id=reqs[4].id, weekdays="0,1,2,3,4"))
    db.add(Absence(company_id=c.id, employee_id=emps[5].id, kind="leave", start_date=first + timedelta(days=9),
                   end_date=first + timedelta(days=15), status="approved", notes="ΣΥΝΘΕΤΙΚΗ άδεια"))
    db.add(Absence(company_id=c.id, employee_id=emps[9].id, kind="sick", start_date=first + timedelta(days=3),
                   end_date=first + timedelta(days=4), status="approved", notes="ΣΥΝΘΕΤΙΚΗ ασθένεια"))
    db.commit()
    return {"company_id": c.id, "users": users}
