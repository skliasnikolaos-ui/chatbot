"""Scheduling engine: demand expansion, DST, and solver invariants.

Every solver result is re-checked with the independent rules validator.
"""
from datetime import date, time, timedelta

import pytest

from indigo.engine import demand, rules, solver
from indigo.engine.types import EmployeeSpec, FixedWork, Problem, RequirementSpec, SiteSpec, Weights
from indigo.holidays import greek_holidays, orthodox_easter
from indigo.timeutil import split_minutes, to_local

ALL_DAYS = frozenset(range(7))
SITES = {1: SiteSpec(1, "Α"), 2: SiteSpec(2, "Β", requires_explicit_eligibility=True)}


def req(id=1, site=1, start=time(8), end=time(16), headcount=1, shift_hours=None, quals=(), weekdays=ALL_DAYS, **kw):
    return RequirementSpec(id=id, site_id=site, name=f"R{id}", weekdays=frozenset(weekdays), start_time=start,
                           end_time=end, headcount=headcount, shift_hours=shift_hours, qualifications=frozenset(quals), **kw)


def emp(id, **kw):
    kw.setdefault("max_hours_week", 48)
    return EmployeeSpec(id=id, name=f"E{id}", **kw)


def run(reqs, emps, start, end, holidays=frozenset(), **kw):
    slots = demand.expand(reqs, start, end, set(holidays))
    p = Problem(slots=slots, employees=emps, sites=SITES, period_start=start, period_end=end,
                workers=kw.pop("workers", 1), time_limit_s=kw.pop("time_limit_s", 20), **kw)
    res = solver.solve(p)
    if res.status in ("complete", "partial"):
        viol = rules.validate(res.assignments, {e.id: e for e in emps}, SITES, p.fixed_work, {s.key for s in slots})
        assert viol == [], [v.as_dict() for v in viol]
        assert len(res.assignments) + len(res.uncovered) == len(slots)
    return p, res


# ---------------------------------------------------------------- time & demand
def test_orthodox_easter_known_dates():
    assert orthodox_easter(2024) == date(2024, 5, 5)
    assert orthodox_easter(2025) == date(2025, 4, 20)
    assert orthodox_easter(2026) == date(2026, 4, 12)
    names = dict(greek_holidays(2026))
    assert names[date(2026, 2, 23)] == "Καθαρά Δευτέρα"
    assert names[date(2026, 6, 1)] == "Αγίου Πνεύματος"


def test_overnight_shift_spans_two_days_and_counts_night():
    slots = demand.expand([req(start=time(22), end=time(6))], date(2026, 1, 10), date(2026, 1, 10), set())
    assert len(slots) == 1
    s = slots[0]
    assert s.minutes == 480 and s.night_minutes == 480 and s.is_night
    assert to_local(s.end).date() == date(2026, 1, 11)


def test_dst_spring_forward_and_fall_back():
    # Europe/Athens: 2026-03-29 03:00 → 04:00 ; 2026-10-25 04:00 → 03:00
    spring = demand.expand([req(start=time(22), end=time(6))], date(2026, 3, 28), date(2026, 3, 28), set())[0]
    autumn = demand.expand([req(start=time(22), end=time(6))], date(2026, 10, 24), date(2026, 10, 24), set())[0]
    assert spring.minutes == 7 * 60
    assert autumn.minutes == 9 * 60
    # 24h coverage split into 3 wall-clock shifts: segment lengths reflect real time
    day = demand.expand([req(start=time(0), end=time(0), shift_hours=8)], date(2026, 3, 29), date(2026, 3, 29), set())
    assert [s.minutes for s in day] == [7 * 60, 8 * 60, 8 * 60]


def test_month_and_year_boundary_overnight():
    slots = demand.expand([req(start=time(20), end=time(8))], date(2026, 12, 31), date(2026, 12, 31), {date(2027, 1, 1)})
    s = slots[0]
    parts = split_minutes(s.start, s.end, {date(2027, 1, 1)})
    assert parts["per_date"] == {date(2026, 12, 31): 240, date(2027, 1, 1): 480}
    assert parts["holiday"] == 480 and parts["night"] == 480


def test_holiday_modes():
    hol = {date(2026, 3, 25)}  # Wednesday
    weekdays_only = frozenset(range(5))
    assert len(demand.expand([req(weekdays=weekdays_only, holiday_mode="skip")], date(2026, 3, 25), date(2026, 3, 25), hol)) == 0
    assert len(demand.expand([req(weekdays=weekdays_only, holiday_mode="normal")], date(2026, 3, 25), date(2026, 3, 25), hol)) == 1
    assert len(demand.expand([req(weekdays=weekdays_only, holiday_mode="sunday_rule")], date(2026, 3, 25), date(2026, 3, 25), hol)) == 0


