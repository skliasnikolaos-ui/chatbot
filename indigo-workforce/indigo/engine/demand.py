"""Expand coverage requirements into concrete slots (independent of who works)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..timeutil import ATHENS, local_to_utc, split_minutes
from .types import RequirementSpec, Slot


def _applies_on(req: RequirementSpec, d: date, holidays: set[date]) -> bool:
    if req.valid_from and d < req.valid_from:
        return False
    if req.valid_to and d > req.valid_to:
        return False
    if d in holidays:
        if req.holiday_mode == "skip":
            return False
        if req.holiday_mode == "sunday_rule":
            return 6 in req.weekdays
    return d.weekday() in req.weekdays


def window_segments(req: RequirementSpec, d: date) -> list[tuple[datetime, datetime]]:
    """Split the requirement window for local date d into shift segments.

    Segment boundaries are local wall-clock times (shift changes happen at the
    same clock time even on DST days); durations are real elapsed time.
    """
    start_naive = datetime.combine(d, req.start_time)
    end_day = d if req.end_time > req.start_time else d + timedelta(days=1)
    end_naive = datetime.combine(end_day, req.end_time)
    bounds = [start_naive]
    if req.shift_hours:
        step = timedelta(hours=float(req.shift_hours))
        cur = start_naive + step
        while cur < end_naive:
            bounds.append(cur)
            cur += step
    bounds.append(end_naive)
    segs = []
    for a, b in zip(bounds, bounds[1:]):
        sa = local_to_utc(a.date(), a.time())
        sb = local_to_utc(b.date(), b.time())
        if sb > sa:
            segs.append((sa, sb))
    return segs


def expand(
    requirements: list[RequirementSpec],
    start: date,
    end: date,
    holidays: set[date],
    site_ids: set[int] | None = None,
    dates: set[date] | None = None,
) -> list[Slot]:
    slots: list[Slot] = []
    d = start
    while d <= end:
        if dates is None or d in dates:
            for req in requirements:
                if site_ids is not None and req.site_id not in site_ids:
                    continue
                if req.headcount <= 0 or not _applies_on(req, d, holidays):
                    continue
                for seg_no, (sa, sb) in enumerate(window_segments(req, d), start=1):
                    parts = split_minutes(sa, sb, holidays, ATHENS)
                    for pos in range(1, req.headcount + 1):
                        slots.append(
                            Slot(
                                key=f"{req.id}|{d.isoformat()}|{seg_no}|{pos}",
                                requirement_id=req.id,
                                site_id=req.site_id,
                                service_date=d,
                                segment=seg_no,
                                position=pos,
                                start=sa,
                                end=sb,
                                qualifications=req.qualifications,
                                night_minutes=parts["night"],
                                sunday_or_holiday_minutes=parts["sunday_or_holiday"],
                                is_weekend=d.weekday() >= 5 or d in holidays,
                            )
                        )
        d += timedelta(days=1)
    slots.sort(key=lambda s: (s.start, s.site_id, s.requirement_id, s.position))
    return slots
