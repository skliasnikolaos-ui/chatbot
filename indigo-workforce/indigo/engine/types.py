"""Pure data types for the scheduling engine (no database dependency)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Optional


@dataclass(frozen=True)
class RequirementSpec:
    id: int
    site_id: int
    name: str
    weekdays: frozenset[int]  # 0=Monday … 6=Sunday
    start_time: time
    end_time: time  # end <= start means the window ends the next day; equal = 24h
    headcount: int
    shift_hours: Optional[float] = None  # split the window into shifts of this length (wall clock)
    holiday_mode: str = "normal"  # normal | skip | sunday_rule
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    qualifications: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Slot:
    """One position to be staffed: requirement × local date × shift segment × position."""

    key: str
    requirement_id: int
    site_id: int
    service_date: date
    segment: int
    position: int
    start: datetime  # aware UTC
    end: datetime
    qualifications: frozenset[int]
    night_minutes: int = 0
    sunday_or_holiday_minutes: int = 0
    is_weekend: bool = False

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    @property
    def is_night(self) -> bool:
        return self.night_minutes >= 180

    @property
    def shift_key(self) -> str:
        """Identifier of the shift (all positions of one segment share it)."""
        return f"{self.requirement_id}|{self.service_date.isoformat()}|{self.segment}"


@dataclass(frozen=True)
class SiteSpec:
    id: int
    name: str
    requires_explicit_eligibility: bool = False


@dataclass
class EmployeeSpec:
    id: int
    name: str
    hire_date: Optional[date] = None
    termination_date: Optional[date] = None
    # qualification_id -> (valid_from, valid_until) ; None means open-ended
    qualifications: dict[int, tuple[Optional[date], Optional[date]]] = field(default_factory=dict)
    # approved absences / unavailable periods: (start_date, end_date inclusive, kind)
    absences: list[tuple[date, date, str]] = field(default_factory=list)
    # recurring weekly unavailability, local time: (weekday, start, end); end<=start → overnight
    weekly_unavailability: list[tuple[int, time, time]] = field(default_factory=list)
    # site_id -> "eligible" | "prohibited" | "preferred"
    site_rules: dict[int, str] = field(default_factory=dict)
    max_hours_week: Optional[float] = 48.0
    max_hours_month: Optional[float] = None
    max_shift_hours: Optional[float] = 12.0
    min_rest_hours: float = 11.0
    max_consecutive_days: Optional[int] = 6
    contract_weekly_hours: float = 40.0
    is_relief: bool = False
    night_preference: str = "neutral"  # avoid | neutral | prefer
    avoid_weekends: bool = False
    # requirement_id -> weekdays on which this employee is the fixed holder of that post
    fixed_posts: dict[int, frozenset[int]] = field(default_factory=dict)


@dataclass(frozen=True)
class FixedWork:
    """Work outside the optimisation scope that constrains rest/limits (kept as is)."""

    employee_id: int
    start: datetime
    end: datetime
    service_date: date
    label: str = ""


@dataclass
class Weights:
    """Soft-objective priorities, each 0 (ignore) … 10 (very important).

    Coverage is always the first, lexicographic priority and is not a weight.
    """

    cost: int = 5
    night_fairness: int = 5
    weekend_fairness: int = 5
    preferences: int = 5
    continuity: int = 5
    balance: int = 5
    stability: int = 5  # minimal changes against the published schedule
    fixed_posts: int = 8
    prefer_regular_staff: int = 3

    # internal unit per objective ("points" ≈ €1 of cost); shown on the setup screen
    UNITS = {
        "cost": ("ανά €1 κόστους", 1),
        "night_fairness": ("ανά βάρδια διαφοράς μέγιστου-ελάχιστου νυχτερινών", 20),
        "weekend_fairness": ("ανά βάρδια διαφοράς μέγιστου-ελάχιστου Σ/Κ", 20),
        "preferences": ("ανά βάρδια αντίθετη σε προτίμηση", 15),
        "continuity": ("ανά επιπλέον εργαζόμενο σε κάθε θέση", 10),
        "balance": ("ανά ώρα απόκλισης από τις συμβατικές ώρες", 2),
        "stability": ("ανά βάρδια που αλλάζει σε σχέση με το δημοσιευμένο", 30),
        "fixed_posts": ("ανά βάρδια σταθερού εργαζομένου που δόθηκε σε άλλον", 50),
        "prefer_regular_staff": ("ανά ώρα εργαζομένου αναπλήρωσης (relief)", 1),
    }

    def coefficient(self, name: str) -> int:
        return int(getattr(self, name)) * self.UNITS[name][1]

    def as_dict(self) -> dict[str, int]:
        return {k: int(getattr(self, k)) for k in self.UNITS}


@dataclass
class Problem:
    slots: list[Slot]
    employees: list[EmployeeSpec]
    sites: dict[int, SiteSpec]
    period_start: date
    period_end: date  # inclusive
    fixed_work: list[FixedWork] = field(default_factory=list)
    locked: dict[str, int] = field(default_factory=dict)  # slot key -> employee id
    previous: dict[str, int] = field(default_factory=dict)  # slot key -> employee id (published)
    cost_cents: dict[tuple[int, str], int] = field(default_factory=dict)  # (employee, slot key) -> cents
    weights: Weights = field(default_factory=Weights)
    time_limit_s: float = 30.0
    workers: int = 4
    seed: int = 7
