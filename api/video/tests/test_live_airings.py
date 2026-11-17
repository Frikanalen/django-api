"""A live programme's page says when it goes out: `liveAirings` on the video."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from fk.models import LiveSource, Organization, Scheduleitem, User, Video
from fk.models.live import LiveTransmission

pytestmark = pytest.mark.django_db


@pytest.fixture
def live_video(editor: User, organization: Organization) -> Video:
    organization.fkmember = True
    organization.save()
    video = Video.objects.create(
        name="Direkte fra studio",
        creator=editor,
        organization=organization,
        duration=timedelta(hours=1),
        # No file yet: it airs from the source.
        proper_import=False,
    )
    source = LiveSource.objects.create(organization=organization, name="Studio")
    LiveTransmission.objects.create(video=video, source=source)
    return video


def _air(video: Video, start, duration=timedelta(hours=1), is_live=True) -> Scheduleitem:
    return Scheduleitem.objects.create(
        video=video, starttime=start, duration=duration, is_live=is_live
    )


def test_lists_live_airings_that_have_not_ended_soonest_first(live_video: Video) -> None:
    now = timezone.now()
    _air(live_video, now - timedelta(days=1))  # over
    later = _air(live_video, now + timedelta(days=2))
    on_air = _air(live_video, now - timedelta(minutes=10))
    _air(live_video, now + timedelta(days=3), is_live=False)  # a rerun from file

    response = APIClient().get(f"/api/videos/{live_video.pk}")

    assert response.status_code == 200
    airings = response.json()["liveAirings"]
    assert [a["id"] for a in airings] == [on_air.pk, later.pk]
    assert set(airings[0]) == {"id", "starttime", "endtime"}
    assert airings[0]["starttime"].endswith(("+01:00", "+02:00"))


def test_video_without_live_source_has_none(editor: User, organization: Organization) -> None:
    video = Video.objects.create(
        name="Fra fil", creator=editor, organization=organization, proper_import=True
    )
    _air(video, timezone.now() + timedelta(days=1), is_live=True)

    assert APIClient().get(f"/api/videos/{video.pk}").json()["liveAirings"] == []


def test_list_carries_live_airings(live_video: Video) -> None:
    airing = _air(live_video, timezone.now() + timedelta(hours=1))

    results = APIClient().get("/api/videos", {"proper_import": "false"}).json()["results"]

    [row] = [r for r in results if r["id"] == live_video.pk]
    assert [a["id"] for a in row["liveAirings"]] == [airing.pk]


def test_none_once_it_has_a_file(live_video: Video) -> None:
    _air(live_video, timezone.now() + timedelta(hours=1))
    Video.objects.filter(pk=live_video.pk).update(proper_import=True)

    assert APIClient().get(f"/api/videos/{live_video.pk}").json()["liveAirings"] == []
