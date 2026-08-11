"""
Unit tests for the jukebox's scoring rules and weighted draw.

Everything here runs on unsaved Video instances and a hand-built
ScheduleContext -- no database. test_jukebox_fill.py covers the wiring
into fill_agenda_with_jukebox, including that the context seeds from
the schedule already in the window.
"""

import datetime
import random
from itertools import pairwise
from zoneinfo import ZoneInfo

from agenda.scheduling.selection import (
    Freshness,
    OrganizationDiversity,
    RepeatAvoidance,
    ScheduleContext,
    WeightedSelector,
)
from fk.models import Video

OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime.datetime(2019, 6, 30, 12, tzinfo=OSLO)


def video(
    video_id: int,
    minutes: float = 10,
    organization_id: int | None = None,
    uploaded_days_ago: float | None = None,
) -> Video:
    uploaded_time = None
    if uploaded_days_ago is not None:
        uploaded_time = NOW - datetime.timedelta(days=uploaded_days_ago)
    return Video(
        id=video_id,
        name=f"id:{video_id}",
        duration=datetime.timedelta(minutes=minutes),
        organization_id=organization_id,
        uploaded_time=uploaded_time,
    )


def minutes(n: float) -> datetime.timedelta:
    return datetime.timedelta(minutes=n)


# Freshness scores a video on its age alone. The context is part of the
# rule protocol but goes unread, so these tests all share one empty.
NO_CONTEXT = ScheduleContext()


# --- Freshness --------------------------------------------------------------


def test_a_newer_upload_outweighs_an_older_one() -> None:
    rule = Freshness(now=NOW)

    fresh = rule.weight(video(1, uploaded_days_ago=7), NO_CONTEXT)
    stale = rule.weight(video(2, uploaded_days_ago=3000), NO_CONTEXT)

    assert fresh > stale


def test_freshness_halves_per_half_life() -> None:
    rule = Freshness(now=NOW, half_life=datetime.timedelta(days=100), floor=0.0)

    assert rule.weight(video(1, uploaded_days_ago=0), NO_CONTEXT) == 1.0
    assert rule.weight(video(1, uploaded_days_ago=100), NO_CONTEXT) == 0.5
    assert rule.weight(video(1, uploaded_days_ago=200), NO_CONTEXT) == 0.25


def test_ancient_and_undated_material_keeps_the_floor_weight() -> None:
    """Old videos are downweighted, never frozen out."""
    rule = Freshness(now=NOW, floor=0.2)

    assert rule.weight(video(1, uploaded_days_ago=100_000), NO_CONTEXT) > 0.2 * 0.999
    assert rule.weight(video(1, uploaded_days_ago=None), NO_CONTEXT) == 0.2


def test_an_upload_dated_in_the_future_counts_as_brand_new() -> None:
    rule = Freshness(now=NOW)

    assert rule.weight(video(1, uploaded_days_ago=-1), NO_CONTEXT) == 1.0


# --- OrganizationDiversity --------------------------------------------------


def context_with_airtime(*org_minutes: tuple[int, float]) -> ScheduleContext:
    context = ScheduleContext()
    for i, (org_id, mins) in enumerate(org_minutes):
        context.record(video(1000 + i, minutes=mins, organization_id=org_id))
    return context


def test_the_dominant_organizations_videos_weigh_less() -> None:
    rule = OrganizationDiversity()
    context = context_with_airtime((1, 45), (2, 15))

    dominant = rule.weight(video(1, organization_id=1), context)
    minority = rule.weight(video(2, organization_id=2), context)

    assert dominant < minority


def test_an_organization_with_no_airtime_yet_is_unpenalized() -> None:
    rule = OrganizationDiversity()
    context = context_with_airtime((1, 45))

    assert rule.weight(video(2, organization_id=2), context) == 1.0


def test_diversity_never_zeroes_a_candidate() -> None:
    """A sole organization holds a share of 1.0; the floor keeps this a
    preference rather than a veto, so RepeatAvoidance stays decisive."""
    rule = OrganizationDiversity(floor=0.05)
    context = context_with_airtime((1, 60))

    assert rule.weight(video(1, organization_id=1), context) == 0.05


# --- RepeatAvoidance --------------------------------------------------------


def test_the_video_that_just_played_is_vetoed() -> None:
    rule = RepeatAvoidance()
    context = ScheduleContext()
    context.record(video(1))

    assert rule.weight(video(1), context) == 0.0
    assert rule.weight(video(2), context) == 1.0


def test_each_earlier_play_in_the_window_halves_the_weight() -> None:
    rule = RepeatAvoidance(penalty=0.5)
    context = ScheduleContext()
    context.record(video(1))
    context.record(video(1))
    context.record(video(2))

    assert rule.weight(video(1), context) == 0.25


# --- ScheduleContext --------------------------------------------------------


def test_airtime_shares_accumulate_as_picks_land() -> None:
    context = context_with_airtime((1, 30), (2, 10))

    assert context.organization_share(1) == 0.75
    assert context.organization_share(2) == 0.25
    assert context.organization_share(3) == 0.0
    assert context.organization_share(None) == 0.0


# --- WeightedSelector -------------------------------------------------------


def selector(candidates, rules, seed=1) -> WeightedSelector:
    return WeightedSelector(candidates, ScheduleContext(), rules, rng=random.Random(seed))


def pick_id(chooser: WeightedSelector, remaining: datetime.timedelta) -> int:
    """The id of a draw the caller expects to succeed.

    pick() returns None when nothing fits; the tests that check for that
    call it directly.
    """
    video = chooser.pick(remaining)
    assert video is not None
    return video.id


def test_only_videos_that_fit_are_drawn() -> None:
    chooser = selector([video(1, minutes=30), video(2, minutes=5)], rules=[])

    assert pick_id(chooser, minutes(10)) == 2
    assert chooser.pick(minutes(3)) is None


def test_picks_are_recorded_so_rules_see_them() -> None:
    chooser = selector([video(1), video(2)], rules=[RepeatAvoidance()])

    drawn = [pick_id(chooser, minutes(60)) for _ in range(10)]

    assert all(a != b for a, b in pairwise(drawn))


def test_a_universally_vetoed_draw_falls_back_to_uniform() -> None:
    """Dead air is worse than repetition: if the only video that fits
    just played, it plays again."""
    chooser = selector([video(1)], rules=[RepeatAvoidance()])

    assert pick_id(chooser, minutes(60)) == 1
    assert pick_id(chooser, minutes(60)) == 1


def test_the_draw_follows_the_weights() -> None:
    """With a 9:1 weight ratio, the heavier video dominates the tally."""

    class Favor:
        def weight(self, v, context):
            return 0.9 if v.id == 1 else 0.1

    chooser = selector([video(1), video(2)], rules=[Favor()], seed=42)

    drawn = [pick_id(chooser, minutes(60)) for _ in range(200)]

    assert drawn.count(1) > 150


def test_zero_length_candidates_are_never_drawn() -> None:
    """A non-positive duration cannot advance the schedule clock."""
    chooser = selector([video(1, minutes=0)], rules=[])

    assert chooser.pick(minutes(60)) is None
