"""
Administrative removal: a video staff flag is_banned is withdrawn from the
website, the listings, the schedule and the air. Its page says only that
it has been removed pending review -- never why, nor whose it was.
"""

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from fk.models import Organization, Scheduleitem, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def editor() -> User:
    return User.objects.create(email="removed-editor@example.test", identity_confirmed=True)


@pytest.fixture
def organization(editor: User) -> Organization:
    organization = Organization.objects.create(name="Some org", fkmember=True, editor=editor)
    organization.members.add(editor)
    return organization


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    return Video.objects.create(
        name="Removed video",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=30),
        proper_import=True,
        publish_on_web=True,
        is_filler=True,
        has_tono_records=False,
        is_banned=True,
    )


@pytest.fixture
def staff() -> User:
    return User.objects.create(email="removal-staff@example.test", is_superuser=True)


@pytest.fixture
def airing(video: Video) -> Scheduleitem:
    return Scheduleitem.objects.create(
        video=video,
        schedulereason=Scheduleitem.REASON_ADMIN,
        starttime=timezone.now() + timedelta(hours=1),
        duration=video.duration,
    )


def test_page_says_removed_and_nothing_more(video) -> None:
    response = APIClient().get(reverse("api-video-detail", args=[video.pk]))

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Administrativt fjernet i påvente av gjennomgang.",
        "code": "video_removed",
    }


def test_its_own_organization_gets_the_same_notice(video, editor) -> None:
    client = APIClient()
    client.force_authenticate(user=editor)

    assert client.get(reverse("api-video-list")).json()["results"] == []
    response = client.get(reverse("api-video-detail", args=[video.pk]))
    assert response.status_code == 403
    assert response.json()["code"] == "video_removed"


def test_staff_still_see_it_flagged(video, staff) -> None:
    client = APIClient()
    client.force_authenticate(user=staff)
    body = client.get(reverse("api-video-detail", args=[video.pk])).json()

    assert body["name"] == "Removed video"
    assert body["isBanned"] is True


def test_the_flag_cannot_be_set_through_the_api(video, staff) -> None:
    client = APIClient()
    client.force_authenticate(user=staff)
    url = reverse("api-video-detail", args=[video.pk])
    client.patch(url, {"isBanned": False}, format="json")

    video.refresh_from_db()
    assert video.is_banned


def test_withdrawn_from_jukebox_grants_and_web(video) -> None:
    assert list(Video.objects.fillers()) == []
    assert list(Video.objects.listable()) == []
    assert list(Video.objects.public()) == []


def test_left_out_of_the_schedule_playout_reads(airing, staff) -> None:
    day = airing.starttime.astimezone(Scheduleitem.objects.none().TZ).date().isoformat()
    url = reverse("api-scheduleitem-list") + f"?date={day}&include_fillers=1"

    assert APIClient().get(url).json()["results"] == []
    assert APIClient().get(reverse("api-scheduleitem-detail", args=[airing.pk])).status_code == 404

    client = APIClient()
    client.force_authenticate(user=staff)
    assert [r["id"] for r in client.get(url).json()["results"]] == [airing.pk]


def test_lifting_the_removal_restores_it(video) -> None:
    video.is_banned = False
    video.save()

    assert APIClient().get(reverse("api-video-detail", args=[video.pk])).status_code == 200
    assert list(Video.objects.fillers()) == [video]


def test_removing_clears_the_page_cache(video, django_capture_on_commit_callbacks) -> None:
    video.is_banned = False
    with django_capture_on_commit_callbacks() as callbacks:
        video.save()
    assert cache.clear in callbacks

    with django_capture_on_commit_callbacks() as callbacks:
        video.name = "Renamed"
        video.save()
    assert cache.clear not in callbacks
