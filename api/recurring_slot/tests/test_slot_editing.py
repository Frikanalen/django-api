from datetime import date, timedelta

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Organization, User
from scheduling.models import FillRule, RecurringSlot

pytestmark = pytest.mark.django_db


def payload(organization: Organization | None, **changes) -> dict:
    return {
        "organization": organization.pk if organization else None,
        "frequency": "weekly",
        "interval": 1,
        "weekdays": [4],
        "monthWeeks": [],
        "startTime": "20:00",
        "duration": "01:00:00",
        "startsOn": "2026-01-01",
        "endsOn": None,
        **changes,
    }


def error_attrs(response) -> set[str]:
    return {error["attr"] for error in response.json()["errors"]}


def other_organization(name: str = "Other organization", **fields) -> Organization:
    editor = User.objects.create(email=f"{name.replace(' ', '-').lower()}@example.test")
    return Organization.objects.create(name=name, editor=editor, fkmember=True, **fields)


def test_members_cannot_create_slots(member_client: APIClient, organization: Organization):
    response = member_client.post(
        reverse("api-recurring-slot-list"), payload(organization), format="json"
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_members_cannot_change_or_delete_slots(member_client: APIClient, slot: RecurringSlot):
    url = reverse("api-recurring-slot-detail", args=[slot.pk])
    assert member_client.patch(url, {"startTime": "19:00"}, format="json").status_code == 403
    assert member_client.delete(url).status_code == 403


def test_staff_create_a_slot_with_the_owners_standard_rule(
    staff_client: APIClient, organization: Organization
):
    response = staff_client.post(
        reverse("api-recurring-slot-list"), payload(organization), format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    slot = RecurringSlot.objects.get(pk=response.data["id"])
    assert slot.weekdays == [4]
    assert slot.default_fill_rule.organization == organization
    assert slot.default_fill_rule.mode == FillRule.Mode.FILL
    assert response.json()["organizationName"] == organization.name
    assert response.json()["defaultFillRule"]["id"] == slot.default_fill_rule_id

    # A second slot reuses the same rule rather than piling up copies.
    second = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(organization, weekdays=[5]),
        format="json",
    )
    assert second.json()["defaultFillRule"]["id"] == slot.default_fill_rule_id


def test_staff_can_create_a_slot_without_automatic_filling(
    staff_client: APIClient, organization: Organization
):
    response = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(organization, defaultFillRuleId=None),
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert response.json()["defaultFillRule"] is None


def test_a_rule_must_belong_to_the_owner(staff_client: APIClient, organization: Organization):
    foreign_rule = FillRule.objects.create(
        organization=other_organization(),
        name="Not theirs",
        pool=FillRule.Pool.ORGANIZATION,
        ordering=FillRule.Ordering.LATEST,
    )
    response = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(organization, defaultFillRuleId=foreign_rule.pk),
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "default_fill_rule_id" in error_attrs(response)


def test_suspended_organizations_get_no_slots(staff_client: APIClient):
    rogue = other_organization("Rogue", is_rogue=True)
    response = staff_client.post(reverse("api-recurring-slot-list"), payload(rogue), format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "organization" in error_attrs(response)


def test_a_suspended_owners_existing_slot_can_still_be_moved(
    staff_client: APIClient, organization: Organization, slot: RecurringSlot
):
    organization.is_rogue = True
    organization.save()
    response = staff_client.patch(
        reverse("api-recurring-slot-detail", args=[slot.pk]),
        {"startTime": "08:00"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data


def test_overlapping_slots_are_refused(staff_client: APIClient, slot: RecurringSlot):
    # `slot` is Wednesdays 18:00-19:00.
    response = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(other_organization(), weekdays=[2], startTime="18:30"),
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Overlaps Slot organization" in response.json()["errors"][0]["detail"]


def test_back_to_back_slots_are_fine(staff_client: APIClient, slot: RecurringSlot):
    response = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(other_organization(), weekdays=[2], startTime="19:00"),
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data


def test_slots_that_never_meet_dont_conflict(staff_client: APIClient, slot: RecurringSlot):
    slot.ends_on = date(2026, 2, 1)
    slot.save()
    response = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(other_organization(), weekdays=[2], startTime="18:00"),
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data


def test_suspended_owners_slots_dont_block(
    staff_client: APIClient, organization: Organization, slot: RecurringSlot
):
    organization.is_rogue = True
    organization.save()
    response = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(other_organization(), weekdays=[2], startTime="18:00"),
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data


def test_moving_a_slot_does_not_conflict_with_itself(staff_client: APIClient, slot: RecurringSlot):
    response = staff_client.patch(
        reverse("api-recurring-slot-detail", args=[slot.pk]),
        {"startTime": "18:30", "duration": "02:00:00"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    slot.refresh_from_db()
    assert slot.duration == timedelta(hours=2)


def test_a_new_owner_gets_their_own_rule(
    staff_client: APIClient, slot: RecurringSlot, rule: FillRule
):
    new_owner = other_organization()
    response = staff_client.patch(
        reverse("api-recurring-slot-detail", args=[slot.pk]),
        {"organization": new_owner.pk},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    slot.refresh_from_db()
    assert slot.organization == new_owner
    assert slot.default_fill_rule.organization == new_owner


def test_moving_keeps_the_rule(staff_client: APIClient, slot: RecurringSlot, rule: FillRule):
    staff_client.patch(
        reverse("api-recurring-slot-detail", args=[slot.pk]),
        {"weekdays": [3]},
        format="json",
    )
    slot.refresh_from_db()
    assert slot.default_fill_rule == rule


def test_weekly_slots_need_a_weekday(staff_client: APIClient, organization: Organization):
    response = staff_client.post(
        reverse("api-recurring-slot-list"), payload(organization, weekdays=[]), format="json"
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "weekdays" in error_attrs(response)


def test_staff_delete_a_slot(staff_client: APIClient, slot: RecurringSlot):
    response = staff_client.delete(reverse("api-recurring-slot-detail", args=[slot.pk]))
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not RecurringSlot.objects.filter(pk=slot.pk).exists()


def test_fill_rules_are_listed_for_staff_and_own_members(
    staff_client: APIClient, member_client: APIClient, rule: FillRule
):
    foreign = FillRule.objects.create(
        organization=other_organization(),
        name="Not theirs",
        pool=FillRule.Pool.ORGANIZATION,
        ordering=FillRule.Ordering.LATEST,
    )
    url = reverse("api-fill-rule-list")
    staff_ids = {r["id"] for r in staff_client.get(url).data["results"]}
    assert staff_ids == {rule.pk, foreign.pk}
    filtered = staff_client.get(url, {"organization": foreign.organization_id}).data
    assert [r["id"] for r in filtered["results"]] == [foreign.pk]
    member_ids = {r["id"] for r in member_client.get(url).data["results"]}
    assert member_ids == {rule.pk}
    assert APIClient().get(url).status_code in (401, 403)


def test_staff_create_a_jukebox_slot(staff_client: APIClient, organization: Organization, settings):
    settings.STATION_ORGANIZATION_ID = other_organization("Frikanalen").pk
    response = staff_client.post(
        reverse("api-recurring-slot-list"),
        # The owner is ignored: a jukebox slot is the station's.
        payload(organization, jukebox=True),
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    body = response.json()
    assert body["organization"] is None
    assert body["defaultFillRule"]["pool"] == "filler"
    assert "jukebox" not in body
    rule = RecurringSlot.objects.get(pk=body["id"]).default_fill_rule
    assert rule.organization_id == settings.STATION_ORGANIZATION_ID
    assert rule.mode == FillRule.Mode.FILL

    # Every jukebox slot shares the one rule.
    second = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(None, jukebox=True, weekdays=[5]),
        format="json",
    )
    assert second.json()["defaultFillRule"]["id"] == rule.pk


def test_a_members_slot_can_become_a_jukebox_slot(
    staff_client: APIClient, slot: RecurringSlot, settings
):
    settings.STATION_ORGANIZATION_ID = other_organization("Frikanalen").pk
    response = staff_client.patch(
        reverse("api-recurring-slot-detail", args=[slot.pk]), {"jukebox": True}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    slot.refresh_from_db()
    assert slot.organization is None
    assert slot.default_fill_rule.pool == FillRule.Pool.FILLER


def test_a_jukebox_slot_needs_the_station_organization(staff_client: APIClient, settings):
    settings.STATION_ORGANIZATION_ID = 999_999
    response = staff_client.post(
        reverse("api-recurring-slot-list"), payload(None, jukebox=True), format="json"
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert error_attrs(response) == {"jukebox"}
