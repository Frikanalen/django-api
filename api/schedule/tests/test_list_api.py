from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIClient

from fk.models import Category, FileFormat, Scheduleitem, Video, VideoFile

pytestmark = pytest.mark.django_db

OSLO = ZoneInfo("Europe/Oslo")


def at(day: date, hour: int = 0, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=OSLO)


def result_ids(response: Response) -> list[int]:
    return [item["id"] for item in response.data["results"]]


@pytest.mark.parametrize(
    "query_params",
    [
        pytest.param({}, id="implicit"),
        pytest.param({"date": "today"}, id="explicit"),
    ],
)
def test_list_defaults_to_today(
    authenticated_client: APIClient,
    schedule_item_factory: Callable[..., Scheduleitem],
    query_params: dict[str, str],
) -> None:
    today = timezone.localdate()
    schedule_item_factory(starttime=at(today - timedelta(days=1), 23))
    expected = schedule_item_factory(starttime=at(today, 12))
    schedule_item_factory(starttime=at(today + timedelta(days=1)))

    response = authenticated_client.get(reverse("api-scheduleitem-list"), query_params)

    assert response.status_code == status.HTTP_200_OK
    assert result_ids(response) == [expected.pk]


def test_list_accepts_days_without_an_explicit_date(
    authenticated_client: APIClient,
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    today = timezone.localdate()
    expected = [
        schedule_item_factory(starttime=at(today, 12)),
        schedule_item_factory(starttime=at(today + timedelta(days=1), 12)),
    ]
    schedule_item_factory(starttime=at(today + timedelta(days=2)))

    response = authenticated_client.get(reverse("api-scheduleitem-list"), {"days": 2})

    assert response.status_code == status.HTTP_200_OK
    assert result_ids(response) == [item.pk for item in expected]


@pytest.mark.parametrize(
    "target_day",
    [
        pytest.param(date(2025, 3, 30), id="spring-forward"),
        pytest.param(date(2025, 10, 26), id="fall-back"),
    ],
)
def test_list_uses_oslo_calendar_day_across_dst_transitions(
    authenticated_client: APIClient,
    schedule_item_factory: Callable[..., Scheduleitem],
    target_day: date,
) -> None:
    one_minute = timedelta(minutes=1)
    schedule_item_factory(
        starttime=at(target_day - timedelta(days=1), 23, 59),
        duration=one_minute,
    )
    expected = [
        schedule_item_factory(starttime=at(target_day), duration=one_minute),
        schedule_item_factory(
            starttime=at(target_day, 23, 59),
            duration=one_minute,
        ),
    ]
    schedule_item_factory(
        starttime=at(target_day + timedelta(days=1)),
        duration=one_minute,
    )

    response = authenticated_client.get(
        reverse("api-scheduleitem-list"),
        {"date": target_day.isoformat()},
    )

    assert response.status_code == status.HTTP_200_OK
    assert result_ids(response) == [item.pk for item in expected]


@pytest.mark.parametrize(
    ("query_params", "error_attr"),
    [
        pytest.param({"date": "not-a-date"}, "date", id="invalid-date"),
        pytest.param({"days": "0"}, "days", id="zero-days"),
        pytest.param({"days": "-1"}, "days", id="negative-days"),
    ],
)
def test_list_rejects_invalid_window_parameters(
    authenticated_client: APIClient,
    query_params: dict[str, str],
    error_attr: str,
) -> None:
    response = authenticated_client.get(reverse("api-scheduleitem-list"), query_params)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["type"] == "validation_error"
    assert [error["attr"] for error in response.data["errors"]] == [error_attr]


@pytest.mark.parametrize(
    ("surrounding", "expected_indexes"),
    [
        pytest.param("false", [2, 3], id="disabled"),
        pytest.param("true", [1, 2, 3, 4], id="enabled"),
    ],
)
def test_list_optionally_includes_immediate_surrounding_items(
    authenticated_client: APIClient,
    schedule_item_factory: Callable[..., Scheduleitem],
    surrounding: str,
    expected_indexes: list[int],
) -> None:
    target_day = date(2015, 1, 2)
    items = [
        schedule_item_factory(starttime=at(target_day - timedelta(days=2), 12)),
        schedule_item_factory(starttime=at(target_day - timedelta(days=1), 23)),
        schedule_item_factory(starttime=at(target_day, 10)),
        schedule_item_factory(starttime=at(target_day, 11)),
        schedule_item_factory(starttime=at(target_day + timedelta(days=1))),
        schedule_item_factory(starttime=at(target_day + timedelta(days=2), 12)),
    ]

    response = authenticated_client.get(
        reverse("api-scheduleitem-list"),
        {"date": target_day.isoformat(), "surrounding": surrounding},
    )

    assert response.status_code == status.HTTP_200_OK
    assert result_ids(response) == [items[index].pk for index in expected_indexes]


@pytest.mark.parametrize(
    ("has_previous", "has_next"),
    [
        pytest.param(True, False, id="previous-only"),
        pytest.param(False, True, id="next-only"),
        pytest.param(False, False, id="no-neighbors"),
    ],
)
def test_surrounding_tolerates_missing_neighbors(
    authenticated_client: APIClient,
    schedule_item_factory: Callable[..., Scheduleitem],
    has_previous: bool,
    has_next: bool,
) -> None:
    target_day = date(2015, 1, 2)
    expected = []
    if has_previous:
        expected.append(schedule_item_factory(starttime=at(target_day - timedelta(days=1), 23)))
    expected.append(schedule_item_factory(starttime=at(target_day, 10)))
    if has_next:
        expected.append(schedule_item_factory(starttime=at(target_day + timedelta(days=1))))

    response = authenticated_client.get(
        reverse("api-scheduleitem-list"),
        {"date": target_day.isoformat(), "surrounding": "true"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert result_ids(response) == [item.pk for item in expected]


@pytest.mark.parametrize(
    ("ordering", "expected_indexes"),
    [
        pytest.param("starttime", [0, 1, 2], id="ascending"),
        pytest.param("-starttime", [2, 1, 0], id="descending"),
    ],
)
def test_list_orders_by_starttime(
    authenticated_client: APIClient,
    schedule_item_factory: Callable[..., Scheduleitem],
    ordering: str,
    expected_indexes: list[int],
) -> None:
    target_day = date(2015, 1, 2)
    items = [schedule_item_factory(starttime=at(target_day, hour)) for hour in (9, 10, 11)]

    response = authenticated_client.get(
        reverse("api-scheduleitem-list"),
        {"date": target_day.isoformat(), "ordering": ordering},
    )

    assert response.status_code == status.HTTP_200_OK
    assert result_ids(response) == [items[index].pk for index in expected_indexes]


def test_list_uses_schedule_page_size(
    authenticated_client: APIClient,
    schedule_item_factory: Callable[..., Scheduleitem],
) -> None:
    target_day = date(2015, 1, 2)
    start = at(target_day)
    for offset in range(201):
        schedule_item_factory(
            starttime=start + timedelta(minutes=offset),
            duration=timedelta(minutes=1),
        )

    response = authenticated_client.get(
        reverse("api-scheduleitem-list"),
        {"date": target_day.isoformat()},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 201
    assert len(response.data["results"]) == 200
    assert response.data["next"] is not None


def test_list_serializes_nested_video_details(
    authenticated_client: APIClient,
    schedule_item_factory: Callable[..., Scheduleitem],
    video: Video,
) -> None:
    target_day = date(2015, 1, 2)
    category = Category.objects.create(id=1, name="News")
    video.categories.add(category)
    file_format = FileFormat.objects.create(fsname="original")
    video_file = VideoFile.objects.create(
        video=video,
        format=file_format,
        filename="schedule-test.mp4",
    )
    item = schedule_item_factory(starttime=at(target_day, 10))

    response = authenticated_client.get(
        reverse("api-scheduleitem-list"),
        {"date": target_day.isoformat()},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["results"] == [
        {
            "id": item.pk,
            "video": {
                "id": video.pk,
                "name": video.name,
                "header": None,
                "description": None,
                "organization": {
                    "id": video.organization.pk,
                    "name": video.organization.name,
                    "description": "",
                },
                "categories": [category.name],
                "files": [
                    {
                        "id": video_file.pk,
                        "fsname": file_format.fsname,
                        "filename": video_file.filename,
                    }
                ],
            },
            "starttime": "2015-01-02T10:00:00+01:00",
            "endtime": "2015-01-02T11:00:00+01:00",
            "displaceable": False,
        }
    ]


def test_list_query_count_is_constant(
    authenticated_client: APIClient,
    schedule_item_factory: Callable[..., Scheduleitem],
    video: Video,
    django_assert_max_num_queries,
) -> None:
    target_day = date(2015, 1, 2)
    file_format = FileFormat.objects.create(fsname="original")
    VideoFile.objects.create(video=video, format=file_format, filename="schedule-test.mp4")
    for hour in range(10, 15):
        schedule_item_factory(starttime=at(target_day, hour))

    with django_assert_max_num_queries(4):
        response = authenticated_client.get(
            reverse("api-scheduleitem-list"),
            {"date": target_day.isoformat()},
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 5
