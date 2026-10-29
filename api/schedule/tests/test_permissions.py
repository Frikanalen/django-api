from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIClient

from fk.models import Organization, Scheduleitem, User, Video

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("now_in_the_drafting_week")]

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
    payload = {
        "starttime": SCHEDULE_START.isoformat(),
        "duration": "01:00:00",
        "schedulereason": Scheduleitem.REASON_USER,
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


def test_confirmed_users_from_member_organizations_can_schedule(
    member_client: APIClient,
    organization_member: User,
    organization: Organization,
    video: Video,
) -> None:
    organization.members.add(organization_member)

    response = member_client.post(
        reverse("api-scheduleitem-list"), schedule_payload(video), format="json"
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert Scheduleitem.objects.get().video == video


def test_member_schedule_provenance_is_server_owned(
    member_client: APIClient,
    organization_member: User,
    organization: Organization,
    video: Video,
) -> None:
    organization.members.add(organization_member)
    payload = schedule_payload(video)
    payload["schedulereason"] = Scheduleitem.REASON_JUKEBOX

    response = member_client.post(reverse("api-scheduleitem-list"), payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert Scheduleitem.objects.get().schedulereason == Scheduleitem.REASON_USER


def test_members_cannot_turn_their_programming_into_jukebox_filler(
    member_client: APIClient,
    organization_member: User,
    organization: Organization,
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    organization.members.add(organization_member)
    item = schedule_item_factory(starttime=SCHEDULE_START)

    response = member_client.patch(
        reverse("api-scheduleitem-detail", args=[item.pk]),
        {"schedulereason": Scheduleitem.REASON_JUKEBOX},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    item.refresh_from_db()
    assert item.schedulereason == Scheduleitem.REASON_USER


def test_members_cannot_schedule_a_video_that_is_still_processing(
    member_client: APIClient,
    organization_member: User,
    organization: Organization,
    video: Video,
) -> None:
    organization.members.add(organization_member)
    video.proper_import = False
    video.save(update_fields=["proper_import"])

    response = member_client.post(
        reverse("api-scheduleitem-list"), schedule_payload(video), format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["errors"][0]["attr"] == "video"
    assert not Scheduleitem.objects.exists()


@pytest.mark.parametrize(
    ("identity_confirmed", "fkmember"),
    [(False, True), (True, False), (False, False)],
)
def test_scheduling_requires_both_identity_confirmation_and_a_member_organization(
    organization_member: User,
    organization: Organization,
    video: Video,
    identity_confirmed: bool,
    fkmember: bool,
) -> None:
    organization_member.identity_confirmed = identity_confirmed
    organization_member.save(update_fields=["identity_confirmed"])
    organization.fkmember = fkmember
    organization.save(update_fields=["fkmember"])
    organization.members.add(organization_member)
    client = APIClient()
    client.force_authenticate(user=organization_member)

    response = client.post(reverse("api-scheduleitem-list"), schedule_payload(video), format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert_error_code(response, "permission_denied")
    assert not Scheduleitem.objects.exists()


def test_unconfirmed_non_member_organization_users_can_upload_but_not_schedule(
    organization: Organization,
) -> None:
    user = User.objects.create(email="unapproved-uploader@example.test")
    organization.fkmember = False
    organization.save(update_fields=["fkmember"])
    organization.members.add(user)
    client = APIClient()
    client.force_authenticate(user=user)

    upload_response = client.post(
        reverse("api-video-list"),
        {"name": "Pending approval", "organization": organization.pk, "categories": []},
        format="json",
    )
    assert upload_response.status_code == status.HTTP_201_CREATED
    video = Video.objects.get(pk=upload_response.data["id"])
    schedule_response = client.post(
        reverse("api-scheduleitem-list"), schedule_payload(video), format="json"
    )

    assert schedule_response.status_code == status.HTTP_403_FORBIDDEN
    assert not Scheduleitem.objects.exists()


@pytest.mark.parametrize(
    ("identity_confirmed", "fkmember"),
    [(False, True), (True, False)],
)
@pytest.mark.parametrize(
    ("method", "payload"),
    [("patch", {"duration": "00:30:00"}), ("delete", None)],
)
def test_ineligible_users_cannot_modify_or_delete_existing_schedule_items(
    organization_member: User,
    organization: Organization,
    schedule_item_factory: Callable[..., Scheduleitem],
    identity_confirmed: bool,
    fkmember: bool,
    method: str,
    payload: dict | None,
) -> None:
    organization_member.identity_confirmed = identity_confirmed
    organization_member.save(update_fields=["identity_confirmed"])
    organization.fkmember = fkmember
    organization.save(update_fields=["fkmember"])
    organization.members.add(organization_member)
    item = schedule_item_factory(starttime=SCHEDULE_START)
    client = APIClient()
    client.force_authenticate(user=organization_member)

    response = getattr(client, method)(
        reverse("api-scheduleitem-detail", args=[item.pk]), payload, format="json"
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert_error_code(response, "permission_denied")
    item.refresh_from_db()
    assert item.duration == timedelta(hours=1)


def test_non_staff_users_cannot_create_schedule_items_without_a_video(
    member_client: APIClient,
    organization_member: User,
    organization: Organization,
) -> None:
    organization.members.add(organization_member)

    response = member_client.post(
        reverse("api-scheduleitem-list"), schedule_payload(), format="json"
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not Scheduleitem.objects.exists()


def test_non_staff_users_cannot_remove_the_video_from_a_schedule_item(
    member_client: APIClient,
    organization_member: User,
    organization: Organization,
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    organization.members.add(organization_member)
    item = schedule_item_factory(starttime=SCHEDULE_START)

    response = member_client.patch(
        reverse("api-scheduleitem-detail", args=[item.pk]), {"video": None}, format="json"
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    item.refresh_from_db()
    assert item.video is not None


def test_repointing_requires_the_target_organization_to_be_a_member(
    member_client: APIClient,
    organization_member: User,
    organization: Organization,
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    organization.members.add(organization_member)
    item = schedule_item_factory(starttime=SCHEDULE_START)
    non_member = Organization.objects.create(
        name="Pending organization", editor=organization_member, fkmember=False
    )
    target = Video.objects.create(
        name="Pending video", creator=organization_member, organization=non_member
    )

    response = member_client.patch(
        reverse("api-scheduleitem-detail", args=[item.pk]),
        {"video": target.pk},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    item.refresh_from_db()
    assert item.video != target


def test_staff_can_schedule_for_unapproved_users_and_organizations(
    authenticated_client: APIClient,
    organization: Organization,
    video: Video,
) -> None:
    organization.fkmember = False
    organization.save(update_fields=["fkmember"])

    response = authenticated_client.post(
        reverse("api-scheduleitem-list"), schedule_payload(video), format="json"
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert Scheduleitem.objects.get().video == video


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
