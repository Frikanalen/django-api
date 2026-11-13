from datetime import date, timedelta

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Category, Organization, Series, User
from programming.models import Recipe
from programming.services import uploads_recipe
from scheduling.models import RecurringSlot

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


def test_staff_create_a_slot_with_the_owners_standard_recipe(
    staff_client: APIClient, organization: Organization
):
    response = staff_client.post(
        reverse("api-recurring-slot-list"), payload(organization), format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    slot = RecurringSlot.objects.get(pk=response.data["id"])
    assert slot.weekdays == [4]
    assert slot.recipe == uploads_recipe(organization)
    assert response.json()["organizationName"] == organization.name
    assert response.json()["recipe"]["kind"] == "uploads"

    # A second slot reuses the same recipe rather than piling up copies.
    second = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(organization, weekdays=[5]),
        format="json",
    )
    assert second.json()["recipe"]["id"] == slot.recipe_id


def test_staff_can_create_a_slot_without_automatic_filling(
    staff_client: APIClient, organization: Organization
):
    response = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(organization, recipe=None),
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert response.json()["recipe"] is None


def test_member_recipes_need_an_owner(staff_client: APIClient):
    response = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(None, recipe={"kind": "uploads"}),
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "recipe" in error_attrs(response)


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


def test_a_new_owner_gets_their_own_recipe(staff_client: APIClient, slot: RecurringSlot):
    new_owner = other_organization()
    response = staff_client.patch(
        reverse("api-recurring-slot-detail", args=[slot.pk]),
        {"organization": new_owner.pk},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    slot.refresh_from_db()
    assert slot.organization == new_owner
    assert slot.recipe == uploads_recipe(new_owner)


def test_moving_keeps_the_recipe(staff_client: APIClient, slot: RecurringSlot, recipe: Recipe):
    staff_client.patch(
        reverse("api-recurring-slot-detail", args=[slot.pk]),
        {"weekdays": [3]},
        format="json",
    )
    slot.refresh_from_db()
    assert slot.recipe == recipe


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


def test_staff_create_a_themed_jukebox_slot(
    staff_client: APIClient, organization: Organization, settings
):
    settings.STATION_ORGANIZATION_ID = other_organization("Frikanalen").pk
    culture = Category.objects.create(id=1, name="Kultur")
    recipe = {
        "kind": "jukebox",
        "name": "Kulturnatt",
        "categories": [culture.pk],
        "longest": "00:30:00",
        "olderThanDays": 365,
    }
    response = staff_client.post(
        reverse("api-recurring-slot-list"), payload(None, recipe=recipe), format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    body = response.json()
    assert body["organization"] is None
    assert body["recipe"] == {
        "id": body["recipe"]["id"],
        "kind": "jukebox",
        "description": body["recipe"]["description"],
        "series": None,
        "seriesName": None,
        "name": "Kulturnatt",
        "categories": [culture.pk],
        "shortest": None,
        "longest": "00:30:00",
        "newerThanDays": None,
        "olderThanDays": 365,
    }
    assert "Kulturnatt" in body["recipe"]["description"]
    saved = Recipe.objects.get(pk=body["recipe"]["id"])
    assert saved.organization_id == settings.STATION_ORGANIZATION_ID

    # A jukebox slot is the station's: it can't also have an owner.
    refused = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(organization, recipe={"kind": "jukebox"}, weekdays=[5]),
        format="json",
    )
    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    assert error_attrs(refused) == {"organization"}


def test_a_members_slot_can_become_a_jukebox_slot(
    staff_client: APIClient, slot: RecurringSlot, settings
):
    settings.STATION_ORGANIZATION_ID = other_organization("Frikanalen").pk
    response = staff_client.patch(
        reverse("api-recurring-slot-detail", args=[slot.pk]),
        {"organization": None, "recipe": {"kind": "jukebox"}},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    slot.refresh_from_db()
    assert slot.organization is None
    assert response.json()["recipe"]["kind"] == "jukebox"


def test_a_jukebox_slot_needs_the_station_organization(staff_client: APIClient, settings):
    settings.STATION_ORGANIZATION_ID = 999_999
    response = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(None, recipe={"kind": "jukebox"}),
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert error_attrs(response) == {"kind"}


def test_a_jukebox_theme_must_make_sense(staff_client: APIClient, settings):
    settings.STATION_ORGANIZATION_ID = other_organization("Frikanalen").pk
    response = staff_client.post(
        reverse("api-recurring-slot-list"),
        payload(None, recipe={"kind": "jukebox", "shortest": "01:00:00", "longest": "00:10:00"}),
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_staff_point_a_slot_at_a_series(staff_client, organization, slot) -> None:
    series = Series.objects.create(organization=organization, name="Ukens program")
    url = reverse("api-recurring-slot-detail", args=[slot.pk])

    response = staff_client.patch(
        url, {"recipe": {"kind": "series", "series": series.pk}}, format="json"
    )

    assert response.status_code == 200, response.content
    recipe = response.json()["recipe"]
    assert (recipe["kind"], recipe["series"], recipe["seriesName"]) == (
        "series",
        series.pk,
        "Ukens program",
    )

    theirs = Series.objects.create(organization=other_organization(), name="Theirs")
    response = staff_client.patch(
        url, {"recipe": {"kind": "series", "series": theirs.pk}}, format="json"
    )
    assert response.status_code == 400
    assert error_attrs(response) == {"series"}
