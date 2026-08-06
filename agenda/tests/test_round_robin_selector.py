"""
Unit tests for RoundRobinSelector: the draw policy on its own, with no
database and no clock. The integration tests in test_jukebox_fill.py
pin the same behavior end to end through `items_for_gap`.
"""

import datetime
from itertools import pairwise

from agenda.scheduling.jukebox import RoundRobinSelector
from fk.models import Video


def video(video_id: int, minutes: float = 10) -> Video:
    return Video(
        id=video_id,
        name=f"id:{video_id}",
        duration=datetime.timedelta(minutes=minutes),
    )


def minutes(n: float) -> datetime.timedelta:
    return datetime.timedelta(minutes=n)


def picks(selector: RoundRobinSelector, remaining: datetime.timedelta, n: int) -> list[int]:
    return [selector.pick(remaining).id for _ in range(n)]


def test_draws_in_the_order_given_and_cycles() -> None:
    selector = RoundRobinSelector([video(1), video(2), video(3)])

    assert picks(selector, minutes(60), 6) == [1, 2, 3, 1, 2, 3]


def test_no_video_repeats_until_the_rest_have_played() -> None:
    selector = RoundRobinSelector([video(1), video(2)])

    drawn = picks(selector, minutes(60), 10)

    assert all(a != b for a, b in pairwise(drawn))


def test_a_video_too_long_for_the_moment_keeps_its_place_in_line() -> None:
    selector = RoundRobinSelector([video(1, minutes=30), video(2, minutes=5)])

    assert selector.pick(minutes(10)).id == 2
    # With room again, the passed-over video goes first.
    assert selector.pick(minutes(60)).id == 1


def test_returns_none_when_nothing_fits() -> None:
    selector = RoundRobinSelector([video(1, minutes=30)])

    assert selector.pick(minutes(10)) is None


def test_recovers_after_a_pick_that_found_nothing() -> None:
    """A failed pick must not poison the next one: the pool tops back up."""
    selector = RoundRobinSelector([video(1, minutes=30)])

    assert selector.pick(minutes(10)) is None
    assert selector.pick(minutes(60)).id == 1


def test_an_empty_candidate_list_always_yields_none() -> None:
    selector = RoundRobinSelector([])

    assert selector.pick(minutes(60)) is None
    assert selector.pick(minutes(60)) is None
