"""
Wire-format tests for the video endpoints.

The frontend consumes the rendered JSON, not the serializer's internal
representation, so these tests assert on response.json(): that includes
the CamelCaseJSONRenderer pass, which rewrites every key in the payload
- including the fsname keys of the `files` dict ('large_thumb' becomes
'largeThumb' on the wire).
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from fk.models import Category, FileFormat, Organization, User, Video, VideoFile

pytestmark = pytest.mark.django_db

MEDIA = "https://upload.frikanalen.no/media"

CREATED = datetime(2015, 1, 1, 10, 0, tzinfo=UTC)
UPDATED = datetime(2015, 1, 2, 10, 0, tzinfo=UTC)
UPLOADED = datetime(2015, 1, 3, 10, 0, tzinfo=UTC)


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    # Category.id is a plain IntegerField primary key, not an AutoField,
    # so an explicit id is mandatory.
    category = Category.objects.create(id=1, name="News")
    video = Video.objects.create(
        name="Shape test video",
        header="A header",
        description="A description",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=1, seconds=2, milliseconds=300),
        proper_import=True,
        publish_on_web=True,
        ref_url="https://example.test/ref",
        uploaded_time=UPLOADED,
    )
    video.categories.add(category)
    for fsname, filename in (
        ("original", "master.mp4"),
        ("large_thumb", "thumb.jpg"),
        ("theora", "video.ogv"),
    ):
        VideoFile.objects.create(
            video=video,
            format=FileFormat.objects.create(fsname=fsname),
            filename=filename,
        )
    # created_time/updated_time are auto-set; pin them so the rendered
    # timestamps are reproducible.
    Video.objects.filter(pk=video.pk).update(created_time=CREATED, updated_time=UPDATED)
    return video


def expected_video_json(video: Video) -> dict:
    organization = video.organization
    editor = organization.editor
    return {
        "id": video.pk,
        "name": "Shape test video",
        "header": "A header",
        "description": "A description",
        "files": {
            "original": f"{MEDIA}/{video.pk}/original/master.mp4",
            "largeThumb": f"{MEDIA}/{video.pk}/large_thumb/thumb.jpg",
            "theora": f"{MEDIA}/{video.pk}/theora/video.ogv",
        },
        "creator": editor.email,
        "organization": {
            "id": organization.pk,
            "name": "Video test organization",
            "homepage": None,
            "description": "",
            "postalAddress": None,
            "streetAddress": None,
            "editorId": editor.pk,
            "editorName": "Ada Lovelace",
            "editorEmail": editor.email,
            "editorMsisdn": None,
            "fkmember": False,
        },
        "duration": "00:01:02.300000",
        "durationSec": 62.3,
        "categories": ["News"],
        "framerate": 25000,
        "properImport": True,
        "hasTonoRecords": False,
        "publishOnWeb": True,
        "isFiller": False,
        "refUrl": "https://example.test/ref",
        "createdTime": "2015-01-01T10:00:00Z",
        "updatedTime": "2015-01-02T10:00:00Z",
        "uploadedTime": "2015-01-03T10:00:00Z",
        "ogvUrl": f"{MEDIA}/{video.pk}/theora/video.ogv",
        "largeThumbnailUrl": f"{MEDIA}/{video.pk}/large_thumb/thumb.jpg",
    }


def test_video_detail_shape(video: Video) -> None:
    response = APIClient().get(reverse("api-video-detail", args=[video.pk]))

    assert response.status_code == 200
    assert response.json() == expected_video_json(video)


def test_video_list_wraps_the_same_shape_in_a_pagination_envelope(video: Video) -> None:
    response = APIClient().get(reverse("api-video-list"))

    assert response.status_code == 200
    assert response.json() == {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [expected_video_json(video)],
    }


def test_video_list_orders_newest_first(editor: User, organization: Organization) -> None:
    videos = [
        Video.objects.create(
            name=f"Video {index}",
            creator=editor,
            organization=organization,
            proper_import=True,
        )
        for index in range(3)
    ]

    response = APIClient().get(reverse("api-video-list"))

    assert [item["id"] for item in response.json()["results"]] == [
        video.pk for video in reversed(videos)
    ]


def test_video_without_files_uses_fallback_urls(editor: User, organization: Organization) -> None:
    video = Video.objects.create(
        name="Bare video",
        creator=editor,
        organization=organization,
        proper_import=True,
    )

    payload = APIClient().get(reverse("api-video-detail", args=[video.pk])).json()

    assert payload["files"] == {}
    assert payload["ogvUrl"] is None
    assert payload["largeThumbnailUrl"] == "/static/default_large_thumbnail.png"
