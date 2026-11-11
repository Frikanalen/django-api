"""Broadcast time: wall-clock Oslo times to instants, and week boundaries.

Pure clock arithmetic -- no database.
"""

import datetime
from datetime import UTC
from zoneinfo import ZoneInfo

from django.utils import timezone

from scheduling import broadcast_time

OSLO = ZoneInfo("Europe/Oslo")

# A Thursday. Its broadcast week starts Monday 2019-06-24.
NOW = datetime.datetime(2019, 6, 27, 12, tzinfo=OSLO)
WEEK_START = datetime.datetime(2019, 6, 24, tzinfo=OSLO)


def test_a_week_runs_monday_to_monday_local_time() -> None:
    assert broadcast_time.week_start(NOW) == WEEK_START
    monday_early = datetime.datetime(2019, 6, 24, 0, 0, 1, tzinfo=OSLO)
    assert broadcast_time.week_start(monday_early) == WEEK_START
    sunday_late = datetime.datetime(2019, 6, 30, 23, 59, tzinfo=OSLO)
    assert broadcast_time.week_start(sunday_late) == WEEK_START


def test_week_boundaries_ignore_the_active_timezone() -> None:
    """fkweb.middleware forces UTC for /api/ requests. A UTC Sunday 23:30
    is already Monday in Oslo, and Oslo is what counts."""
    utc_sunday_night = datetime.datetime(2019, 6, 30, 22, 30, tzinfo=UTC)
    with timezone.override(UTC):
        assert broadcast_time.week_start(utc_sunday_night) == datetime.datetime(
            2019, 7, 1, tzinfo=OSLO
        )


def test_grants_lock_the_monday_before_their_week_airs() -> None:
    thursday = datetime.datetime(2019, 7, 4, 19, tzinfo=OSLO)
    assert broadcast_time.default_locks_at(thursday) == WEEK_START

    monday_midnight = datetime.datetime(2019, 7, 1, tzinfo=OSLO)
    assert broadcast_time.default_locks_at(monday_midnight) == WEEK_START


def test_the_deadline_is_always_seven_to_thirteen_days_ahead() -> None:
    for hours in range(0, 7 * 24, 5):
        start = datetime.datetime(2019, 7, 1, tzinfo=OSLO) + datetime.timedelta(hours=hours)
        lead = start - broadcast_time.default_locks_at(start)
        assert datetime.timedelta(days=7) <= lead < datetime.timedelta(days=14)


def test_grants_are_issued_to_the_end_of_the_week_after_next() -> None:
    assert broadcast_time.issue_horizon(NOW) == WEEK_START + datetime.timedelta(weeks=3)


def test_weeks_across_dst_end_on_local_midnight() -> None:
    # The week of 2019-10-27 (autumn transition) is 169 hours long.
    in_transition_week = datetime.datetime(2019, 10, 23, 12, tzinfo=OSLO)
    after = broadcast_time.weeks_after(in_transition_week, 1)
    assert after.astimezone(OSLO) == datetime.datetime(2019, 10, 28, tzinfo=OSLO)
    assert after - broadcast_time.week_start(in_transition_week) == datetime.timedelta(hours=169)


def test_local_instant_converts_wall_clock_oslo_times() -> None:
    winter = broadcast_time.local_instant(datetime.date(2019, 1, 10), datetime.time(19))
    summer = broadcast_time.local_instant(datetime.date(2019, 7, 10), datetime.time(19))
    assert winter == datetime.datetime(2019, 1, 10, 18, tzinfo=UTC)
    assert summer == datetime.datetime(2019, 7, 10, 17, tzinfo=UTC)


def test_a_time_skipped_by_spring_forward_moves_forward_by_the_gap() -> None:
    # 2019-03-31: 02:00 CET jumps to 03:00 CEST; 02:30 does not exist.
    instant = broadcast_time.local_instant(datetime.date(2019, 3, 31), datetime.time(2, 30))
    assert instant == datetime.datetime(2019, 3, 31, 1, 30, tzinfo=UTC)
    assert instant.astimezone(OSLO).time() == datetime.time(3, 30)


def test_a_time_repeated_by_fall_back_means_the_first() -> None:
    # 2019-10-27: 03:00 CEST falls back to 02:00 CET; 02:30 happens twice.
    instant = broadcast_time.local_instant(datetime.date(2019, 10, 27), datetime.time(2, 30))
    assert instant == datetime.datetime(2019, 10, 27, 0, 30, tzinfo=UTC)
