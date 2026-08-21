"""
How the broadcast-week policy reaches the frontend: the read-only
/api/scheduling/policy endpoint, the `displaceable` flag on schedule
items, and both being visible to drf-spectacular.

The frontend derives every UI state from these -- frozen, open,
replaceable filler, not yet drafted -- rather than re-implementing the
week arithmetic of agenda.scheduling.policy.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.test import APIClient

from agenda.scheduling import policy
from fk.models import Scheduleitem, SchedulePurpose, WeeklySlot

pytestmark = pytest.mark.django_db

OSLO = ZoneInfo("Europe/Oslo")


def test_the_policy_endpoint_is_public_and_states_the_boundaries(
    now_in_the_drafting_week: datetime,
) -> None:
    response = APIClient().get(reverse("api-scheduling-policy"))

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert parse_datetime(payload["freezeBoundary"]) == policy.freeze_boundary(
        now_in_the_drafting_week
    )
    assert parse_datetime(payload["schedulingHorizon"]) == policy.scheduling_horizon(
        now_in_the_drafting_week
    )
    assert parse_datetime(payload["serverTime"]) == now_in_the_drafting_week


def test_the_boundaries_are_rendered_in_oslo_time(now_in_the_drafting_week: datetime) -> None:
    """Members read these as wall-clock deadlines; the offset must be
    Norway's, not UTC."""
    payload = APIClient().get(reverse("api-scheduling-policy")).json()

    assert payload["freezeBoundary"] == "2014-12-29T00:00:00+01:00"
    assert payload["schedulingHorizon"] == "2015-01-05T00:00:00+01:00"


def test_the_policy_includes_recurring_weekly_slots() -> None:
    purpose = SchedulePurpose.objects.create(
        name="Member premiere",
        type=SchedulePurpose.TYPE.videos,
        strategy=SchedulePurpose.STRATEGY.latest,
    )
    slot = WeeklySlot.objects.create(
        purpose=purpose,
        day=4,
        start_time="18:15",
        duration=timedelta(hours=1, minutes=30),
    )

    payload = APIClient().get(reverse("api-scheduling-policy")).json()

    assert payload["weeklySlots"] == [
        {
            "id": slot.pk,
            "purpose": {"id": purpose.pk, "name": "Member premiere"},
            "day": 4,
            "startTime": "18:15:00",
            "duration": "01:30:00",
        }
    ]


def test_schedule_items_say_whether_they_are_displaceable(schedule_item_factory) -> None:
    slot = WeeklySlot.objects.create(
        day=3,
        start_time="12:00",
        duration=timedelta(hours=1),
    )
    filler = schedule_item_factory(starttime=datetime(2015, 1, 1, 10, tzinfo=OSLO))
    filler.schedulereason = Scheduleitem.REASON_JUKEBOX
    filler.save()
    pick = schedule_item_factory(starttime=datetime(2015, 1, 1, 12, tzinfo=OSLO))
    pick.weekly_slot = slot
    pick.save()

    response = APIClient().get(reverse("api-scheduleitem-list"), {"date": "2015-01-01"})

    by_id = {
        entry["id"]: (entry["displaceable"], entry["weeklySlot"])
        for entry in response.json()["results"]
    }
    assert by_id == {filler.pk: (True, None), pick.pk: (False, slot.pk)}


def test_the_openapi_schema_documents_the_policy(db) -> None:
    """drf-spectacular must see the endpoint and the flag: the schema
    names the operation, the response component's camelized fields, and
    the displaceable property with its description."""
    schema = APIClient().get(reverse("schema")).content.decode()

    assert "scheduling_policy_retrieve" in schema
    assert "/api/scheduling/policy" in schema
    for field in (
        "freezeBoundary",
        "schedulingHorizon",
        "serverTime",
        "weeklySlots",
        "weeklySlot",
        "displaceable",
    ):
        assert field in schema
    assert "jukebox filler" in schema
