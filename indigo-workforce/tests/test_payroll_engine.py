"""Payroll engine examples. Every expected figure is computed by hand in the
comment next to it (independently of the engine code)."""
from datetime import date, time
from decimal import Decimal as D

from indigo.payroll_engine import DEFAULT_RULES, Compensation, WorkInterval, calculate, shift_cost_cents
from indigo.timeutil import local_to_utc

RULES = dict(DEFAULT_RULES)


def iv(d0, t0, d1, t1, site=1):
    return WorkInterval(local_to_utc(d0, t0), local_to_utc(d1, t1), site)


def hourly(rate="10", weekly="40", **kw):
    return Compensation("hourly", hourly_rate=D(rate), weekly_hours=D(weekly), **kw)


def amt(res, cat):
    return sum((ln.amount for ln in res.lines if ln.category == cat), D("0"))


def test_regular_week_and_employer_cost():
    ivs = [iv(date(2026, 6, d), time(8), date(2026, 6, d), time(16)) for d in range(1, 6)]
    r = calculate(1, 2026, 6, hourly(), RULES, ivs, set())
    # 40h × 10.00 = 400.00 ; employer 21.79% = 87.16 ; employee 13.37% = 53.48
    assert r.gross == D("400.00")
    assert r.employer_contributions == D("87.16")
    assert r.employee_contributions == D("53.48")
    assert r.employer_cost == D("487.16")


def test_overwork_and_overtime_categories():
    ivs = [iv(date(2026, 6, d), time(8), date(2026, 6, d), time(16)) for d in range(1, 6)]
    ivs.append(iv(date(2026, 6, 6), time(8), date(2026, 6, 6), time(14)))  # Saturday +6h → week = 46h
    r = calculate(1, 2026, 6, hourly(), RULES, ivs, set())
    # regular 40h = 400 ; overwork 5h base 50 + 20% = 10 ; overtime 1h base 10 + 40% = 4 → 474
    assert amt(r, "regular") == D("400.00")
    assert amt(r, "overwork") == D("50.00") and amt(r, "overwork_premium") == D("10.00")
    assert amt(r, "overtime") == D("10.00") and amt(r, "overtime_premium") == D("4.00")
    assert r.gross == D("474.00")


def test_sunday_night_shift_crossing_week():
    r = calculate(1, 2026, 6, hourly(), RULES, [iv(date(2026, 6, 7), time(22), date(2026, 6, 8), time(6))], set())
    # 8h × 10 = 80 ; night 8h × 10 × 25% = 20 ; Sunday 2h (22-24) × 10 × 75% = 15 → 115
    assert amt(r, "night_premium") == D("20.00")
    assert amt(r, "sunday_holiday_premium") == D("15.00")
    assert r.gross == D("115.00")
    assert r.employer_contributions == D("25.06")  # 115 × 21.79% = 25.0585 → 25.06


def test_public_holiday_premium():
    hol = {date(2026, 8, 15)}  # Saturday
    r = calculate(1, 2026, 8, hourly(), RULES, [iv(date(2026, 8, 15), time(8), date(2026, 8, 15), time(16))], hol)
    # 80 + 8 × 10 × 75% = 60 → 140
    assert r.gross == D("140.00")


def test_month_boundary_split():
    shift = [iv(date(2026, 6, 30), time(20), date(2026, 7, 1), time(8))]
    june = calculate(1, 2026, 6, hourly(), RULES, shift, set())
    july = calculate(1, 2026, 7, hourly(), RULES, shift, set())
    # June: 4h = 40 + night 2h × 2.5 = 5 → 45 ; July: 8h = 80 + night 6h × 2.5 = 15 → 95
    assert june.gross == D("45.00")
    assert july.gross == D("95.00")


def test_dst_fall_back_night_shift():
    # 2026-10-24 (Sat) 22:00 → 2026-10-25 (Sun) 06:00 lasts 9 real hours
    r = calculate(1, 2026, 10, hourly(), RULES, [iv(date(2026, 10, 24), time(22), date(2026, 10, 25), time(6))], set())
    # regular 9h = 90 ; night 9h × 2.5 = 22.5 ; Sunday 00-06 local = 7 real h × 7.5 = 52.5 → 165
    assert r.minutes["total"] == 540
    assert r.gross == D("165.00")


