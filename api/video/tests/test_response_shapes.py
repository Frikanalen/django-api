"""
Wire-format tests for the video endpoints.

The frontend consumes the rendered JSON, not the serializer's internal
representation, so these tests assert on response.json(): that includes
the CamelCaseJSONRenderer pass, which rewrites every key in the payload
- including the variant keys of the `files` dict ('large_thumb' becomes
'largeThumb' on the wire).
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from fk.models import Category, Organization, User, Video, VideoFile, VideoFileVariant

pytestmark = pytest.mark.django_db

MEDIA = "https://frikanalen.no/media"

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
        description="A header\n\nA description",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=1, seconds=2, milliseconds=300),
        proper_import=True,
        publish_on_web=True,
        ref_url="https://example.test/ref",
        uploaded_time=UPLOADED,
    )
    video.categories.add(category)
    for variant, filename in (
        (VideoFileVariant.ORIGINAL, "master.mp4"),
        (VideoFileVariant.BROADCAST, "master.dv"),
        (VideoFileVariant.LARGE_THUMB, "thumb.jpg"),
        (VideoFileVariant.THEORA, "video.ogv"),
        (VideoFileVariant.WEBM_MED, "video.webm"),
    ):
        VideoFile.objects.create(video=video, variant=variant, filename=filename)
    # created_time/updated_time are auto-set; pin them so the rendered
    # timestamps are reproducible.
    Video.objects.filter(pk=video.pk).update(created_time=CREATED, updated_time=UPDATED)
    return video


def expected_video_json(video: Video) -> dict:
    # Both FKs are nullable on the model; the fixture always sets them,
    # and a missing one should fail here rather than as an attribute
    # error halfway through building the expectation.
    organization = video.organization
    assert organization is not None
    editor = organization.editor
    assert editor is not None
    return {
        "id": video.pk,
        "name": "Shape test video",
        "description": "A header\n\nA description",
        "files": {
            "original": {
                "url": f"{MEDIA}/{video.pk}/original/master.mp4",
                "mimeType": "application/octet-stream",
            },
            "broadcast": {
                "url": f"{MEDIA}/{video.pk}/broadcast/master.dv",
                "mimeType": "video/DV",
            },
            "largeThumb": {
                "url": f"{MEDIA}/{video.pk}/large_thumb/thumb.jpg",
                "mimeType": "image/jpeg",
            },
            "theora": {
                "url": f"{MEDIA}/{video.pk}/theora/video.ogv",
                "mimeType": "video/ogg",
            },
            "webmMed": {
                "url": f"{MEDIA}/{video.pk}/webm_med/video.webm",
                "mimeType": "video/webm",
            },
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
        "series": None,
        "episodeNumber": None,
        "duration": "00:01:02.300000",
        "durationSec": 62.3,
        "categories": ["News"],
        "framerate": 25000,
        "properImport": True,
        "hasTonoRecords": False,
        "publishOnWeb": True,
        "isFiller": False,
        "refUrl": "https://example.test/ref",
        "spokenLanguage": "no",
        "minimumAge": None,
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


def test_unpublished_and_broken_videos_have_public_detail_pages(
    editor: User, organization: Organization
) -> None:
    """Video detail is unrestricted: proper_import/publish_on_web hide
    a video from the default list, not from direct retrieval."""
    hidden = Video.objects.create(
        name="Hidden video",
        creator=editor,
        organization=organization,
        proper_import=False,
        publish_on_web=False,
    )

    response = APIClient().get(reverse("api-video-detail", args=[hidden.pk]))

    assert response.status_code == 200
    assert response.json()["name"] == "Hidden video"


def test_video_list_hides_improper_imports_by_default(
    editor: User, organization: Organization
) -> None:
    Video.objects.create(
        name="Broken import",
        creator=editor,
        organization=organization,
        proper_import=False,
    )
    listed = Video.objects.create(
        name="Proper import",
        creator=editor,
        organization=organization,
        proper_import=True,
    )

    response = APIClient().get(reverse("api-video-list"))

    assert [item["id"] for item in response.json()["results"]] == [listed.pk]


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


def test_a_dash_manifest_is_offered_under_its_own_files_key(video: Video) -> None:
    # 'dash' is one word, so it survives the camel-case renderer intact --
    # unlike 'large_thumb'. The frontend reads the manifest URL from here.
    VideoFile.objects.create(video=video, variant=VideoFileVariant.DASH, filename="manifest.mpd")

    payload = APIClient().get(reverse("api-video-detail", args=[video.pk])).json()

    assert payload["files"]["dash"] == {
        "url": f"{MEDIA}/{video.pk}/dash/manifest.mpd",
        "mimeType": "application/dash+xml",
    }