def test_multiple_positions_same_site():
    slots = demand.expand([req(headcount=3)], date(2026, 5, 1), date(2026, 5, 1), set())
    assert [s.position for s in slots] == [1, 2, 3]
    _, res = run([req(headcount=3)], [emp(1), emp(2), emp(3)], date(2026, 5, 1), date(2026, 5, 1))
    assert res.status == "complete"
    assert len({e for e, _ in res.assignments}) == 3


# ---------------------------------------------------------------- solver invariants
def test_insufficient_staff_returns_partial_with_explanation():
    # 24/7 coverage, one employee: impossible to cover; best partial schedule proven maximal
    p, res = run([req(start=time(0), end=time(0), shift_hours=8)], [emp(1)], date(2026, 2, 2), date(2026, 2, 8))
    assert res.status == "partial"
    assert res.proven_max_coverage
    assert res.stats["uncovered_hours"] > 0
    causes = {res.explanations[s.key]["cause"] for s in res.uncovered}
    assert causes <= {"CAPACITY", "UNAVAILABLE", "NO_QUALIFIED"}
    assert "CAPACITY" in causes
    # one person at 8h shifts with 11h rest and ≤6 consecutive days → at most 6 shifts
    assert len(res.assignments) == 6


def test_no_qualified_staff_cause():
    _, res = run([req(quals={5})], [emp(1), emp(2)], date(2026, 2, 2), date(2026, 2, 2))
    assert res.status == "partial"
    assert res.explanations[res.uncovered[0].key]["cause"] == "NO_QUALIFIED"


def test_expired_qualification_is_excluded():
    e1 = emp(1, qualifications={5: (None, date(2026, 2, 3))})
    e2 = emp(2, qualifications={5: (None, None)})
    _, res = run([req(quals={5})], [e1, e2], date(2026, 2, 2), date(2026, 2, 5), weights=Weights(cost=0, balance=0, continuity=10))
    for e, s in res.assignments:
        if s.service_date > date(2026, 2, 3):
            assert e == 2


def test_rest_and_overlap_respected():
    # two sites with adjacent night/day shifts; one employee must not take both
    reqs = [req(1, start=time(22), end=time(6)), req(2, start=time(6), end=time(14))]
    _, res = run(reqs, [emp(1), emp(2)], date(2026, 6, 1), date(2026, 6, 3))
    assert res.status == "complete"  # validator already confirmed rest ≥ 11h


def test_absence_and_weekly_unavailability():
    e1 = emp(1, absences=[(date(2026, 6, 2), date(2026, 6, 2), "leave")])
    e2 = emp(2, weekly_unavailability=[(2, time(0), time(23, 59))])  # Wednesdays (2026-06-03)
    _, res = run([req()], [e1, e2], date(2026, 6, 1), date(2026, 6, 3))
    got = {s.service_date: e for e, s in res.assignments}
    assert got[date(2026, 6, 2)] == 2
    assert got[date(2026, 6, 3)] == 1


def test_site_eligibility_and_prohibition():
    e1 = emp(1, site_rules={1: "prohibited"})
    e2 = emp(2)  # site 2 requires explicit eligibility
    e3 = emp(3, site_rules={2: "eligible"})
    _, res = run([req(1, site=1), req(2, site=2)], [e1, e2, e3], date(2026, 6, 1), date(2026, 6, 1))
    by_site = {s.site_id: e for e, s in res.assignments}
    assert by_site[1] != 1 and by_site[2] == 3


def test_weekly_limit():
    e1 = emp(1, max_hours_week=16)
    _, res = run([req()], [e1], date(2026, 6, 1), date(2026, 6, 7))
    assert len(res.assignments) == 2 and len(res.uncovered) == 5


def test_locked_assignment_is_kept_and_conflicting_lock_is_reported():
    slots = demand.expand([req()], date(2026, 6, 1), date(2026, 6, 2), set())
    k = slots[0].key
    _, res = run([req()], [emp(1), emp(2)], date(2026, 6, 1), date(2026, 6, 2), locked={k: 2})
    assert (2, slots[0]) in res.assignments
    # lock an employee who is on leave that day → refuse, never silently drop the lock
    e2 = emp(2, absences=[(date(2026, 6, 1), date(2026, 6, 1), "sick")])
    _, res = run([req()], [emp(1), e2], date(2026, 6, 1), date(2026, 6, 2), locked={k: 2})
    assert res.status == "locked_conflict"
    assert res.locked_violations[0].code == "ABSENCE"


