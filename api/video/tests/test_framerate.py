"""
Writing a video's frame rate.

The column has existed since the beginning and has never been populated,
because the serializer declared it read-only. Ingest is the one thing
that knows the answer -- it works the exact rate out to align DASH
segments to whole frames -- so it needed somewhere to put it.
"""

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Organization, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    return Video.objects.create(
        name="Framerate video",
        creator=editor,
        organization=organization,
        proper_import=True,
    )


def patch(client: APIClient, video: Video, **fields):
    return client.patch(reverse("api-video-detail", args=[video.pk]), fields, format="json")


def test_the_default_is_the_broadcast_rate(video: Video) -> None:
    assert video.framerate == 25000


@pytest.mark.parametrize(
    ("thousandths", "described"),
    [
        (25000, "25 fps, the PAL broadcast rate"),
        (30000, "30 fps"),
        (59940, "59.94 fps, which is why the units are thousandths at all"),
        (24000, "24 fps"),
    ],
)
def test_framerate_is_writable_in_thousandths_of_a_frame_per_second(
    editor_client: APIClient, video: Video, thousandths: int, described: str
) -> None:
    """The units are the model field's own, unchanged: 25 fps is 25000.
    agenda.tvanytime.document._frame_rate is the one existing reader and
    divides by 1000, so this is the convention it already assumes."""
    response = patch(editor_client, video, framerate=thousandths)

    assert response.status_code == status.HTTP_200_OK, described
    assert response.json()["framerate"] == thousandths
    video.refresh_from_db()
    assert video.framerate == thousandths


def test_a_video_the_caller_does_not_own_keeps_its_framerate(video: Video) -> None:
    outsider = User.objects.create(email="framerate-outsider@example.test")
    client = APIClient()
    client.force_authenticate(user=outsider)

    assert patch(client, video, framerate=30000).status_code == status.HTTP_403_FORBIDDEN
    video.refresh_from_db()
    assert video.framerate == 25000
