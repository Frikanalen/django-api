"""Where entries play: programmes on the five-minute grid, posters right after."""

import datetime
from dataclasses import dataclass
from datetime import timedelta

from scheduling import layout
from scheduling.models import Poster

from .conftest import OSLO

START = datetime.datetime(2026, 1, 22, 19, tzinfo=OSLO)


@dataclass
class Clip:
    duration: timedelta


def poster(seconds: int) -> Poster:
    return Poster(heading="P", duration=timedelta(seconds=seconds))


def starts(entries, padded=True) -> list[str]:
    return [f"{p.start:%H:%M:%S}" for p in layout.lay_out(START, entries, padded=padded)]


def test_the_first_entry_starts_with_the_grant() -> None:
    assert starts([Clip(timedelta(minutes=3))]) == ["19:00:00"]


def test_a_programme_gets_a_breather_and_the_next_grid_point() -> None:
    clips = [
        Clip(timedelta(minutes=4, seconds=31)),
        Clip(timedelta(minutes=4)),
        Clip(timedelta(minutes=10)),
    ]
    # 19:04:31 + 30 s rounds to 19:10; 19:14:00 + 30 s rounds to 19:15.
    assert starts(clips) == ["19:00:00", "19:10:00", "19:15:00"]


def test_a_programme_ending_exactly_on_the_grid_still_gets_a_gap() -> None:
    assert starts([Clip(timedelta(minutes=5)), Clip(timedelta(minutes=5))]) == [
        "19:00:00",
        "19:10:00",
    ]


def test_posters_follow_directly_and_the_next_programme_takes_the_grid() -> None:
    entries = [Clip(timedelta(minutes=12)), poster(30), poster(20), Clip(timedelta(minutes=1))]
    assert starts(entries) == ["19:00:00", "19:12:00", "19:12:30", "19:15:00"]


def test_unpadded_lists_play_back_to_back() -> None:
    assert starts([Clip(timedelta(minutes=4)), Clip(timedelta(minutes=4))], padded=False) == [
        "19:00:00",
        "19:04:00",
    ]


def test_the_grid_is_wall_clock_even_when_the_grant_is_not_on_it() -> None:
    late = START + timedelta(minutes=2)
    placed = layout.lay_out(
        late, [Clip(timedelta(minutes=1)), Clip(timedelta(minutes=1))], padded=True
    )
    assert [f"{p.start:%H:%M}" for p in placed] == ["19:02", "19:05"]
