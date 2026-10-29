from datetime import time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import (
    Organization,
    SlotSourceStrategy,
    SlotSourceType,
    User,
    WeeklySlot,
    WeeklySlotCreationRequest,
    WeeklySlotOwnershipRequest,
    WeeklySlotRequestStatus,
    WeeklySlotSource,
)

pytestmark = pytest.mark.django_db


def creation_payload(organization: Organization) -> dict:
    return {
        "organization": organization.pk,
        "day": 4,
        "startTime": "20:30",
        "duration": "01:30:00",
    }


def make_foreign_slot(*, with_source: bool = False) -> tuple[Organization, WeeklySlot]:
    owner_editor = User.objects.create(email="current-slot-owner@example.test")
    owner = Organization.objects.create(name="Current slot owner", editor=owner_editor)
    source = None
    if with_source:
        source = WeeklySlotSource.objects.create(
            name="Former owner's source",
            type=SlotSourceType.ORGANIZATION,
            strategy=SlotSourceStrategy.LATEST,
            organization=owner,
        )
    slot = WeeklySlot.objects.create(
        organization=owner,
        source=source,
        day=2,
        start_time="14:15",
        duration=timedelta(minutes=45),
    )
    return owner, slot


def test_creation_endpoint_creates_only_a_creation_request(
    member: User,
    member_client: APIClient,
    organization: Organization,
) -> None:
    response = member_client.post(
        reverse("api-weekly-slot-creation-request-create"),
        creation_payload(organization),
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    slot_request = WeeklySlotCreationRequest.objects.get()
    assert slot_request.requested_by == member
    assert slot_request.status == WeeklySlotRequestStatus.PENDING
    assert slot_request.reviewed_by is None
    assert slot_request.weekly_slot is None
    assert not WeeklySlotOwnershipRequest.objects.exists()


def test_ownership_endpoint_creates_only_an_ownership_request(
    member: User,
    member_client: APIClient,
    organization: Organization,
) -> None:
    previous_owner, slot = make_foreign_slot()

    response = member_client.post(
        reverse("api-weekly-slot-ownership-request-create"),
        {
            "organization": organization.pk,
            "weeklySlot": slot.pk,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    slot_request = WeeklySlotOwnershipRequest.objects.get()
    assert slot_request.requested_by == member
    assert slot_request.weekly_slot == slot
    assert slot_request.previous_organization == previous_owner
    assert not WeeklySlotCreationRequest.objects.exists()


@pytest.mark.parametrize("branch", ["creation", "ownership"])
def test_source_is_not_part_of_either_request_type(
    branch: str,
    member_client: APIClient,
    organization: Organization,
    source: WeeklySlotSource,
) -> None:
    if branch == "creation":
        payload = creation_payload(organization)
        endpoint = "api-weekly-slot-creation-request-create"
    else:
        _, slot = make_foreign_slot()
        payload = {
            "organization": organization.pk,
            "weeklySlot": slot.pk,
        }
        endpoint = "api-weekly-slot-ownership-request-create"
    payload["source"] = source.pk

    response = member_client.post(reverse(endpoint), payload, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not WeeklySlotCreationRequest.objects.exists()
    assert not WeeklySlotOwnershipRequest.objects.exists()


def test_outsider_cannot_request_for_another_organization(
    organization: Organization,
) -> None:
    outsider = User.objects.create(email="slot-request-outsider@example.test")
    client = APIClient()
    client.force_authenticate(outsider)

    response = client.post(
        reverse("api-weekly-slot-creation-request-create"),
        creation_payload(organization),
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not WeeklySlotCreationRequest.objects.exists()


def test_admin_approval_creates_an_unsourced_slot_and_records_audit(
    staff: User,
    organization: Organization,
    member: User,
) -> None:
    slot_request = WeeklySlotCreationRequest.objects.create(
        organization=organization,
        requested_by=member,
        day=4,
        start_time="20:30",
        duration=timedelta(hours=1, minutes=30),
    )

    slot_request.decide(
        admin=staff,
        status=WeeklySlotRequestStatus.APPROVED,
        comment="Approved for the autumn season.",
    )

    slot = slot_request.weekly_slot
    assert slot is not None
    assert slot.organization == organization
    assert slot.source is None
    assert slot.day == 4
    assert slot.start_time == time(20, 30)
    assert slot.duration == timedelta(hours=1, minutes=30)
    assert slot_request.reviewed_by == staff
    assert slot_request.reviewed_at is not None
    assert slot_request.admin_comment == "Approved for the autumn season."


def test_admin_denial_does_not_create_a_slot(
    staff: User,
    organization: Organization,
    member: User,
) -> None:
    slot_request = WeeklySlotCreationRequest.objects.create(
        organization=organization,
        requested_by=member,
        day=0,
        start_time="08:00",
        duration=timedelta(minutes=30),
    )

    slot_request.decide(
        admin=staff,
        status=WeeklySlotRequestStatus.DENIED,
        comment="That airtime is unavailable.",
    )

    assert slot_request.status == WeeklySlotRequestStatus.DENIED
    assert slot_request.weekly_slot is None
    assert not WeeklySlot.objects.exists()


def test_admin_approval_transfers_ownership_and_clears_former_source(
    staff: User,
    organization: Organization,
    member: User,
) -> None:
    previous_owner, slot = make_foreign_slot(with_source=True)
    slot_request = WeeklySlotOwnershipRequest.objects.create(
        organization=organization,
        requested_by=member,
        weekly_slot=slot,
        previous_organization=previous_owner,
    )

    slot_request.decide(
        admin=staff,
        status=WeeklySlotRequestStatus.APPROVED,
        comment="Transferred by agreement.",
    )

    slot.refresh_from_db()
    assert slot.organization == organization
    assert slot.source is None
    assert slot_request.reviewed_by == staff
    assert slot_request.admin_comment == "Transferred by agreement."


def test_duplicate_pending_ownership_request_is_rejected(
    member_client: APIClient,
    organization: Organization,
) -> None:
    _, slot = make_foreign_slot()
    payload = {
        "organization": organization.pk,
        "weeklySlot": slot.pk,
    }

    endpoint = reverse("api-weekly-slot-ownership-request-create")
    first = member_client.post(endpoint, payload, format="json")
    second = member_client.post(endpoint, payload, format="json")

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_400_BAD_REQUEST
    assert WeeklySlotOwnershipRequest.objects.count() == 1


def test_stale_ownership_request_cannot_overwrite_a_later_transfer(
    member: User,
    staff: User,
    organization: Organization,
) -> None:
    previous_owner, slot = make_foreign_slot()
    slot_request = WeeklySlotOwnershipRequest.objects.create(
        organization=organization,
        requested_by=member,
        weekly_slot=slot,
        previous_organization=previous_owner,
    )
    later_owner = Organization.objects.create(name="Later owner", editor=member)
    WeeklySlot.objects.filter(pk=slot.pk).update(organization=later_owner, source=None)

    with pytest.raises(ValidationError, match="ownership changed"):
        slot_request.decide(
            admin=staff,
            status=WeeklySlotRequestStatus.APPROVED,
            comment="Outdated approval.",
        )

    slot.refresh_from_db()
    slot_request.refresh_from_db()
    assert slot.organization == later_owner
    assert slot_request.status == WeeklySlotRequestStatus.PENDING


def test_decision_requires_a_comment_and_cannot_be_repeated(
    staff: User,
    organization: Organization,
    member: User,
) -> None:
    slot_request = WeeklySlotCreationRequest.objects.create(
        organization=organization,
        requested_by=member,
        day=3,
        start_time="17:00",
        duration=timedelta(hours=1),
    )

    with pytest.raises(ValidationError, match="comment"):
        slot_request.decide(admin=staff, status="approved", comment="")
    slot_request.decide(admin=staff, status="approved", comment="Approved.")
    with pytest.raises(ValidationError, match="already been decided"):
        slot_request.decide(admin=staff, status="denied", comment="Changed my mind.")


def test_list_combines_both_models_using_the_same_two_branches(
    member: User,
    member_client: APIClient,
    organization: Organization,
) -> None:
    WeeklySlotCreationRequest.objects.create(
        organization=organization,
        requested_by=member,
        day=1,
        start_time="09:30",
        duration=timedelta(minutes=30),
    )
    previous_owner, slot = make_foreign_slot()
    WeeklySlotOwnershipRequest.objects.create(
        organization=organization,
        requested_by=member,
        weekly_slot=slot,
        previous_organization=previous_owner,
    )

    response = member_client.get(reverse("api-weekly-slot-request-list"))

    assert response.status_code == status.HTTP_200_OK
    results = response.json()["results"]
    assert {next(iter(item)) for item in results} == {"creation", "ownership"}
