"""A five-field cron schedule: minute, hour, day of month, month, day of week.

Standard syntax: ``*``, numbers, ranges (``1-5``), steps (``*/15``,
``2-10/2``), comma lists, and 0 or 7 for Sunday. As in cron, with both day
fields restricted a date matches when either does. Times are wall-clock local.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

#: Label and allowed range per field, in expression order.
_FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of month", 1, 31),
    ("month", 1, 12),
    ("day of week", 0, 7),
)

#: How far next_run() searches before calling a schedule impossible. A Feb 29
#: date-and-weekday pair can sit 8 years apart.
_HORIZON = timedelta(days=366 * 9)


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    days_restricted: bool
    weekdays_restricted: bool


def parse(expr: str) -> Cron:
    """Raises ValueError naming the offending field on a bad expression."""
    parts = expr.split()
    if len(parts) != len(_FIELDS):
        raise ValueError("a schedule is 5 fields: minute hour day-of-month month day-of-week")
    minutes, hours, days, months, weekdays = (
        _field(part, label, low, high)
        for part, (label, low, high) in zip(parts, _FIELDS, strict=True)
    )
    _, _, day_part, _, weekday_part = parts
    return Cron(
        minutes=minutes,
        hours=hours,
        days=days,
        months=months,
        weekdays=frozenset(0 if day == 7 else day for day in weekdays),
        days_restricted=day_part != "*",
        weekdays_restricted=weekday_part != "*",
    )


def next_run(cron: Cron, after: datetime) -> datetime:
    """The first matching minute strictly after ``after``. Walks the calendar
    a day, then an hour, then a minute at a time."""
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = candidate + _HORIZON
    while candidate < limit:
        if candidate.month not in cron.months or not _day_matches(cron, candidate):
            candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
        elif candidate.hour not in cron.hours:
            candidate = (candidate + timedelta(hours=1)).replace(minute=0)
        elif candidate.minute not in cron.minutes:
            candidate += timedelta(minutes=1)
        else:
            return candidate
    raise ValueError("the schedule never matches a real date")


def _day_matches(cron: Cron, when: datetime) -> bool:
    in_days = when.day in cron.days
    # datetime counts Monday=0; cron counts Sunday=0.
    in_weekdays = (when.weekday() + 1) % 7 in cron.weekdays
    if cron.days_restricted and cron.weekdays_restricted:
        return in_days or in_weekdays
    # An unrestricted field is the full set, so it never vetoes.
    return in_days and in_weekdays


def _field(part: str, label: str, low: int, high: int) -> frozenset[int]:
    """The set of values one field covers ("*", "8", "1-5", "*/15", lists)."""
    values: set[int] = set()
    for piece in part.split(","):
        span, _, step_raw = piece.partition("/")
        step = _number(step_raw, label) if step_raw else 1
        if step < 1:
            raise ValueError(f"{label} step {step_raw!r} must be at least 1")
        if span == "*":
            first, last = low, high
        elif "-" in span:
            first_raw, _, last_raw = span.partition("-")
            first, last = _number(first_raw, label), _number(last_raw, label)
        else:
            # A bare number. With a step it opens a range, as Vixie cron does.
            first = _number(span, label)
            last = high if step_raw else first
        if not low <= first <= last <= high:
            raise ValueError(f"{label} {piece!r} is outside {low}-{high}")
        values.update(range(first, last + 1, step))
    return frozenset(values)


def _number(raw: str, label: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{label} has a non-number {raw!r}") from None
