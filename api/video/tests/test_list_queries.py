"""The video list must cost a fixed number of queries, however long the page.

VideoSerializer nests the organization and its editor, and reads the
creator, series, categories, live source and files of every video. Each of
those is a query per video unless the list view fetches it up front, which
it once did not: a default page of 50 took some 350 queries.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from fk.models import Category, LiveSource, Organization, Series, User, Video
from fk.models.live import LiveTransmission
from fk.models.video_file import VideoFile, VideoFileVariant


def _add_videos(count: int, start: int) -> None:
    category, _ = Category.objects.get_or_create(id=901, defaults={"name": "Query count"})
    for i in range(start, start + count):
        # A separate organization, editor and creator for each video, so
        # nothing is shared that a per-video query could hit in a cache.
        editor = User.objects.create(email=f"editor{i}@example.test")
        organization = Organization.objects.create(name=f"Org {i}", editor=editor, fkmember=True)
        series = Series.objects.create(name=f"Series {i}", organization=organization)
        video = Video.objects.create(
            name=f"Video {i}",
            creator=editor,
            organization=organization,
            series=series,
            proper_import=True,
        )
        video.categories.add(category)
        VideoFile.objects.create(
            video=video, variant=VideoFileVariant.LARGE_THUMB, filename=f"{i}.jpg"
        )
        source = LiveSource.objects.create(organization=organization, name=f"Studio {i}")
        LiveTransmission.objects.create(video=video, source=source)


def _count_list_queries(client: APIClient) -> int:
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/api/videos", {"limit": 100})
    assert response.status_code == 200
    return len(queries)


@pytest.mark.django_db
def test_video_list_query_count_does_not_grow_with_page_length() -> None:
    client = APIClient()

    _add_videos(2, start=0)
    short_page = _count_list_queries(client)

    _add_videos(10, start=2)
    long_page = _count_list_queries(client)

    assert long_page == short_page
