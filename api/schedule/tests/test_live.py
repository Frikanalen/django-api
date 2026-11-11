from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from api.schedule.serializers import ScheduleitemReadSerializer
from fk.models import LiveSource, LiveTransmission, Scheduleitem

pytestmark = pytest.mark.django_db
START = datetime(2015, 1, 1, 10, tzinfo=ZoneInfo("Europe/Oslo"))


@pytest.mark.parametrize("live", [False, True])
def test_a_live_airing_names_its_videos_source(
    authenticated_client,
    schedule_item_factory,
    organization,
    live,
):
    entry = schedule_item_factory(starttime=START)
    expected = None
    if live:
        source = LiveSource.objects.create(name="Studio", organization=organization)
        LiveTransmission.objects.create(video=entry.video, source=source)
        Scheduleitem.objects.filter(pk=entry.pk).update(is_live=True)
        expected = {"id": source.pk, "stream_id": str(source.stream_id)}
    detail = authenticated_client.get(reverse("api-scheduleitem-detail", args=[entry.pk]))
    listing = authenticated_client.get(reverse("api-scheduleitem-list"), {"date": "2015-01-01"})
    assert detail.status_code == listing.status_code == 200
    for item in [detail.data, listing.data["results"][0]]:
        assert item["is_live"] is live
        assert item["live_source"] == expected
        assert item["video"]["id"] == entry.video_id
        assert item["duration"] == "01:00:00"
    body = detail.json()
    assert "liveSource" in body
    assert not {"liveTransmission", "displaceable", "weeklySlot"} & body.keys()


def test_an_airing_from_the_file_names_no_source(
    authenticated_client, schedule_item_factory, organization
):
    entry = schedule_item_factory(starttime=START)
    source = LiveSource.objects.create(name="Studio", organization=organization)
    LiveTransmission.objects.create(video=entry.video, source=source)

    detail = authenticated_client.get(reverse("api-scheduleitem-detail", args=[entry.pk]))

    assert detail.data["live_source"] is None


def test_live_relations_are_eager_loaded(
    schedule_item_factory,
    organization,
    django_assert_num_queries,
):
    from api.schedule.views import ScheduleitemViewSet

    entries = [schedule_item_factory(starttime=START) for _ in range(3)]
    source = LiveSource.objects.create(name="Studio", organization=organization)
    LiveTransmission.objects.create(video=entries[0].video, source=source)
    Scheduleitem.objects.filter(pk__in=[e.pk for e in entries]).update(is_live=True)
    queryset = ScheduleitemViewSet.queryset.filter(pk__in=[entry.pk for entry in entries])
    items = list(queryset)
    with django_assert_num_queries(0):
        data = ScheduleitemReadSerializer(items, many=True).data
    assert [item["live_source"]["id"] for item in data] == [source.pk] * 3
