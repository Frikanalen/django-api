from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from api.schedule.serializers import ScheduleitemReadSerializer
from fk.models import LiveSource, LiveTransmission

pytestmark = pytest.mark.django_db
START = datetime(2015, 1, 1, 10, tzinfo=ZoneInfo("Europe/Oslo"))


@pytest.mark.parametrize("live", [False, True])
def test_schedule_list_and_detail_include_optional_live_transmission(
    authenticated_client,
    schedule_item_factory,
    organization,
    live,
):
    entry = schedule_item_factory(starttime=START)
    expected = None
    if live:
        source = LiveSource.objects.create(name="Studio", organization=organization)
        transmission = LiveTransmission.objects.create(schedule_item=entry, source=source)
        expected = {"id": transmission.pk, "source": source.pk, "stream_id": str(source.stream_id)}
    detail = authenticated_client.get(reverse("api-scheduleitem-detail", args=[entry.pk]))
    listing = authenticated_client.get(reverse("api-scheduleitem-list"), {"date": "2015-01-01"})
    assert detail.status_code == listing.status_code == 200
    for item in [detail.data, listing.data["results"][0]]:
        assert item["live_transmission"] == expected
        assert item["video"]["id"] == entry.video_id
        assert item["duration"] == "01:00:00"
    assert "liveTransmission" in detail.json()


def test_live_relations_are_eager_loaded(
    schedule_item_factory,
    organization,
    django_assert_num_queries,
):
    from api.schedule.views import ScheduleitemViewSet

    entries = [schedule_item_factory(starttime=START) for _ in range(3)]
    source = LiveSource.objects.create(name="Studio", organization=organization)
    for entry in entries:
        LiveTransmission.objects.create(schedule_item=entry, source=source)
    queryset = ScheduleitemViewSet.queryset.filter(pk__in=[entry.pk for entry in entries])
    items = list(queryset)
    with django_assert_num_queries(0):
        data = ScheduleitemReadSerializer(items, many=True).data
    assert len(data) == 3


def test_api_update_cannot_change_live_attachment(
    authenticated_client,
    schedule_item_factory,
    organization,
):
    entry = schedule_item_factory(starttime=START)
    source = LiveSource.objects.create(name="Studio", organization=organization)
    transmission = LiveTransmission.objects.create(schedule_item=entry, source=source)
    response = authenticated_client.patch(
        reverse("api-scheduleitem-detail", args=[entry.pk]),
        {"live_transmission": None},
        format="json",
    )
    assert response.status_code == 200
    assert LiveTransmission.objects.get(schedule_item=entry).pk == transmission.pk