def test_part_time_extra_hours():
    ivs = [iv(date(2026, 6, d), time(8), date(2026, 6, d), time(13)) for d in range(1, 6)]  # 25h
    r = calculate(1, 2026, 6, hourly("8", "20"), RULES, ivs, set())
    # regular 20h × 8 = 160 ; part-time extra 5h × 8 = 40 + 12% = 4.80 → 204.80
    assert amt(r, "part_time_extra_premium") == D("4.80")
    assert r.gross == D("204.80")


def test_contract_cannot_undercut_statutory_premium():
    shift = [iv(date(2026, 6, 2), time(22), date(2026, 6, 3), time(6))]
    low = calculate(1, 2026, 6, hourly(night_premium_pct=D("20")), RULES, shift, set())
    high = calculate(1, 2026, 6, hourly(night_premium_pct=D("50")), RULES, shift, set())
    assert amt(low, "night_premium") == D("20.00")  # 25% statutory applied: 8 × 10 × 25%
    assert low.warnings and "κάτω από το ελάχιστο" in low.warnings[0]
    assert amt(high, "night_premium") == D("40.00")  # contractual 50% is more favourable


def test_monthly_salary_proration_and_extra_hours():
    comp = Compensation("monthly", monthly_salary=D("1000"), weekly_hours=D("40"))
    # hired on 16 June → 15/30 days → 500.00 ; no worked hours
    r = calculate(1, 2026, 6, comp, RULES, [], set(), employed_from=date(2026, 6, 16))
    assert amt(r, "base_salary") == D("500.00")
    # night hours premium on hourly equivalent: 1000 / 166.6667 = 5.99999880 ; 8h × 25% → 11.99999 → 12.00
    r = calculate(1, 2026, 6, comp, RULES, [iv(date(2026, 6, 2), time(22), date(2026, 6, 3), time(6))], set())
    assert amt(r, "night_premium") == D("12.00")
    assert r.gross == D("1012.00")


def test_half_up_rounding():
    # 30 min × 10.01 = 5.005 → 5.01 with HALF_UP (banker's rounding would give 5.00)
    r = calculate(1, 2026, 6, hourly("10.01"), RULES, [iv(date(2026, 6, 2), time(10), date(2026, 6, 2), time(10, 30))], set())
    assert amt(r, "regular") == D("5.01")


def test_weekly_categories_use_previous_month_days_in_same_week():
    # Week of Mon 29 Jun – Sun 5 Jul 2026: 40h in June (29,30 Jun × 10h + 1–2 Jul × 10h) then Fri 3 Jul 8h
    ivs = [iv(date(2026, 6, 29), time(8), date(2026, 6, 29), time(18)),
           iv(date(2026, 6, 30), time(8), date(2026, 6, 30), time(18)),
           iv(date(2026, 7, 1), time(8), date(2026, 7, 1), time(18)),
           iv(date(2026, 7, 2), time(8), date(2026, 7, 2), time(18)),
           iv(date(2026, 7, 3), time(8), date(2026, 7, 3), time(16))]
    r = calculate(1, 2026, 7, hourly(), RULES, ivs, set())
    # July pays 28h: 20 regular (1–2 Jul) + Fri: 5h overwork + 3h overtime
    assert r.minutes["regular"] == 20 * 60 and r.minutes["overwork"] == 300 and r.minutes["overtime"] == 180


def test_site_cost_allocation_sums_to_total():
    ivs = [iv(date(2026, 6, 1), time(8), date(2026, 6, 1), time(16), site=1),
           iv(date(2026, 6, 2), time(8), date(2026, 6, 2), time(12), site=2)]
    r = calculate(1, 2026, 6, hourly("9.33"), RULES, ivs, set())
    sc = r.site_costs()
    assert sum(sc.values()) == r.employer_cost
    assert sc[1] > sc[2]


def test_shift_cost_estimate():
    # 8h night shift, €10/h: gross 80 + 20 = 100 → × 1.2179 = 121.79 → 12179 cents
    assert shift_cost_cents(hourly(), RULES, 480, 480, 0) == 12179


def test_no_compensation_terms():
    r = calculate(1, 2026, 6, None, RULES, [], set())
    assert r.gross == 0 and r.warnings
