from datetime import date, time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Organization, User
from programming.services import uploads_recipe
from scheduling.models import (
    RecurringSlot,
    RecurringSlotCreationRequest,
    RecurringSlotOwnershipRequest,
    ReviewStatus,
)

pytestmark = pytest.mark.django_db


def creation_payload(organization: Organization) -> dict:
    return {
        "organization": organization.pk,
        "day": 4,
        "startTime": "20:30",
        "duration": "01:30:00",
    }


def make_foreign_slot(*, with_rule: bool = False) -> tuple[Organization, RecurringSlot]:
    owner_editor = User.objects.create(email="current-slot-owner@example.test")
    owner = Organization.objects.create(name="Current slot owner", editor=owner_editor)
    rule = None
    if with_rule:
        rule = uploads_recipe(owner)
    slot = RecurringSlot.objects.create(
        organization=owner,
        recipe=rule,
        weekdays=[2],
        start_time="14:15",
        duration=timedelta(minutes=45),
        starts_on=date(2026, 1, 1),
    )
    return owner, slot


def test_creation_endpoint_creates_only_a_creation_request(
    member: User,
    member_client: APIClient,
    organization: Organization,
) -> None:
    response = member_client.post(
        reverse("api-recurring-slot-creation-request-create"),
        creation_payload(organization),
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    slot_request = RecurringSlotCreationRequest.objects.get()
    assert slot_request.requested_by == member
    assert slot_request.status == ReviewStatus.PENDING
    assert slot_request.reviewed_by is None
    assert slot_request.slot is None
    assert not RecurringSlotOwnershipRequest.objects.exists()


def test_ownership_endpoint_creates_only_an_ownership_request(
    member: User,
    member_client: APIClient,
    organization: Organization,
) -> None:
    previous_owner, slot = make_foreign_slot()

    response = member_client.post(
        reverse("api-recurring-slot-ownership-request-create"),
        {
            "organization": organization.pk,
            "slot": slot.pk,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    slot_request = RecurringSlotOwnershipRequest.objects.get()
    assert slot_request.requested_by == member
    assert slot_request.slot == slot
    assert slot_request.previous_organization == previous_owner
    assert not RecurringSlotCreationRequest.objects.exists()


def test_outsider_cannot_request_for_another_organization(
    organization: Organization,
) -> None:
    outsider = User.objects.create(email="slot-request-outsider@example.test")
    client = APIClient()
    client.force_authenticate(outsider)

    response = client.post(
        reverse("api-recurring-slot-creation-request-create"),
        creation_payload(organization),
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not RecurringSlotCreationRequest.objects.exists()


def test_admin_approval_creates_a_weekly_slot_with_a_default_recipe_and_records_audit(
    staff: User,
    organization: Organization,
    member: User,
) -> None:
    slot_request = RecurringSlotCreationRequest.objects.create(
        organization=organization,
        requested_by=member,
        day=4,
        start_time="20:30",
        duration=timedelta(hours=1, minutes=30),
    )

    slot_request.decide(
        admin=staff,
        status=ReviewStatus.APPROVED,
        comment="Approved for the autumn season.",
    )

    slot = slot_request.slot
    assert slot is not None
    assert slot.organization == organization
    assert slot.frequency == RecurringSlot.Frequency.WEEKLY
    assert slot.weekdays == [4]
    # Doing nothing works: new uploads, then the archive, fill the window.
    assert slot.recipe == uploads_recipe(organization)
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
    slot_request = RecurringSlotCreationRequest.objects.create(
        organization=organization,
        requested_by=member,
        day=0,
        start_time="08:00",
        duration=timedelta(minutes=30),
    )

    slot_request.decide(
        admin=staff,
        status=ReviewStatus.DENIED,
        comment="That airtime is unavailable.",
    )

    assert slot_request.status == ReviewStatus.DENIED
    assert slot_request.slot is None
    assert not RecurringSlot.objects.exists()


def test_admin_approval_transfers_ownership_with_a_rule_for_the_new_owner(
    staff: User,
    organization: Organization,
    member: User,
) -> None:
    previous_owner, slot = make_foreign_slot(with_rule=True)
    slot_request = RecurringSlotOwnershipRequest.objects.create(
        organization=organization,
        requested_by=member,
        slot=slot,
        previous_organization=previous_owner,
    )

    slot_request.decide(
        admin=staff,
        status=ReviewStatus.APPROVED,
        comment="Transferred by agreement.",
    )

    slot.refresh_from_db()
    assert slot.organization == organization
    # The former owner's recipe doesn't follow the slot.
    assert slot.recipe == uploads_recipe(organization)
    assert slot_request.reviewed_by == staff
    assert slot_request.admin_comment == "Transferred by agreement."


def test_duplicate_pending_ownership_request_is_rejected(
    member_client: APIClient,
    organization: Organization,
) -> None:
    _, slot = make_foreign_slot()
    payload = {
        "organization": organization.pk,
        "slot": slot.pk,
    }

    endpoint = reverse("api-recurring-slot-ownership-request-create")
    first = member_client.post(endpoint, payload, format="json")
    second = member_client.post(endpoint, payload, format="json")

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_400_BAD_REQUEST
    assert RecurringSlotOwnershipRequest.objects.count() == 1


def test_stale_ownership_request_cannot_overwrite_a_later_transfer(
    member: User,
    staff: User,
    organization: Organization,
) -> None:
    previous_owner, slot = make_foreign_slot()
    slot_request = RecurringSlotOwnershipRequest.objects.create(
        organization=organization,
        requested_by=member,
        slot=slot,
        previous_organization=previous_owner,
    )
    later_owner = Organization.objects.create(name="Later owner", editor=member)
    RecurringSlot.objects.filter(pk=slot.pk).update(organization=later_owner)

    with pytest.raises(ValidationError, match="ownership changed"):
        slot_request.decide(
            admin=staff,
            status=ReviewStatus.APPROVED,
            comment="Outdated approval.",
        )

    slot.refresh_from_db()
    slot_request.refresh_from_db()
    assert slot.organization == later_owner
    assert slot_request.status == ReviewStatus.PENDING


def test_decision_requires_a_comment_and_cannot_be_repeated(
    staff: User,
    organization: Organization,
    member: User,
) -> None:
    slot_request = RecurringSlotCreationRequest.objects.create(
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
    RecurringSlotCreationRequest.objects.create(
        organization=organization,
        requested_by=member,
        day=1,
        start_time="09:30",
        duration=timedelta(minutes=30),
    )
    previous_owner, slot = make_foreign_slot()
    RecurringSlotOwnershipRequest.objects.create(
        organization=organization,
        requested_by=member,
        slot=slot,
        previous_organization=previous_owner,
    )

    response = member_client.get(reverse("api-recurring-slot-request-list"))

    assert response.status_code == status.HTTP_200_OK
    results = response.json()["results"]
    assert {next(iter(item)) for item in results} == {"creation", "ownership"}


def test_recurring_slots_are_listed_publicly(slot: RecurringSlot) -> None:
    response = APIClient().get(reverse("api-recurring-slot-list"))

    assert response.status_code == status.HTTP_200_OK
    [listed] = response.json()["results"]
    assert listed["id"] == slot.pk
    assert listed["weekdays"] == [2]
    assert listed["frequency"] == "weekly"
    assert listed["recipe"]["kind"] == "uploads"
    assert listed["description"] == "Weekly Wed"
