"""Behavior of videofile creation through the API."""

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Organization, User, Video, VideoFile, VideoFileVariant

pytestmark = pytest.mark.django_db


def test_organization_members_can_register_a_file_for_their_video() -> None:
    member = User.objects.create(email="videofile-member@example.test")
    organization = Organization.objects.create(name="Videofile create org", editor=member)
    organization.members.add(member)
    video = Video.objects.create(
        name="Video getting a file",
        creator=member,
        organization=organization,
        proper_import=True,
    )
    client = APIClient()
    client.force_authenticate(user=member)

    response = client.post(
        reverse("api-videofile-list"),
        {"video": video.pk, "variant": "original", "filename": "new-file.mov"},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    created = VideoFile.objects.get(pk=response.json()["id"])
    assert created.video == video
    assert created.variant == VideoFileVariant.ORIGINAL
    assert created.filename == "new-file.mov"


def test_a_second_file_with_the_same_format_is_rejected() -> None:
    member = User.objects.create(email="videofile-dupe@example.test")
    organization = Organization.objects.create(name="Videofile dupe org", editor=member)
    organization.members.add(member)
    video = Video.objects.create(
        name="Video with a broadcast file",
        creator=member,
        organization=organization,
        proper_import=True,
    )
    VideoFile.objects.create(video=video, variant=VideoFileVariant.BROADCAST, filename="first.mov")
    client = APIClient()
    client.force_authenticate(user=member)

    response = client.post(
        reverse("api-videofile-list"),
        {"video": video.pk, "variant": "broadcast", "filename": "second.mov"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert VideoFile.objects.get().filename == "first.mov"
