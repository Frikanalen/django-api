"""
The ffprobe report on a video's original: written by ingest, read by its organization.

What matters most is that the report comes back exactly as it went in.
The rest of the API camelizes keys on the way out and snake_cases them on
the way in, and either would quietly turn ffprobe's output into something
ffprobe never said.
"""

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Organization, User, Video

pytestmark = pytest.mark.django_db

PROBE = {
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "89.000000", "nb_streams": 2},
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "r_frame_rate": "50/1",
            "field_order": "progressive",
            "tags": {"handler_name": "VideoHandler"},
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "sample_rate": "48000",
            "channels": 2,
        },
    ],
}


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    return Video.objects.create(name="Probed video", creator=editor, organization=organization)


@pytest.fixture
def ingest_client() -> APIClient:
    service = User.objects.create(email="ingest@example.test", is_superuser=True)
    client = APIClient()
    client.force_authenticate(user=service)
    return client


@pytest.fixture
def outsider_client() -> APIClient:
    client = APIClient()
    client.force_authenticate(user=User.objects.create(email="outsider@example.test"))
    return client


def url(video: Video) -> str:
    return reverse("api-video-media-metadata", args=[video.pk])


def test_an_unprobed_video_reads_as_version_zero(editor_client: APIClient, video: Video) -> None:
    response = editor_client.get(url(video))

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"version": 0, "data": {}}


def test_ingest_records_the_probe_with_its_keys_untouched(
    ingest_client: APIClient, editor_client: APIClient, video: Video
) -> None:
    response = ingest_client.put(url(video), {"version": 1, "data": PROBE}, format="json")

    assert response.status_code == status.HTTP_200_OK
    video.refresh_from_db()
    assert video.media_metadata == {"version": 1, "data": PROBE}
    assert editor_client.get(url(video)).json() == {"version": 1, "data": PROBE}


def test_recording_a_probe_does_not_mark_the_programme_edited(
    ingest_client: APIClient, video: Video
) -> None:
    before = Video.objects.get(pk=video.pk).updated_time

    ingest_client.put(url(video), {"version": 1, "data": PROBE}, format="json")

    assert Video.objects.get(pk=video.pk).updated_time == before


def test_a_new_probe_replaces_the_old_one(ingest_client: APIClient, video: Video) -> None:
    ingest_client.put(url(video), {"version": 1, "data": PROBE}, format="json")
    ingest_client.put(
        url(video), {"version": 1, "data": {"format": {"format_name": "dv"}}}, format="json"
    )

    video.refresh_from_db()
    assert video.media_metadata == {"version": 1, "data": {"format": {"format_name": "dv"}}}


@pytest.mark.parametrize(
    "body",
    [
        {"data": PROBE},
        {"version": 0, "data": PROBE},
        {"version": 1, "data": {}},
        {"version": 1, "data": []},
        {"version": 1},
    ],
)
def test_a_malformed_envelope_is_refused(ingest_client: APIClient, video: Video, body) -> None:
    response = ingest_client.put(url(video), body, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    video.refresh_from_db()
    assert video.media_metadata == {}


def test_the_organization_cannot_write_it(editor_client: APIClient, video: Video) -> None:
    response = editor_client.put(url(video), {"version": 1, "data": PROBE}, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_other_users_cannot_read_it(outsider_client: APIClient, video: Video) -> None:
    assert outsider_client.get(url(video)).status_code == status.HTTP_403_FORBIDDEN


def test_anonymous_cannot_read_it(video: Video) -> None:
    assert APIClient().get(url(video)).status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


def test_an_unknown_video_is_not_found(ingest_client: APIClient) -> None:
    assert ingest_client.get(reverse("api-video-media-metadata", args=[999999])).status_code == 404
