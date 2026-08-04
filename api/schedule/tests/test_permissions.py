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
    return User.objects.create(email="schedule-member@example.test")


@pytest.fixture
def member_client(organization_member: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=organization_member)
    return client


def assert_error_code(response: Response, expected_code: str) -> None:
    assert response.data["type"] == "client_error"
    assert [error["code"] for error in response.data["errors"]] == [expected_code]


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
        {
            "video": video.pk,
            "starttime": SCHEDULE_START.isoformat(),
            "duration": "01:00:00",
            "schedulereason": Scheduleitem.REASON_LEGACY,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert_error_code(response, "not_authenticated")
    assert not Scheduleitem.objects.exists()


def test_organization_members_can_update_their_schedule_items(
    member_client: APIClient,
    organization_member: User,
    organization: Organization,
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    organization.members.add(organization_member)
    item = schedule_item_factory(starttime=SCHEDULE_START)

    response = member_client.patch(
        reverse("api-scheduleitem-detail", args=[item.pk]),
        {"duration": "00:30:00"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    item.refresh_from_db()
    assert item.duration == timedelta(minutes=30)


def test_organization_members_cannot_update_another_organizations_schedule_items(
    member_client: APIClient,
    organization_member: User,
    organization: Organization,
    staff_user: User,
) -> None:
    organization.members.add(organization_member)
    other_organization = Organization.objects.create(
        name="Other schedule organization",
        editor=staff_user,
    )
    other_video = Video.objects.create(
        creator=staff_user,
        name="Other schedule video",
        organization=other_organization,
        proper_import=True,
    )
    item = Scheduleitem.objects.create(
        video=other_video,
        starttime=SCHEDULE_START,
        duration=timedelta(hours=1),
        schedulereason=Scheduleitem.REASON_LEGACY,
    )

    response = member_client.patch(
        reverse("api-scheduleitem-detail", args=[item.pk]),
        {"duration": "00:30:00"},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert_error_code(response, "permission_denied")
    item.refresh_from_db()
    assert item.duration == timedelta(hours=1)
