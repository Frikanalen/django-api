"""
The broadcast-week lifecycle: drafted two Mondays ahead, open for one
week, frozen from the Monday before airing.

Pure clock arithmetic -- no database. The enforcement of these
boundaries is tested where it lives: the schedule API tests for member
edits, the agenda tests for the nightly fillers.
"""

import datetime
from zoneinfo import ZoneInfo

from agenda.scheduling import policy

OSLO = ZoneInfo("Europe/Oslo")

# A Thursday. Its broadcast week starts Monday 2019-06-24.
NOW = datetime.datetime(2019, 6, 27, 12, tzinfo=OSLO)
WEEK_START = datetime.datetime(2019, 6, 24, tzinfo=OSLO)


def test_a_week_runs_monday_to_monday_local_time() -> None:
    assert policy.week_start(NOW) == WEEK_START
    monday_early = datetime.datetime(2019, 6, 24, 0, 0, 1, tzinfo=OSLO)
    assert policy.week_start(monday_early) == WEEK_START
    sunday_late = datetime.datetime(2019, 6, 30, 23, 59, tzinfo=OSLO)
    assert policy.week_start(sunday_late) == WEEK_START


def test_the_current_and_next_week_are_frozen() -> None:
    assert policy.freeze_boundary(NOW) == WEEK_START + datetime.timedelta(weeks=2)

    in_this_week = NOW + datetime.timedelta(days=1)
    in_next_week = datetime.datetime(2019, 7, 3, tzinfo=OSLO)
    in_open_week = datetime.datetime(2019, 7, 10, tzinfo=OSLO)
    assert policy.is_frozen(in_this_week, now=NOW)
    assert policy.is_frozen(in_next_week, now=NOW)
    assert not policy.is_frozen(in_open_week, now=NOW)


def test_the_freeze_boundary_is_inclusive_of_the_open_monday() -> None:
    """An item starting exactly at Monday 00:00 of the open week is editable."""
    boundary = policy.freeze_boundary(NOW)

    assert not policy.is_frozen(boundary, now=NOW)
    assert policy.is_frozen(boundary - datetime.timedelta(seconds=1), now=NOW)


def test_a_week_freezes_the_moment_the_preceding_monday_arrives() -> None:
    """Sunday 23:59 the week after next is open; one minute later it is not."""
    airtime = datetime.datetime(2019, 7, 8, 12, tzinfo=OSLO)
    sunday_night = datetime.datetime(2019, 6, 30, 23, 59, tzinfo=OSLO)
    monday_morning = datetime.datetime(2019, 7, 1, 0, 0, tzinfo=OSLO)

    assert not policy.is_frozen(airtime, now=sunday_night)
    assert policy.is_frozen(airtime, now=monday_morning)


def test_the_horizon_is_the_end_of_the_open_week() -> None:
    assert policy.scheduling_horizon(NOW) == WEEK_START + datetime.timedelta(weeks=3)


def test_boundaries_land_on_local_midnight_across_a_dst_transition() -> None:
    """Oslo leaves DST on 2019-10-27; a naive `+ 14 days` on an aware
    datetime would land on 23:00. The boundary must be a true local
    midnight in the new offset."""
    now = datetime.datetime(2019, 10, 16, 12, tzinfo=OSLO)  # Wednesday, CEST

    boundary = policy.freeze_boundary(now)

    assert boundary == datetime.datetime(2019, 10, 28, 0, 0, tzinfo=OSLO)
    assert boundary.utcoffset() == datetime.timedelta(hours=1)
    assert now.utcoffset() == datetime.timedelta(hours=2)


def test_the_freeze_message_names_the_open_monday() -> None:
    assert "2019-07-08" in policy.freeze_message(policy.freeze_boundary(NOW))