def test_fixed_work_outside_scope_constrains_rest():
    slots = demand.expand([req(start=time(8), end=time(16))], date(2026, 6, 2), date(2026, 6, 2), set())
    fw = FixedWork(1, slots[0].start - timedelta(hours=14), slots[0].start - timedelta(hours=6), date(2026, 6, 1), "prev")
    _, res = run([req()], [emp(1), emp(2)], date(2026, 6, 2), date(2026, 6, 2), fixed_work=[fw])
    assert res.assignments[0][0] == 2


def test_consecutive_days_limit():
    e1 = emp(1, max_consecutive_days=3)
    _, res = run([req()], [e1], date(2026, 6, 1), date(2026, 6, 7))
    # best pattern is 3 on / 1 off / 3 on = 6 shifts; the validator in run() enforces ≤3 consecutive
    assert len(res.assignments) == 6
    days = sorted(s.service_date for _, s in res.assignments)
    assert date(2026, 6, 4) not in days
    assert res.proven_max_coverage


def test_night_fairness_spreads_nights():
    reqs = [req(1, start=time(22), end=time(6)), req(2, start=time(8), end=time(16))]
    emps = [emp(i, contract_weekly_hours=28) for i in (1, 2, 3, 4)]
    _, res = run(reqs, emps, date(2026, 6, 1), date(2026, 6, 14),
                 weights=Weights(cost=0, night_fairness=10, continuity=0, balance=0), time_limit_s=20)
    nights = {e: 0 for e in (1, 2, 3, 4)}
    for e, s in res.assignments:
        nights[e] += int(s.is_night)
    assert max(nights.values()) - min(nights.values()) <= 1


def test_stability_keeps_previous_assignments():
    reqs = [req()]
    slots = demand.expand(reqs, date(2026, 6, 1), date(2026, 6, 5), set())
    prev = {s.key: 2 for s in slots}
    _, res = run(reqs, [emp(1), emp(2)], date(2026, 6, 1), date(2026, 6, 5), previous=prev,
                 weights=Weights(cost=0, balance=0, stability=10))
    assert all(e == 2 for e, _ in res.assignments)


def test_validator_detects_violations_directly():
    slots = demand.expand([req(1, start=time(8), end=time(16)), req(2, start=time(12), end=time(20))],
                          date(2026, 6, 1), date(2026, 6, 1), set())
    e = {1: emp(1)}
    codes = {v.code for v in rules.validate([(1, slots[0]), (1, slots[1])], e, SITES)}
    assert "OVERLAP" in codes
    codes = {v.code for v in rules.validate([(1, slots[0]), (2, slots[0])], {1: emp(1), 2: emp(2)}, SITES)}
    assert "DOUBLE_BOOKED" in codes


def test_greedy_hint_respects_hard_rules():
    reqs = [req(1, start=time(0), end=time(0), shift_hours=8, headcount=2), req(2, site=2, start=time(18), end=time(2))]
    emps = [emp(i, max_hours_week=40, max_consecutive_days=5, site_rules={2: "eligible"} if i % 2 else {},
                absences=[(date(2026, 3, 10), date(2026, 3, 12), "leave")] if i == 3 else []) for i in range(1, 12)]
    slots = demand.expand(reqs, date(2026, 3, 1), date(2026, 3, 31), set())
    p = Problem(slots=slots, employees=emps, sites=SITES, period_start=date(2026, 3, 1), period_end=date(2026, 3, 31))
    elig, _ = solver._eligible_pairs(p)
    hint = solver.greedy_hint(p, elig)
    by_key = {s.key: s for s in slots}
    pairs = [(e, by_key[k]) for k, e in hint.items()]
    assert pairs and rules.validate(pairs, {e.id: e for e in emps}, SITES) == []


def test_full_coverage_reported_as_proven():
    _, res = run([req(headcount=2)], [emp(i) for i in range(1, 5)], date(2026, 6, 1), date(2026, 6, 7))
    assert res.status == "complete" and res.coverage_status == "OPTIMAL"


def test_symmetry_breaking_does_not_conflict_with_locked_position():
    # lock position 2 while position 1 cannot be covered by anyone else → must stay feasible (partial)
    slots = demand.expand([req(headcount=2)], date(2026, 6, 1), date(2026, 6, 1), set())
    pos2 = [s for s in slots if s.position == 2][0]
    _, res = run([req(headcount=2)], [emp(1)], date(2026, 6, 1), date(2026, 6, 1), locked={pos2.key: 1})
    assert res.status == "partial"
    assert [(e, s.key) for e, s in res.assignments] == [(1, pos2.key)]
