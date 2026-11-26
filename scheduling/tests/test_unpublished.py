"""Recipes never air an unpublished video; one unpublished after it was
picked comes out of the list, even inside the day the schedule is firm."""

from datetime import timedelta

import pytest

from fk.models import Scheduleitem
from scheduling import services
from scheduling.models import AirtimeGrant

from .test_services import OPEN_WEEK, items, schedule

pytestmark = pytest.mark.django_db
State = AirtimeGrant.State
MINUTE = timedelta(minutes=1)
# Within the day before air, when the nightly refill leaves a compiled grant alone.
SAME_DAY = OPEN_WEEK - timedelta(hours=2)


def picked(grant: AirtimeGrant, now=SAME_DAY - timedelta(days=3)) -> AirtimeGrant:
    services.refill(grant, now=now)
    grant.state = State.LOCKED
    grant.save()
    services.compile_grant(grant, now)
    return grant


def unpublish(video, now) -> None:
    video.publish_on_web = False
    video.save()
    services.recompile_for_video(video, now=now)


def test_a_recipe_does_not_pick_an_unpublished_video(make_grant, make_video, recipe) -> None:
    published = make_video(20)
    make_video(20, publish_on_web=False)  # newer, so it would go first
    grant = make_grant(OPEN_WEEK, recipe=recipe)

    services.refill(grant, now=SAME_DAY)

    assert items(grant) == [published.pk]


def test_an_unpublished_pick_is_replaced_on_the_day(make_grant, make_video, recipe) -> None:
    other = make_video(40)  # older, so the uploads recipe picks the next one first
    video = make_video(50)
    grant = picked(make_grant(OPEN_WEEK, recipe=recipe))
    assert [row[0] for row in schedule(grant)] == [video.pk]

    unpublish(video, SAME_DAY)

    assert items(grant) == [other.pk]
    assert [row[0] for row in schedule(grant)] == [other.pk]


def test_an_unpublished_pick_later_in_a_grant_on_air_comes_out(
    make_grant, make_video, recipe
) -> None:
    b = make_video(10)
    a = make_video(20)  # newest, so it airs first
    grant = picked(make_grant(OPEN_WEEK, minutes=35, recipe=recipe))
    assert [row[0] for row in schedule(grant)] == [a.pk, b.pk]
    a_row = Scheduleitem.objects.get(video=a)

    unpublish(b, OPEN_WEEK + 10 * MINUTE)

    assert schedule(grant) == [(a.pk, OPEN_WEEK, 20 * MINUTE)]
    # The row on air is left alone, so playout keeps playing it.
    assert Scheduleitem.objects.filter(pk=a_row.pk, starttime=OPEN_WEEK).exists()


def test_an_unpublished_pick_about_to_start_stays(make_grant, make_video, recipe) -> None:
    video = make_video(50)
    grant = picked(make_grant(OPEN_WEEK, recipe=recipe))
    before = schedule(grant)

    unpublish(video, OPEN_WEEK - timedelta(seconds=20))

    assert schedule(grant) == before


def test_a_pinned_unpublished_video_stays(make_grant, make_video, recipe) -> None:
    make_video(40)
    video = make_video(20)
    grant = make_grant(OPEN_WEEK, recipe=recipe)
    services._replace_items(grant, [video], pinned=1)
    picked(grant)
    assert schedule(grant)[0][0] == video.pk

    unpublish(video, SAME_DAY)

    assert items(grant)[0] == video.pk
    assert schedule(grant)[0][0] == video.pk


def test_a_hand_made_list_keeps_an_unpublished_video(make_grant, make_video) -> None:
    video = make_video(20, publish_on_web=False)
    grant = make_grant(OPEN_WEEK)
    services._replace_items(grant, [video], pinned=0)
    grant.state = State.LOCKED
    grant.save()

    services.compile_grant(grant, SAME_DAY)

    assert [row[0] for row in schedule(grant)] == [video.pk]


def test_compiling_leaves_out_an_unpublished_pick(make_grant, make_video, recipe) -> None:
    a, b = make_video(20), make_video(10, publish_on_web=False)
    grant = make_grant(OPEN_WEEK, recipe=recipe)
    services._replace_items(grant, [a, b], pinned=0)
    grant.state = State.LOCKED
    grant.save()

    services.compile_grant(grant, SAME_DAY)

    assert schedule(grant) == [(a.pk, OPEN_WEEK, 20 * MINUTE)]
