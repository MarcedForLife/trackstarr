"""The cron schedule parser and its next-run walk. Pure datetime, no media."""

from datetime import datetime

import pytest

from trackstarr.cron import next_run, parse


def runs_at(expr: str, after: datetime) -> datetime:
    return next_run(parse(expr), after)


def test_daily_schedule():
    assert runs_at("0 4 * * *", datetime(2026, 8, 13, 1, 0)) == datetime(2026, 8, 13, 4, 0)
    assert runs_at("0 4 * * *", datetime(2026, 8, 13, 5, 0)) == datetime(2026, 8, 14, 4, 0)


def test_next_run_is_strictly_after():
    """An exact hit rolls forward, so rescheduling at fire time never
    returns the run that just happened."""
    assert runs_at("0 4 * * *", datetime(2026, 8, 13, 4, 0)) == datetime(2026, 8, 14, 4, 0)


def test_minute_steps():
    assert runs_at("*/15 * * * *", datetime(2026, 8, 13, 4, 16)) == datetime(2026, 8, 13, 4, 30)


def test_lists_and_ranges():
    schedule = parse("0 8,20 * * 1-5")
    assert next_run(schedule, datetime(2026, 8, 14, 9, 0)) == datetime(2026, 8, 14, 20, 0)
    # 2026-08-14 is a Friday; the weekday range skips the weekend.
    assert next_run(schedule, datetime(2026, 8, 14, 21, 0)) == datetime(2026, 8, 17, 8, 0)


def test_month_and_day_rollover():
    assert runs_at("30 2 1 * *", datetime(2026, 8, 13, 0, 0)) == datetime(2026, 9, 1, 2, 30)


def test_sunday_is_zero_and_seven():
    # 2026-08-16 is a Sunday.
    for sunday in ("0", "7"):
        assert runs_at(f"0 9 * * {sunday}", datetime(2026, 8, 13, 0, 0)) == datetime(
            2026, 8, 16, 9, 0
        )


def test_restricted_day_fields_match_either():
    """Cron's day rule: day-of-month 13 plus weekday Friday fires on both,
    not only on Friday the 13th."""
    schedule = parse("0 0 13 * 5")
    assert next_run(schedule, datetime(2026, 11, 1, 0, 0)) == datetime(2026, 11, 6, 0, 0)
    assert next_run(schedule, datetime(2026, 11, 7, 0, 0)) == datetime(2026, 11, 13, 0, 0)
    assert next_run(schedule, datetime(2026, 11, 14, 0, 0)) == datetime(2026, 11, 20, 0, 0)


def test_impossible_date_is_refused():
    with pytest.raises(ValueError, match="never matches"):
        runs_at("0 0 30 2 *", datetime(2026, 8, 13, 0, 0))


@pytest.mark.parametrize(
    "expr",
    [
        "0 4 * *",  # four fields
        "0 4 * * * *",  # six fields
        "60 4 * * *",  # minute out of range
        "0 24 * * *",  # hour out of range
        "0 4 0 * *",  # day of month starts at 1
        "0 4 * * mon",  # names unsupported
        "5-1 * * * *",  # reversed range
        "*/0 * * * *",  # zero step
        "04:00",  # the old SWEEP_AT format
    ],
)
def test_bad_expressions_are_refused(expr):
    with pytest.raises(ValueError):
        parse(expr)
