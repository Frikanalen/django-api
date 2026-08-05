"""
Wire-format tests for the videofile endpoints, asserting on the rendered
(camelCase) JSON the legacy frontend and upload tooling consume.
"""

from datetime import UTC, datetime

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from fk.models import FileFormat, Organization, User, Video, VideoFile

pytestmark = pytest.mark.django_db

CREATED = datetime(2015, 1, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def video() -> Video:
    editor = User.objects.create(email="videofile-editor@example.test")
    organization = Organization.objects.create(name="Videofile org", editor=editor)
    return Video.objects.create(
        name="Videofile test video",
        creator=editor,
        organization=organization,
        proper_import=True,
    )


def make_file(video: Video, fsname: str = "original", **fields) -> VideoFile:
    video_file = VideoFile.objects.create(
        video=video,
        format=FileFormat.objects.get_or_create(fsname=fsname)[0],
        filename=fields.pop("filename", "file.mp4"),
        **fields,
    )
    VideoFile.objects.filter(pk=video_file.pk).update(created_time=CREATED)
    return video_file


def test_videofile_detail_shape(video: Video) -> None:
    video_file = make_file(video, filename="master.mp4", integrated_lufs=-23.0)

    response = APIClient().get(reverse("api-videofile-detail", args=[video_file.pk]))

    assert response.status_code == 200
    assert response.json() == {
        "id": video_file.pk,
        "createdTime": "2015-01-01T10:00:00Z",
        "video": video.pk,
        "format": video_file.format.pk,
        "filename": "master.mp4",
        "integratedLufs": -23.0,
        "truepeakLufs": None,
    }


def test_videofile_list_does_not_hide_files_of_improper_videos(video: Video) -> None:
    """Unlike the video list, the videofile list applies no visibility
    filtering: files of unpublished/broken videos are listed too."""
    hidden_video = Video.objects.create(
        name="Hidden video",
        creator=video.creator,
        organization=video.organization,
        proper_import=False,
        publish_on_web=False,
    )
    hidden_file = make_file(hidden_video, filename="hidden.mp4")

    response = APIClient().get(reverse("api-videofile-list"))

    assert hidden_file.pk in [item["id"] for item in response.json()["results"]]


def test_videofile_list_envelope_and_ordering(video: Video) -> None:
    first_video_file = make_file(video, fsname="original")
    second_video_file = make_file(video, fsname="broadcast")
    newer_video = Video.objects.create(
        name="Newer video",
        creator=video.creator,
        organization=video.organization,
        proper_import=True,
    )
    newer_file = make_file(newer_video, fsname="original")

    response = APIClient().get(reverse("api-videofile-list"))
    payload = response.json()

    assert response.status_code == 200
    assert set(payload.keys()) == {"count", "next", "previous", "results"}
    assert payload["count"] == 3
    # Model ordering is (-video_id, -id): newest video first, then the
    # most recently added file within each video.
    assert [item["id"] for item in payload["results"]] == [
        newer_file.pk,
        second_video_file.pk,
        first_video_file.pk,
    ]
