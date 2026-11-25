"""Deleting a video takes it out of grant lists and off the schedule."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError

from fk.models import AsRun, Scheduleitem, Video
from scheduling import services
from scheduling.models import AirtimeGrant, GrantItem

from .conftest import NOW
from .test_services import OPEN_WEEK, items, schedule

pytestmark = pytest.mark.django_db
State = AirtimeGrant.State
MINUTE = timedelta(minutes=1)


def compiled(grant: AirtimeGrant, entries, now=NOW) -> AirtimeGrant:
    services._replace_items(grant, entries, pinned=len(entries))
    grant.state = State.LOCKED
    grant.save()
    services.compile_grant(grant, now)
    return grant


def test_a_coming_grant_closes_up_behind_a_deleted_video(make_grant, make_video) -> None:
    grant = make_grant(OPEN_WEEK)
    a, b, c = make_video(20), make_video(10), make_video(10)
    compiled(grant, [a, b, c])

    services.delete_video(b, now=NOW)

    assert not Video.objects.filter(pk=b.pk).exists()
    assert items(grant) == [a.pk, c.pk]
    assert grant.items.filter(pinned=True).count() == 2
    assert schedule(grant) == [
        (a.pk, OPEN_WEEK, 20 * MINUTE),
        (c.pk, OPEN_WEEK + 25 * MINUTE, 10 * MINUTE),
    ]


def test_an_automatic_grant_fills_the_time_with_something_else(
    make_grant, make_video, recipe
) -> None:
    grant = make_grant(OPEN_WEEK, recipe=recipe)
    test_video = make_video(50)
    services.refill(grant, now=NOW)
    grant.state = State.LOCKED
    grant.save()
    services.compile_grant(grant, NOW)
    assert [row[0] for row in schedule(grant)] == [test_video.pk]
    other = make_video(40)  # too long to fit beside it

    services.delete_video(test_video, now=NOW)

    assert items(grant) == [other.pk]
    assert [row[0] for row in schedule(grant)] == [other.pk]


def test_what_has_aired_keeps_its_title(make_grant, make_video) -> None:
    past = make_grant(NOW - timedelta(days=2))
    video = make_video(20, name="Testvideo")
    compiled(past, [video], now=past.starttime - timedelta(days=1))
    AsRun.objects.create(video=video, played_at=past.starttime)

    services.delete_video(video, now=NOW)

    [row] = Scheduleitem.objects.filter(grant=past)
    assert row.video_id is None
    assert row.default_name == "Testvideo"
    assert AsRun.objects.get().program_name == "Testvideo"
    assert not GrantItem.objects.filter(grant=past).exists()


def test_a_staff_entry_yet_to_air_goes(make_video) -> None:
    video = make_video(20)
    Scheduleitem.objects.create(video=video, starttime=OPEN_WEEK, duration=video.duration)

    services.delete_video(video, now=NOW)

    assert not Scheduleitem.objects.exists()


def test_a_video_later_in_a_grant_on_air_comes_out(make_grant, make_video) -> None:
    grant = make_grant(OPEN_WEEK)
    a, b, c = make_video(20), make_video(10), make_video(10)
    compiled(grant, [a, b, c])
    a_row = Scheduleitem.objects.get(video=a)

    services.delete_video(b, now=OPEN_WEEK + 10 * MINUTE)

    assert items(grant) == [a.pk, c.pk]
    assert schedule(grant) == [
        (a.pk, OPEN_WEEK, 20 * MINUTE),
        (c.pk, OPEN_WEEK + 25 * MINUTE, 10 * MINUTE),
    ]
    # The row on air is left alone, so playout keeps playing it.
    assert Scheduleitem.objects.filter(pk=a_row.pk, starttime=OPEN_WEEK).exists()


@pytest.mark.parametrize(
    "now",
    [
        OPEN_WEEK + 10 * MINUTE,  # on air
        OPEN_WEEK - timedelta(seconds=20),  # about to start: playout has cued it
        OPEN_WEEK + 30 * MINUTE,  # aired, and its grant is still on air
    ],
)
def test_a_video_on_air_or_in_a_grant_on_air_is_kept(make_grant, make_video, now) -> None:
    grant = make_grant(OPEN_WEEK)
    a, b = make_video(20), make_video(10)
    compiled(grant, [a, b])
    before = schedule(grant)

    with pytest.raises(ValidationError, match="Du kan slette den når"):
        services.delete_video(a, now=now)

    assert Video.objects.filter(pk=a.pk).exists()
    assert schedule(grant) == before


def test_a_video_whose_grant_has_ended_can_go(make_grant, make_video) -> None:
    grant = make_grant(OPEN_WEEK)
    a = make_video(20)
    compiled(grant, [a])

    services.delete_video(a, now=OPEN_WEEK + 61 * MINUTE)

    assert not Video.objects.filter(pk=a.pk).exists()
    assert Scheduleitem.objects.get(grant=grant).video_id is None
