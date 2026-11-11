"""Fill rules: pools, orderings, modes, and that filling is deterministic."""

from datetime import timedelta

import pytest

from fk.models import Organization, Scheduleitem, User
from scheduling.filling import candidates, plan
from scheduling.models import FillRule, Playlist
from scheduling.selection import ScheduleContext

from .conftest import NOW

pytestmark = pytest.mark.django_db

START = NOW + timedelta(days=8)


def ids(videos) -> list[int]:
    return [v.pk for v in videos]


def test_latest_fills_the_window_newest_first(rule, make_grant, make_video) -> None:
    old, middle, new = make_video(30), make_video(20), make_video(25)
    grant = make_grant(START, minutes=60)

    picks = plan(rule, grant, ScheduleContext(), NOW)

    # Newest (25) then middle (20); the oldest (30) no longer fits.
    assert ids(picks) == [new.pk, middle.pk]
    assert old not in picks


def test_one_video_mode_stops_after_the_first_pick(rule, make_grant, make_video) -> None:
    make_video(10)
    newest = make_video(10)
    rule.mode = FillRule.Mode.ONE

    assert ids(plan(rule, make_grant(START), ScheduleContext(), NOW)) == [newest.pk]


def test_a_video_too_long_for_the_window_is_skipped(rule, make_grant, make_video) -> None:
    fits = make_video(30)
    make_video(90)

    assert ids(plan(rule, make_grant(START), ScheduleContext(), NOW)) == [fits.pk]


def test_every_pool_excludes_zero_length_and_unaccountable_videos(
    rule, make_grant, make_video
) -> None:
    good = make_video(10)
    make_video(0)
    orphan_org = Organization.objects.create(name="No editor")
    make_video(10, organization=orphan_org)

    assert ids(candidates(rule)) == [good.pk]


def test_only_latest_picks_a_pending_upload(rule, make_video) -> None:
    """A weekly regular creates next week's entry before the lock, with a
    hand-set duration, and uploads later."""
    finished = make_video(10)
    placeholder = make_video(10, proper_import=False)

    assert ids(candidates(rule)) == [placeholder.pk, finished.pk]
    rule.ordering = FillRule.Ordering.SHUFFLED
    assert ids(candidates(rule)) == [finished.pk]


def test_playlist_pool(organization, rule, make_video) -> None:
    listed, _unlisted = make_video(10), make_video(10)
    playlist = Playlist.objects.create(organization=organization, name="Reruns")
    playlist.videos.add(listed)
    rule.pool, rule.playlist = FillRule.Pool.PLAYLIST, playlist

    assert ids(candidates(rule)) == [listed.pk]


def test_filler_pool(organization, rule, make_video) -> None:
    filler = make_video(10, is_filler=True)
    make_video(10)
    make_video(10, is_filler=True, has_tono_records=True)
    rule.pool = FillRule.Pool.FILLER

    assert ids(candidates(rule)) == [filler.pk]


def test_least_aired_counts_the_schedule_and_the_run(rule, make_grant, make_video) -> None:
    aired, fresh = make_video(10), make_video(10)
    Scheduleitem.objects.create(
        video=aired, starttime=NOW - timedelta(days=30), duration=aired.duration
    )
    rule.ordering, rule.mode = FillRule.Ordering.LEAST_AIRED, FillRule.Mode.ONE
    context = ScheduleContext()

    first = plan(rule, make_grant(START), context, NOW)
    second = plan(rule, make_grant(START + timedelta(hours=2)), context, NOW)

    assert ids(first) == [fresh.pk]
    # `fresh` now has one play in the run too; the older one wins the tie.
    assert ids(second) == [aired.pk]


def test_shuffled_is_stable_night_to_night(rule, make_grant, make_video) -> None:
    for _ in range(8):
        make_video(5)
    rule.ordering = FillRule.Ordering.SHUFFLED
    grant = make_grant(START, minutes=30)

    first = ids(plan(rule, grant, ScheduleContext(), NOW))
    again = ids(plan(rule, grant, ScheduleContext(), NOW))
    assert first == again
    assert len(first) == 6


def test_weighted_repeats_for_the_same_grant(organization, rule, make_grant, make_video) -> None:
    for _ in range(10):
        make_video(5, is_filler=True)
    rule.pool, rule.ordering = FillRule.Pool.FILLER, FillRule.Ordering.WEIGHTED
    grant = make_grant(START, minutes=30)

    first = ids(plan(rule, grant, ScheduleContext(), NOW))
    second = ids(plan(rule, grant, ScheduleContext(), NOW))

    assert first == second
    assert len(first) == 6


def test_a_new_upload_slots_into_shuffled_order_without_reshuffling(
    rule, make_grant, make_video
) -> None:
    for _ in range(6):
        make_video(5)
    rule.ordering = FillRule.Ordering.SHUFFLED
    grant = make_grant(START, minutes=60)  # room for everything
    before = ids(plan(rule, grant, ScheduleContext(), NOW))

    newcomer = make_video(5)
    after = ids(plan(rule, grant, ScheduleContext(), NOW))

    assert [pk for pk in after if pk != newcomer.pk] == before


def test_an_empty_pool_plans_nothing(rule, make_grant) -> None:
    assert plan(rule, make_grant(START), ScheduleContext(), NOW) == []


def test_another_organizations_rule_draws_from_its_own_uploads(
    organization, make_grant, make_video
) -> None:
    other_editor = User.objects.create(email="other@example.test")
    other = Organization.objects.create(name="Other", editor=other_editor, fkmember=True)
    theirs = make_video(10, organization=other)
    make_video(10)
    rule = FillRule(organization=other, pool="organization", ordering="latest", mode="fill")

    assert ids(plan(rule, make_grant(START), ScheduleContext(), NOW)) == [theirs.pk]
