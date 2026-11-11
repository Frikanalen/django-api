from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIClient

from fk.models import Organization, Scheduleitem, User, Video

pytestmark = pytest.mark.django_db

OSLO = ZoneInfo("Europe/Oslo")
SCHEDULE_START = datetime(2015, 1, 1, 10, tzinfo=OSLO)


@pytest.fixture
def organization_member() -> User:
    return User.objects.create(
        email="schedule-member@example.test",
        identity_confirmed=True,
    )


@pytest.fixture
def member_client(organization_member: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=organization_member)
    return client


def assert_error_code(response: Response, expected_code: str) -> None:
    assert response.data["type"] == "client_error"
    assert [error["code"] for error in response.data["errors"]] == [expected_code]


def schedule_payload(video: Video | None = None) -> dict:
    payload: dict[str, str | int] = {
        "starttime": SCHEDULE_START.isoformat(),
        "duration": "01:00:00",
    }
    if video is not None:
        payload["video"] = video.pk
    return payload


def test_anonymous_users_can_read_schedule(
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    item = schedule_item_factory(starttime=SCHEDULE_START)
    client = APIClient()

    list_response = client.get(
        reverse("api-scheduleitem-list"),
        {"date": SCHEDULE_START.date().isoformat()},
    )
    detail_response = client.get(reverse("api-scheduleitem-detail", args=[item.pk]))

    assert list_response.status_code == status.HTTP_200_OK
    assert [result["id"] for result in list_response.data["results"]] == [item.pk]
    assert detail_response.status_code == status.HTTP_200_OK
    assert detail_response.data["id"] == item.pk


def test_anonymous_users_cannot_create_schedule_items(video: Video) -> None:
    response = APIClient().post(
        reverse("api-scheduleitem-list"),
        schedule_payload(video),
        format="json",
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert_error_code(response, "not_authenticated")
    assert not Scheduleitem.objects.exists()


@pytest.fixture
def member_video(organization_member: User) -> Video:
    """A video of an approved member organization the member belongs to:
    everything that used to let a member schedule it."""
    organization = Organization.objects.create(
        name="Member organization", editor=organization_member, fkmember=True
    )
    organization.members.add(organization_member)
    return Video.objects.create(
        creator=organization_member,
        name="Member video",
        organization=organization,
        proper_import=True,
        duration=timedelta(hours=1),
    )


def test_members_cannot_create_schedule_items(
    member_client: APIClient, member_video: Video
) -> None:
    """Organizations program airtime through grants, not the schedule."""
    response = member_client.post(
        reverse("api-scheduleitem-list"), schedule_payload(member_video), format="json"
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert_error_code(response, "permission_denied")
    assert not Scheduleitem.objects.exists()


@pytest.mark.parametrize(
    ("method", "payload"),
    [("patch", {"duration": "00:30:00"}), ("delete", None)],
)
def test_members_cannot_modify_or_delete_their_schedule_items(
    member_client: APIClient, member_video: Video, method: str, payload: dict | None
) -> None:
    item = Scheduleitem.objects.create(
        video=member_video, starttime=SCHEDULE_START, duration=timedelta(hours=1)
    )

    response = getattr(member_client, method)(
        reverse("api-scheduleitem-detail", args=[item.pk]), payload, format="json"
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    item.refresh_from_db()
    assert item.duration == timedelta(hours=1)


def test_staff_correct_the_schedule_for_any_organization(
    authenticated_client: APIClient, video: Video
) -> None:
    response = authenticated_client.post(
        reverse("api-scheduleitem-list"), schedule_payload(video), format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED
    item = Scheduleitem.objects.get()
    assert item.video == video
    # A staff entry: no grant, no historical reason.
    assert (item.grant, item.schedulereason) == (None, None)

    detail = reverse("api-scheduleitem-detail", args=[item.pk])
    patched = authenticated_client.patch(detail, {"duration": "00:30:00"}, format="json")
    assert patched.status_code == 200
    assert authenticated_client.delete(detail).status_code == 204
