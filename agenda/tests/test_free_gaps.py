"""
Unit tests for the jukebox's gap discovery, `free_gaps`.

These run against plain datetimes -- no database. The integration tests
in test_jukebox_fill.py pin the same whole-minute rules end to end; here
each boundary is exercised directly.
"""

import datetime
from zoneinfo import ZoneInfo

from agenda.scheduling.jukebox import Gap, free_gaps

OSLO = ZoneInfo("Europe/Oslo")
NOON = datetime.datetime(2019, 6, 30, 12, tzinfo=OSLO)


def at(minutes: float = 0, seconds: float = 0) -> datetime.datetime:
    return NOON + datetime.timedelta(minutes=minutes, seconds=seconds)


def gaps(start, end, occupied=()) -> list[Gap]:
    return list(free_gaps(start, end, occupied))


def test_an_empty_window_is_one_gap() -> None:
    assert gaps(NOON, at(minutes=60)) == [Gap(at(minutes=1), at(minutes=60))]


def test_the_window_start_rounds_forward_even_from_a_whole_minute() -> None:
    """Filling starts on the minute *after* the window opens -- 12:00 sharp
    included, which is what makes `next_whole_minute` not a true ceiling."""
    assert gaps(at(seconds=0), at(minutes=30))[0].start == at(minutes=1)
    assert gaps(at(seconds=37), at(minutes=30))[0].start == at(minutes=1)


def test_the_window_end_rounds_backward() -> None:
    assert gaps(NOON, at(minutes=30, seconds=59))[0].end == at(minutes=30)


def test_an_occupied_stretch_splits_the_window() -> None:
    occupied = [(at(minutes=20), at(minutes=30))]

    assert gaps(NOON, at(minutes=60), occupied) == [
        Gap(at(minutes=1), at(minutes=20)),
        Gap(at(minutes=31), at(minutes=60)),
    ]


def test_a_gap_ends_on_the_whole_minute_before_an_occupied_stretch() -> None:
    occupied = [(at(minutes=20, seconds=30), at(minutes=30))]

    assert gaps(NOON, at(minutes=60), occupied)[0].end == at(minutes=20)


def test_filling_resumes_on_the_whole_minute_after_an_occupied_stretch() -> None:
    occupied = [(at(minutes=20), at(minutes=29, seconds=1))]

    assert gaps(NOON, at(minutes=60), occupied)[1].start == at(minutes=30)


def test_a_gap_of_exactly_the_minimum_is_left_empty() -> None:
    """The boundary is exclusive: 300 seconds is too short, 360 is used."""
    assert gaps(NOON, at(minutes=6)) == []
    assert gaps(NOON, at(minutes=7)) == [Gap(at(minutes=1), at(minutes=7))]


def test_an_item_overrunning_from_before_the_window_pushes_the_first_gap_back() -> None:
    """The lead-in gap it leaves is negative-length and must not be yielded."""
    occupied = [(at(minutes=-50), at(minutes=10))]

    assert gaps(NOON, at(minutes=60), occupied) == [Gap(at(minutes=11), at(minutes=60))]


def test_an_item_ending_before_the_window_is_ignored() -> None:
    occupied = [(at(minutes=-10), at(minutes=-5))]

    assert gaps(NOON, at(minutes=60), occupied) == [Gap(at(minutes=1), at(minutes=60))]


def test_an_item_starting_after_the_window_is_ignored() -> None:
    occupied = [(at(minutes=70), at(minutes=80))]

    assert gaps(NOON, at(minutes=60), occupied) == [Gap(at(minutes=1), at(minutes=60))]


def test_back_to_back_occupied_stretches_leave_no_gap_between_them() -> None:
    occupied = [
        (at(minutes=10), at(minutes=20)),
        (at(minutes=20), at(minutes=30)),
    ]

    assert gaps(NOON, at(minutes=60), occupied) == [
        Gap(at(minutes=1), at(minutes=10)),
        Gap(at(minutes=31), at(minutes=60)),
    ]


def test_an_item_ending_exactly_at_a_resume_point_still_bounds_the_next_gap() -> None:
    """Strict comparison: an item ending exactly where filling would resume
    is not skipped as 'behind us'; it re-bounds the (then empty) gap."""
    occupied = [
        (at(minutes=10), at(minutes=20)),
        (at(minutes=20, seconds=30), at(minutes=21)),
    ]

    assert gaps(NOON, at(minutes=60), occupied) == [
        Gap(at(minutes=1), at(minutes=10)),
        Gap(at(minutes=22), at(minutes=60)),
    ]


def test_occupied_stretches_shorter_than_the_gap_minimum_still_split() -> None:
    """A one-minute item makes its own minute and both bounding minutes
    unusable, but the remaining sides fill normally."""
    occupied = [(at(minutes=30), at(minutes=31))]

    assert gaps(NOON, at(minutes=60), occupied) == [
        Gap(at(minutes=1), at(minutes=30)),
        Gap(at(minutes=32), at(minutes=60)),
    ]
