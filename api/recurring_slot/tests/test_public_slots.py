from datetime import date, timedelta

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from fk.models import Organization, User
from programming.services import jukebox_recipe
from scheduling.models import RecurringSlot

pytestmark = pytest.mark.django_db


def test_anyone_sees_the_slots_in_force_without_their_recipe(
    organization: Organization, slot: RecurringSlot
):
    rogue = Organization.objects.create(
        name="Rogue", editor=User.objects.create(email="rogue@example.test"), is_rogue=True
    )
    RecurringSlot.objects.create(
        organization=rogue,
        weekdays=[3],
        start_time="21:00",
        duration=timedelta(hours=1),
        starts_on=date(2026, 1, 1),
    )
    ended = RecurringSlot.objects.create(
        organization=organization,
        weekdays=[4],
        start_time="12:00",
        duration=timedelta(hours=1),
        starts_on=date(2020, 1, 1),
        ends_on=date(2021, 1, 1),
    )

    response = APIClient().get(reverse("api-recurring-slot-public"))

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": slot.pk,
            "organization": {"id": organization.pk, "name": organization.name, "fkmember": True},
            "frequency": "weekly",
            "interval": 1,
            "weekdays": [2],
            "monthWeeks": [],
            "startTime": "18:00:00",
            "duration": "01:00:00",
            "startsOn": "2026-01-01",
            "endsOn": None,
            "jukebox": False,
        }
    ]
    assert ended.pk not in [s["id"] for s in response.json()]


def test_the_station_jukebox_is_marked():
    station = Organization.objects.create(
        name="Frikanalen", editor=User.objects.create(email="station@example.test")
    )
    recipe = jukebox_recipe(station, name="Natt")
    RecurringSlot.objects.create(
        organization=None,
        recipe=recipe,
        weekdays=[0],
        start_time="02:00",
        duration=timedelta(hours=4),
        starts_on=date(2026, 1, 1),
    )

    [slot] = APIClient().get(reverse("api-recurring-slot-public")).json()

    assert slot["organization"] is None
    assert slot["jukebox"] is True
