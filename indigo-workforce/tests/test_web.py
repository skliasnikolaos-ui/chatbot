"""HTTP-level tests: authentication, server-side authorization per role,
company data isolation, CSRF, every screen rendering, and the critical
end-to-end workflow through the real routes."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from indigo.models import (Absence, Assignment, CoverageRequirement, Employee, GenerationRun, PayrollRun, ScheduleVersion,
                           Site, User)
from indigo.services import scheduling as sch
from indigo.services.companies import create_company, create_user

from .conftest import PASSWORD


@pytest.fixture()
def app(engine):
    from indigo.web.app import create_app
    return create_app(str(engine.url))


def login(app, email, password=PASSWORD) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


def csrf(c: TestClient, path="/") -> str:
    html = c.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, html[:500]
    return m.group(1)


@pytest.fixture()
def other_company(db):
    c = create_company(db, "Άλλη εταιρεία")
    create_user(db, c.id, "admin@other.invalid", "Other Admin", "admin", PASSWORD)
    s = Site(company_id=c.id, client_id=_client(db, c.id), name="Ξένο σημείο")
    db.add(s)
    db.flush()
    e = Employee(company_id=c.id, code="X1", first_name="Ξένος", last_name="Εργαζόμενος")
    db.add(e)
    db.commit()
    return {"company_id": c.id, "site_id": s.id, "employee_id": e.id}


def _client(db, cid):
    from indigo.models import Client
    cl = Client(company_id=cid, name="Πελάτης Χ")
    db.add(cl)
    db.flush()
    return cl.id


def test_login_required_and_bad_password(app, demo):
    c = TestClient(app)
    assert c.get("/", follow_redirects=False).status_code == 303
    r = c.post("/login", data={"email": "admin@demo.invalid", "password": "wrong-password"}, follow_redirects=False)
    assert r.status_code == 401


def test_lockout_after_failed_logins(app, demo, db):
    c = TestClient(app)
    for _ in range(5):
        c.post("/login", data={"email": "reviewer@demo.invalid", "password": "nope-nope-nope"})
    r = c.post("/login", data={"email": "reviewer@demo.invalid", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 401  # locked even with the right password


SCREENS = ["/", "/employees", "/employees/new", "/employees/import", "/clients", "/sites/import", "/schedule?year=2026&month=11",
           "/schedule/generate?year=2026&month=11", "/coverage?year=2026&month=11", "/absences", "/attendance",
           "/payroll?year=2026&month=11", "/reports?year=2026&month=11", "/settings", "/settings/audit"]


def test_every_screen_renders_for_admin(app, demo, db):
    c = login(app, "admin@demo.invalid")
    emp = db.scalar(select(Employee).where(Employee.company_id == demo["company_id"]))
    site = db.scalar(select(Site).where(Site.company_id == demo["company_id"]))
    for path in SCREENS + [f"/employees/{emp.id}", f"/sites/{site.id}"]:
        r = c.get(path)
        assert r.status_code == 200, (path, r.text[:300])
        assert "ΣΥΝΘΕΤΙΚΑ ΔΕΔΟΜΕΝΑ" in r.text  # demo banner


@pytest.mark.parametrize("role,path,expected", [
    ("reviewer", "/employees/new", 403),
    ("reviewer", "/schedule/generate?year=2026&month=11", 403),
    ("reviewer", "/settings", 403),
    ("reviewer", "/payroll?year=2026&month=11", 200),
    ("reviewer", "/settings/audit", 200),
    ("scheduler", "/payroll?year=2026&month=11", 403),
    ("scheduler", "/reports/costs.csv?year=2026&month=11", 403),
    ("scheduler", "/settings/audit", 403),
    ("scheduler", "/schedule/generate?year=2026&month=11", 200),
    ("payroll", "/schedule/generate?year=2026&month=11", 403),
    ("payroll", "/reports/costs.csv?year=2026&month=11", 200),
    ("payroll", "/settings", 403),
])
def test_role_permissions_enforced_server_side(app, demo, role, path, expected):
    c = login(app, f"{role}@demo.invalid")
    assert c.get(path).status_code == expected


def test_compensation_hidden_and_blocked_for_scheduler(app, demo, db):
    emp = db.scalar(select(Employee).where(Employee.company_id == demo["company_id"]))
    c = login(app, "scheduler@demo.invalid")
    page = c.get(f"/employees/{emp.id}").text
    assert "Όροι αμοιβής" not in page
    token = csrf(c, f"/employees/{emp.id}")
    r = c.post(f"/employees/{emp.id}/compensation", data={"csrf_token": token, "pay_basis": "hourly",
                                                          "effective_from": "2026-01-01", "hourly_rate": "99"})
    assert r.status_code == 403


def test_reviewer_cannot_post(app, demo, db):
    c = login(app, "reviewer@demo.invalid")
    token = csrf(c)
    r = c.post("/absences", data={"csrf_token": token, "employee_id": "1", "kind": "leave", "start_date": "2026-11-01", "end_date": "2026-11-02"})
    assert r.status_code == 403
    r = c.post("/schedule/generate", data={"csrf_token": token, "year": "2026", "month": "11"})
    assert r.status_code == 403


def test_csrf_required(app, demo):
    c = login(app, "admin@demo.invalid")
    r = c.post("/clients", data={"name": "X"})
    assert r.status_code == 403
    r = c.post("/clients", data={"name": "X", "csrf_token": "forged"})
    assert r.status_code == 403


def test_company_isolation(app, demo, other_company, db):
    c = login(app, "admin@demo.invalid")
    oc = other_company
    # reads of another company's rows by id → 404
    assert c.get(f"/employees/{oc['employee_id']}").status_code == 404
    assert c.get(f"/sites/{oc['site_id']}").status_code == 404
    # writes against another company's rows → 404, and nothing changes
    token = csrf(c)
    r = c.post(f"/employees/{oc['employee_id']}", data={"csrf_token": token, "code": "HACK", "first_name": "a", "last_name": "b",
                                                        "weekly_contract_hours": "40", "min_rest_hours": "11"})
    assert r.status_code == 404
    db.expire_all()
    assert db.get(Employee, oc["employee_id"]).code == "X1"
    # absence for a foreign employee
    r = c.post("/absences", data={"csrf_token": token, "employee_id": str(oc["employee_id"]), "kind": "leave",
                                  "start_date": "2026-11-01", "end_date": "2026-11-02"})
    assert r.status_code == 404
    # listings never include foreign rows
    assert "Ξένος" not in c.get("/employees?show=all").text
    assert "Ξένο σημείο" not in c.get("/clients").text
    # the other company's admin sees none of the demo data
    c2 = login(app, "admin@other.invalid")
    assert "D001" not in c2.get("/employees?show=all").text
    assert "ΣΥΝΘΕΤΙΚΑ" not in c2.get("/").text


def test_foreign_ids_in_exports_and_versions(app, demo, other_company, db):
    # make a schedule version in the demo company, then try to read it as the other company
    period = sch.get_period(db, demo["company_id"], 2026, 11)
    db.commit()
    run = sch.generate(db, demo["company_id"], None, 2026, 11, time_limit_s=8, expected_period_revision=period.revision)
    c2 = login(app, "admin@other.invalid")
    assert c2.get(f"/schedule/versions/{run.version_id}/export.csv").status_code == 404
    assert c2.get(f"/schedule/versions/{run.version_id}/diff").status_code == 404
    assert c2.get(f"/schedule/runs/{run.id}").status_code == 404
    assert c2.get(f"/schedule?v={run.version_id}").status_code == 404


def test_end_to_end_http_workflow(app, demo, db):
    cid = demo["company_id"]
    c = login(app, "scheduler@demo.invalid")
    # 1. create an employee and a site requirement through the UI
    token = csrf(c, "/employees/new")
    r = c.post("/employees/new", data={"csrf_token": token, "code": "N100", "first_name": "Νέος", "last_name": "Φύλακας",
                                       "weekly_contract_hours": "40", "max_hours_week": "48", "min_rest_hours": "11",
                                       "max_consecutive_days": "6", "max_shift_hours": "12"}, follow_redirects=False)
    assert r.status_code == 303
    site = db.scalar(select(Site).where(Site.company_id == cid))
    r = c.post(f"/sites/{site.id}/requirements", data={"csrf_token": token, "name": "Πρόσθετη θέση", "start_time": "09:00",
                                                       "end_time": "13:00", "headcount": "1", "weekdays[]": ["0", "2", "4"],
                                                       "holiday_mode": "normal"}, follow_redirects=False)
    assert r.status_code == 303
    assert db.scalar(select(CoverageRequirement).where(CoverageRequirement.name == "Πρόσθετη θέση")) is not None
    # 2. generate
    token = csrf(c, "/schedule/generate?year=2026&month=11")
    period = sch.get_period(db, cid, 2026, 11, create=False)
    db.refresh(period)
    from indigo.engine.types import Weights
    data = {"csrf_token": token, "year": "2026", "month": "11", "period_revision": str(period.revision), "time_limit": "10"}
    data.update({f"w_{k}": "5" for k in Weights.UNITS})
    r = c.post("/schedule/generate", data=data, follow_redirects=False)
    assert r.status_code == 303 and "/schedule/runs/" in r.headers["location"]
    page = c.get(r.headers["location"]).text
    assert "Αποτέλεσμα" in page
    run = db.scalar(select(GenerationRun).order_by(GenerationRun.id.desc()))
    v = db.get(ScheduleVersion, run.version_id)
    # stale period revision → conflict message, no new run
    r = c.post("/schedule/generate", data=data, follow_redirects=False)
    assert "err=" in r.headers["location"]
    # 3. coverage report and board
    assert c.get(f"/coverage?year=2026&month=11&v={v.id}").status_code == 200
    assert c.get(f"/schedule?year=2026&month=11&v={v.id}&view=employees").status_code == 200
    # 4. publish
    token = csrf(c, f"/schedule/versions/{v.id}/diff")
    db.refresh(v)
    r = c.post(f"/schedule/versions/{v.id}/publish", data={"csrf_token": token, "revision": str(v.revision), "accept_gaps": "1"},
               follow_redirects=False)
    assert "msg=" in r.headers["location"], r.headers["location"]
    db.refresh(v)
    assert v.status == "published"
    # 5. absence after publication → replacement candidates → staged in draft → publish
    a = sch.assignments_of(db, v.id)[5]
    r = c.post("/absences", data={"csrf_token": token, "employee_id": str(a.employee_id), "kind": "sick",
                                  "start_date": a.service_date.isoformat(), "end_date": a.service_date.isoformat(), "status": "approved"},
               follow_redirects=False)
    ab = db.scalar(select(Absence).order_by(Absence.id.desc()))
    assert f"/absences/{ab.id}" in r.headers["location"]
    page = c.get(f"/absences/{ab.id}/assignments/{a.id}").text
    assert "Επιλέξιμοι" in page
    m = re.search(r'name="employee_id" value="(\d+)"', page)
    assert m
    r = c.post(f"/absences/{ab.id}/assignments/{a.id}", data={"csrf_token": token, "employee_id": m.group(1), "reason": "test"},
               follow_redirects=False)
    assert "/diff" in r.headers["location"], r.headers["location"]
    draft = db.scalar(select(ScheduleVersion).where(ScheduleVersion.status == "draft", ScheduleVersion.company_id == cid))
    db.refresh(draft)
    r = c.post(f"/schedule/versions/{draft.id}/publish", data={"csrf_token": token, "revision": str(draft.revision), "accept_gaps": "1"},
               follow_redirects=False)
    assert "msg=" in r.headers["location"], r.headers["location"]
    # 6. attendance (scheduler) + approval (payroll operator)
    new_a = db.scalar(select(Assignment).where(Assignment.version_id == draft.id, Assignment.slot_key == a.slot_key))
    assert new_a.employee_id == int(m.group(1))
    r = c.post("/attendance/record", data={"csrf_token": token, "employee_id": str(new_a.employee_id), "assignment_id": str(new_a.id),
                                           "clock_in": new_a.start_utc.astimezone(__import__("indigo.timeutil", fromlist=["ATHENS"]).ATHENS).strftime("%Y-%m-%dT%H:%M"),
                                           "clock_out": new_a.end_utc.astimezone(__import__("indigo.timeutil", fromlist=["ATHENS"]).ATHENS).strftime("%Y-%m-%dT%H:%M")},
               follow_redirects=False)
    assert "msg=" in r.headers["location"], r.headers["location"]
    # scheduler may not approve hours
    from indigo.models import AttendanceRecord
    rec = db.scalar(select(AttendanceRecord).order_by(AttendanceRecord.id.desc()))
    assert c.post("/attendance/approve", data={"csrf_token": token, "basis": "attendance", "attendance_id": str(rec.id)}).status_code == 403
    p = login(app, "payroll@demo.invalid")
    pt = csrf(p, "/attendance")
    r = p.post("/attendance/approve", data={"csrf_token": pt, "basis": "attendance", "attendance_id": str(rec.id)}, follow_redirects=False)
    assert "msg=" in r.headers["location"], r.headers["location"]
    # 7. payroll run, export, lock
    from indigo.services import payroll as pay
    pp = pay.get_period(db, cid, 2026, 11)
    db.commit()
    r = p.post("/payroll/run", data={"csrf_token": pt, "year": "2026", "month": "11", "revision": str(pp.revision)}, follow_redirects=False)
    assert "msg=" in r.headers["location"], r.headers["location"]
    run = db.scalar(select(PayrollRun).where(PayrollRun.company_id == cid).order_by(PayrollRun.id.desc()))
    csv = p.get(f"/payroll/runs/{run.id}/export.csv")
    assert csv.status_code == 200 and "ΕΚΤΙΜΗΣΗ" in csv.content.decode("utf-8-sig")
    db.refresh(pp)
    r = p.post("/payroll/lock", data={"csrf_token": pt, "year": "2026", "month": "11", "revision": str(pp.revision)}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    # attendance in a locked period is refused with a message
    r = c.post("/attendance/record", data={"csrf_token": token, "employee_id": str(new_a.employee_id),
                                           "clock_in": "2026-11-20T08:00", "clock_out": "2026-11-20T16:00"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    # payroll operator cannot reopen; admin can with a reason
    assert p.post("/payroll/reopen", data={"csrf_token": pt, "year": "2026", "month": "11", "reason": "x", "revision": "0"}).status_code == 403
    # 8. reports
    assert p.get("/reports?year=2026&month=11").status_code == 200
    gaps = c.get("/reports/gaps.csv?year=2026&month=11")
    assert gaps.status_code == 200


def test_import_employees_dry_run_then_commit(app, demo, db):
    c = login(app, "admin@demo.invalid")
    token = csrf(c, "/employees/import")
    text = "code;last_name;first_name;hire_date;weekly_hours;qualifications;pay_basis;hourly_rate\nI1;Δοκιμής;Αλέξης;2025-01-10;40;ΑΦΑ:2027-12-31;hourly;7,50\nI2;Λάθος;Γραμμή;99/99/2025;40;;;\n"
    r = c.post("/employees/import", data={"csrf_token": token, "text": text, "commit": "1"})
    assert "Γραμμή 3" in r.text
    assert db.scalar(select(Employee).where(Employee.code == "I1")) is None  # all-or-nothing
    text_ok = text.splitlines()[0] + "\n" + text.splitlines()[1] + "\n"
    r = c.post("/employees/import", data={"csrf_token": token, "text": text_ok, "commit": "1"})
    assert "Αποθηκεύτηκε" in r.text
    assert db.scalar(select(Employee).where(Employee.code == "I1", Employee.company_id == demo["company_id"])) is not None


def test_security_headers_and_session_cookie(app, demo):
    c = TestClient(app)
    r = c.post("/login", data={"email": "admin@demo.invalid", "password": PASSWORD}, follow_redirects=False)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    r = c.get("/")
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_deactivated_user_session_revoked(app, demo, db):
    rc = login(app, "reviewer@demo.invalid")
    assert rc.get("/").status_code == 200
    a = login(app, "admin@demo.invalid")
    u = db.scalar(select(User).where(User.email == "reviewer@demo.invalid"))
    token = csrf(a, "/settings")
    a.post(f"/settings/users/{u.id}", data={"csrf_token": token, "role": "reviewer"})  # is_active unchecked → deactivate
    assert rc.get("/", follow_redirects=False).status_code == 303
