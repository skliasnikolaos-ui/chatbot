"""End-to-end vertical workflow at the service layer, on a real database:
employees → site demand → automatic schedule → coverage → publish → absence →
replacement → attendance → approval → payroll → lock/reopen."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from indigo.engine import rules
from indigo.engine.types import Weights
from indigo.models import Absence, Assignment, AuditLog, Employee, ScheduleVersion
from indigo.services import attendance as att
from indigo.services import payroll as pay
from indigo.services import replacements as rep
from indigo.services import reports
from indigo.services import scheduling as sch
from indigo.services.attendance import LockedPeriod


def _generate(db, cid, **kw):
    period = sch.get_period(db, cid, 2026, 11)
    db.commit()
    return sch.generate(db, cid, None, 2026, 11, time_limit_s=kw.pop("time_limit_s", 20),
                        expected_period_revision=period.revision, **kw)


def test_full_workflow(db, demo):
    cid = demo["company_id"]
    run = _generate(db, cid)
    assert run.status in ("complete", "partial"), run.report
    rep_ = run.report
    assert rep_["stats"]["slots"] > 0
    v = db.get(ScheduleVersion, run.version_id)
    assert v.status == "draft"
    # independent validation of the persisted draft
    check = sch.validate_version(db, v)
    assert check["violations"] == [], [x.as_dict() for x in check["violations"]]
    # every uncovered slot has an explanation
    for u in rep_["uncovered"]:
        assert u["cause"] in ("NO_QUALIFIED", "UNAVAILABLE", "CAPACITY", "NOT_ASSIGNED")
    # expired licence (employee D004 expires on day 13) is respected
    d4 = db.scalar(select(Employee).where(Employee.company_id == cid, Employee.code == "D004"))
    for a in sch.assignments_of(db, v.id):
        if a.employee_id == d4.id:
            assert a.service_date <= date(2026, 11, 13)
    # coverage report agrees with solver
    cov = reports.coverage(db, cid, 2026, 11, version=v)
    assert abs(cov["total_uncovered_h"] - rep_["stats"]["uncovered_hours"]) < 0.01

    # publish (accept gaps if any)
    sch.publish(db, cid, 1, v.id, v.revision, accept_gaps=True)
    db.refresh(v)
    assert v.status == "published"

    # leave after publication → affected assignments → replacement → approval
    victim = sch.assignments_of(db, v.id)[10]
    ab = Absence(company_id=cid, employee_id=victim.employee_id, kind="sick", start_date=victim.service_date,
                 end_date=victim.service_date, status="approved")
    db.add(ab)
    db.commit()
    affected = rep.affected_assignments(db, cid, ab)
    assert victim.id in [a.id for a in affected]
    cands = rep.candidates(db, cid, victim.id)
    assert all(c["employee_id"] != victim.employee_id for c in cands["eligible"])
    # published validation now flags the absence (the plan is stale until replaced)
    assert any(x.code == "ABSENCE" for x in sch.validate_version(db, v)["violations"])
    if cands["eligible"]:
        pick = cands["eligible"][0]["employee_id"]
        draft = rep.apply(db, cid, 1, victim.id, pick, "ασθένεια")
        assert draft.status == "draft"
        # the published version is unchanged until approval
        assert db.scalar(select(Assignment).where(Assignment.version_id == v.id, Assignment.slot_key == victim.slot_key)).employee_id == victim.employee_id
        diff = sch.diff_versions(db, draft, v)
        assert any(d["slot_key"] == victim.slot_key for d in diff)
        db.refresh(draft)
        sch.publish(db, cid, 1, draft.id, draft.revision, accept_gaps=True)
        db.refresh(v)
        assert v.status == "superseded"  # history preserved
        assert db.scalar(select(Assignment).where(Assignment.version_id == draft.id, Assignment.slot_key == victim.slot_key)).employee_id == pick

    # attendance + approval for a few published shifts
    pub = sch.version_with_status(db, sch.get_period(db, cid, 2026, 11).id, "published")
    shifts = sch.assignments_of(db, pub.id)[:6]
    for i, a in enumerate(shifts):
        rec = att.record_attendance(db, cid, 1, a.employee_id, a.start_utc, a.end_utc + timedelta(minutes=30 if i == 0 else 0), assignment_id=a.id)
        att.approve(db, cid, 1, employee_id=a.employee_id, start=rec.clock_in, end=rec.clock_out, basis="attendance", attendance_id=rec.id)
    rows = att.reconcile(db, cid, date(2026, 11, 1), date(2026, 11, 30))
    assert any("Διαφορά λήξης +30′" in r["flags"] for r in rows)
    assert any("Χωρίς καταγραφή παρουσίας" in r["flags"] for r in rows)

    # payroll run, lock, blocked change, reopen with reason
    period = pay.get_period(db, cid, 2026, 11)
    db.commit()
    r1 = pay.run(db, cid, 1, 2026, 11, period.revision)
    assert r1.version_no == 1 and r1.ruleset_snapshot["verified"] is False
    db.refresh(period)
    pay.lock(db, cid, 1, 2026, 11, period.revision)
    with pytest.raises(LockedPeriod):
        att.record_attendance(db, cid, 1, shifts[0].employee_id, shifts[0].start_utc, shifts[0].end_utc)
    with pytest.raises(LockedPeriod):
        pay.run(db, cid, 1, 2026, 11, None)
    with pytest.raises(ValueError):
        pay.reopen(db, cid, 1, 2026, 11, "", None)
    pay.reopen(db, cid, 1, 2026, 11, "Διόρθωση ωρών εργαζομένου", None)
    r2 = pay.run(db, cid, 1, 2026, 11, None)
    assert r2.version_no == 2
    db.refresh(r1)
    assert r1.is_current is False  # earlier run retained, not overwritten
    actions = {a.action for a in db.scalars(select(AuditLog).where(AuditLog.company_id == cid))}
    assert {"schedule.generate", "schedule.publish", "payroll.lock", "payroll.reopen", "hours.approve"} <= actions

    # cost report runs and planned cost is positive
    c = reports.costs(db, cid, 2026, 11)
    assert c["total_planned"] > 0


def test_regenerate_subset_preserves_locked_and_outside_scope(db, demo):
    cid = demo["company_id"]
    run = _generate(db, cid, time_limit_s=10)
    v = db.get(ScheduleVersion, run.version_id)
    assigns = sch.assignments_of(db, v.id)
    keep = assigns[0]
    sch.set_lock(db, cid, 1, v.id, keep.slot_key, True, v.revision)
    target_dates = {date(2026, 11, 20), date(2026, 11, 21)}
    outside_before = {a.slot_key: a.employee_id for a in assigns if a.service_date not in target_dates}
    run2 = _generate(db, cid, dates=target_dates | {keep.service_date}, time_limit_s=10,
                     weights=Weights(stability=0, cost=10))
    v2 = db.get(ScheduleVersion, run2.version_id)
    after = {a.slot_key: a for a in sch.assignments_of(db, v2.id)}
    assert after[keep.slot_key].employee_id == keep.employee_id and after[keep.slot_key].locked
    for k, e in outside_before.items():
        if k.split("|")[1] != keep.service_date.isoformat():
            assert after[k].employee_id == e
    db.refresh(v)
    assert v.status == "discarded"
    assert sch.validate_version(db, v2)["violations"] == []


def test_manual_edit_validation_and_optimistic_lock(db, demo):
    cid = demo["company_id"]
    run = _generate(db, cid, time_limit_s=10)
    v = db.get(ScheduleVersion, run.version_id)
    a0, a1 = sch.assignments_of(db, v.id)[:2]
    rev = v.revision
    # assigning the same employee to an overlapping/too-close slot must be refused
    overlapping = [a for a in sch.assignments_of(db, v.id) if a.employee_id != a0.employee_id
                   and a.start_utc < a0.end_utc and a0.start_utc < a.end_utc]
    if overlapping:
        with pytest.raises(sch.RuleViolation) as ei:
            sch.assign(db, cid, 1, v.id, overlapping[0].slot_key, a0.employee_id, rev)
        assert {x.code for x in ei.value.violations} & {"OVERLAP", "REST"}
    # clearing works with current revision; a stale revision is rejected
    sch.assign(db, cid, 1, v.id, a1.slot_key, None, rev)
    with pytest.raises(sch.ConflictError):
        sch.assign(db, cid, 1, v.id, a0.slot_key, None, rev)


def test_leave_after_publication_blocks_locked_regeneration(db, demo):
    cid = demo["company_id"]
    run = _generate(db, cid, time_limit_s=10)
    v = db.get(ScheduleVersion, run.version_id)
    a = sch.assignments_of(db, v.id)[0]
    sch.set_lock(db, cid, 1, v.id, a.slot_key, True, v.revision)
    db.add(Absence(company_id=cid, employee_id=a.employee_id, kind="leave", start_date=a.service_date, end_date=a.service_date, status="approved"))
    db.commit()
    run2 = _generate(db, cid, time_limit_s=10)
    assert run2.status == "locked_conflict"
    assert run2.version_id is None
    assert run2.report["locked_violations"][0]["code"] == "ABSENCE"
