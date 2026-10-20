"""Time handling. All instants are stored as aware UTC datetimes; local wall
times are interpreted in Europe/Athens (DST-aware via zoneinfo).

Policy for DST edge cases (documented in docs/ARCHITECTURE.md):
* A local wall time that does not exist (spring-forward gap 03:00-04:00) is
  resolved with fold=0, i.e. shifted forward by one hour.
* A local wall time that occurs twice (fall-back 03:00-04:00) resolves to its
  first occurrence (fold=0).
* Durations are always computed on real elapsed time (UTC), so a 22:00-06:00
  shift lasts 7h on the spring-forward night and 9h on the fall-back night.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ATHENS = ZoneInfo("Europe/Athens")
UTC = timezone.utc

NIGHT_START = time(22, 0)
NIGHT_END = time(6, 0)


def local_to_utc(d: date, t: time, tz: ZoneInfo = ATHENS) -> datetime:
    return datetime.combine(d, t).replace(tzinfo=tz, fold=0).astimezone(UTC)


def to_local(dt: datetime, tz: ZoneInfo = ATHENS) -> datetime:
    return dt.astimezone(tz)


def minutes_between(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() // 60)


def overlap_minutes(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> int:
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0, minutes_between(lo, hi)) if hi > lo else 0


def day_bounds_utc(d: date, tz: ZoneInfo = ATHENS) -> tuple[datetime, datetime]:
    """UTC instants of local midnight at the start and end of local date d."""
    return local_to_utc(d, time(0)), local_to_utc(d + timedelta(days=1), time(0))


def local_dates_touched(start: datetime, end: datetime, tz: ZoneInfo = ATHENS) -> list[date]:
    d0 = to_local(start, tz).date()
    d1 = to_local(end - timedelta(microseconds=1), tz).date() if end > start else d0
    out, d = [], d0
    while d <= d1:
        out.append(d)
        d += timedelta(days=1)
    return out


def split_minutes(start: datetime, end: datetime, holidays: set[date], tz: ZoneInfo = ATHENS) -> dict:
    """Break an interval into total / night / Sunday / holiday minutes.

    Night = local 22:00-06:00. Sunday and holiday minutes are counted per local
    calendar day; `sunday_or_holiday` avoids double counting a holiday that
    falls on a Sunday. Also returns minutes per local date.
    """
    total = minutes_between(start, end)
    night = sunday = holiday = sun_or_hol = 0
    per_date: dict[date, int] = {}
    for d in local_dates_touched(start, end, tz):
        b0, b1 = day_bounds_utc(d, tz)
        m = overlap_minutes(start, end, b0, b1)
        if not m:
            continue
        per_date[d] = m
        is_sun = d.weekday() == 6
        is_hol = d in holidays
        sunday += m if is_sun else 0
        holiday += m if is_hol else 0
        sun_or_hol += m if (is_sun or is_hol) else 0
        # night windows that intersect this day: [d 00:00, d 06:00) and [d 22:00, d+1 00:00)
        n0 = overlap_minutes(start, end, b0, local_to_utc(d, NIGHT_END, tz))
        n1 = overlap_minutes(start, end, local_to_utc(d, NIGHT_START, tz), b1)
        night += n0 + n1
    return {
        "total": total,
        "night": night,
        "sunday": sunday,
        "holiday": holiday,
        "sunday_or_holiday": sun_or_hol,
        "per_date": per_date,
    }


def iso_week_key(d: date) -> tuple[int, int]:
    y, w, _ = d.isocalendar()
    return (y, w)


def month_dates(year: int, month: int) -> list[date]:
    d = date(year, month, 1)
    out = []
    while d.month == month:
        out.append(d)
        d += timedelta(days=1)
    return out


def fmt_local(dt: datetime | None, fmt: str = "%d/%m %H:%M") -> str:
    return to_local(dt).strftime(fmt) if dt else ""
