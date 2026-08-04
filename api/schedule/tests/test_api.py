from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils.dateparse import parse_datetime, parse_duration
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIClient

from fk.models import Scheduleitem, Video


pytestmark = pytest.mark.django_db

OSLO = ZoneInfo("Europe/Oslo")


def oslo_datetime(hour: int, minute: int = 0) -> datetime:
    return datetime(2015, 1, 1, hour, minute, tzinfo=OSLO)


@pytest.fixture
def adjacent_schedule(
    schedule_item_factory: Callable[..., Scheduleitem],
) -> tuple[Scheduleitem, Scheduleitem]:
    return (
        schedule_item_factory(starttime=oslo_datetime(10)),
        schedule_item_factory(starttime=oslo_datetime(11)),
    )


def assert_schedule_conflict(response: Response, conflict: Scheduleitem) -> None:
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data == {
        "type": "validation_error",
        "errors": [
            {
                "code": "invalid",
                "detail": f"Conflict with '{conflict}'.",
                "attr": "duration",
            }
        ],
    }


@pytest.mark.parametrize(
    ("given_starttime", "returned_starttime"),
    [
        pytest.param(
            "2015-01-01T11:00:00Z",
            "2015-01-01T12:00:00+01:00",
            id="utc-in-winter",
        ),
        pytest.param(
            "2015-07-01T10:00:00Z",
            "2015-07-01T12:00:00+02:00",
            id="utc-in-summer",
        ),
        pytest.param(
            "2015-01-01T09:58:00+01:00",
            "2015-01-01T09:58:00+01:00",
            id="already-local",
        ),
    ],
)
def test_create_schedule_item_normalizes_starttime_to_oslo(
    authenticated_client: APIClient,
    video: Video,
    given_starttime: str,
    returned_starttime: str,
) -> None:
    response = authenticated_client.post(
        reverse("api-scheduleitem-list"),
        {
            "video": video.pk,
            "starttime": given_starttime,
            "duration": "58.312",
            "schedulereason": Scheduleitem.REASON_LEGACY,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["starttime"] == returned_starttime
    assert response.data["duration"] == "00:00:58.312000"
    assert response.data["video"] == video.pk

    created = Scheduleitem.objects.get(pk=response.data["id"])
    assert created.starttime == parse_datetime(given_starttime)
    assert created.duration == timedelta(seconds=58, milliseconds=312)


@pytest.mark.parametrize(
    ("starttime", "duration", "conflict_index"),
    [
        pytest.param(oslo_datetime(9, 59), timedelta(minutes=2), 0, id="overlaps-start"),
        pytest.param(oslo_datetime(10, 30), timedelta(minutes=10), 0, id="starts-inside"),
        pytest.param(oslo_datetime(10), timedelta(hours=1), 0, id="same-interval"),
        pytest.param(oslo_datetime(9, 30), timedelta(hours=3), 0, id="contains-existing"),
        pytest.param(oslo_datetime(11, 30), timedelta(minutes=10), 1, id="second-item"),
    ],
)
def test_create_schedule_item_rejects_overlaps(
    authenticated_client: APIClient,
    video: Video,
    adjacent_schedule: tuple[Scheduleitem, Scheduleitem],
    starttime: datetime,
    duration: timedelta,
    conflict_index: int,
) -> None:
    conflict = adjacent_schedule[conflict_index]
    conflict.refresh_from_db()

    response = authenticated_client.post(
        reverse("api-scheduleitem-list"),
        {
            "video": video.pk,
            "starttime": starttime.isoformat(),
            "duration": str(duration),
            "schedulereason": Scheduleitem.REASON_LEGACY,
        },
        format="json",
    )

    assert_schedule_conflict(response, conflict)
    assert Scheduleitem.objects.count() == 2


@pytest.mark.parametrize(
    "starttime",
    [
        pytest.param(oslo_datetime(9), id="ends-at-existing-start"),
        pytest.param(oslo_datetime(12), id="starts-at-existing-end"),
    ],
)
def test_create_schedule_item_allows_adjacent_intervals(
    authenticated_client: APIClient,
    video: Video,
    adjacent_schedule: tuple[Scheduleitem, Scheduleitem],
    starttime: datetime,
) -> None:
    response = authenticated_client.post(
        reverse("api-scheduleitem-list"),
        {
            "video": video.pk,
            "starttime": starttime.isoformat(),
            "duration": "01:00:00",
            "schedulereason": Scheduleitem.REASON_LEGACY,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert Scheduleitem.objects.count() == 3


@pytest.mark.parametrize(
    ("target_index", "changes"),
    [
        pytest.param(0, {"starttime": oslo_datetime(9).isoformat()}, id="move-before"),
        pytest.param(0, {"duration": "00:30:00"}, id="shorten-before-neighbor"),
        pytest.param(1, {"starttime": oslo_datetime(12).isoformat()}, id="move-after"),
    ],
)
def test_update_schedule_item_allows_non_overlapping_changes(
    authenticated_client: APIClient,
    adjacent_schedule: tuple[Scheduleitem, Scheduleitem],
    target_index: int,
    changes: dict[str, str],
) -> None:
    target = adjacent_schedule[target_index]

    response = authenticated_client.patch(
        reverse("api-scheduleitem-detail", args=[target.pk]),
        changes,
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    target.refresh_from_db()
    if starttime := changes.get("starttime"):
        assert target.starttime == parse_datetime(starttime)
        assert parse_datetime(response.data["starttime"]) == parse_datetime(starttime)
    if duration := changes.get("duration"):
        assert target.duration == parse_duration(duration)
        assert parse_duration(response.data["duration"]) == parse_duration(duration)


def test_update_schedule_item_allows_reason_only_change(
    authenticated_client: APIClient,
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    target = schedule_item_factory(starttime=oslo_datetime(10))
    original_starttime = target.starttime
    original_duration = target.duration

    response = authenticated_client.patch(
        reverse("api-scheduleitem-detail", args=[target.pk]),
        {"schedulereason": Scheduleitem.REASON_ADMIN},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    target.refresh_from_db()
    assert target.schedulereason == Scheduleitem.REASON_ADMIN
    assert target.starttime == original_starttime
    assert target.duration == original_duration


@pytest.mark.parametrize(
    ("target_index", "changes", "conflict_index"),
    [
        pytest.param(0, {"duration": "01:01:00"}, 1, id="extend-into-neighbor"),
        pytest.param(
            0,
            {"starttime": oslo_datetime(10, 30).isoformat()},
            1,
            id="move-first-into-second",
        ),
        pytest.param(
            1,
            {"starttime": oslo_datetime(10, 30).isoformat()},
            0,
            id="move-second-into-first",
        ),
        pytest.param(
            0,
            {"starttime": oslo_datetime(9).isoformat(), "duration": "03:00:00"},
            1,
            id="contain-neighbor",
        ),
    ],
)
def test_update_schedule_item_rejects_overlaps(
    authenticated_client: APIClient,
    adjacent_schedule: tuple[Scheduleitem, Scheduleitem],
    target_index: int,
    changes: dict[str, str],
    conflict_index: int,
) -> None:
    target = adjacent_schedule[target_index]
    conflict = adjacent_schedule[conflict_index]
    original_starttime = target.starttime
    original_duration = target.duration
    conflict.refresh_from_db()

    response = authenticated_client.patch(
        reverse("api-scheduleitem-detail", args=[target.pk]),
        changes,
        format="json",
    )

    assert_schedule_conflict(response, conflict)
    target.refresh_from_db()
    assert target.starttime == original_starttime
    assert target.duration == original_duration
