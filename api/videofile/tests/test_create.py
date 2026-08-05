"""Behavior of videofile creation through the API."""

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import FileFormat, Organization, User, Video, VideoFile

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
    file_format = FileFormat.objects.create(fsname="original")
    client = APIClient()
    client.force_authenticate(user=member)

    response = client.post(
        reverse("api-videofile-list"),
        {"video": video.pk, "format": file_format.pk, "filename": "new-file.mov"},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    created = VideoFile.objects.get(pk=response.json()["id"])
    assert created.video == video
    assert created.format == file_format
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
    file_format = FileFormat.objects.create(fsname="broadcast")
    VideoFile.objects.create(video=video, format=file_format, filename="first.mov")
    client = APIClient()
    client.force_authenticate(user=member)

    response = client.post(
        reverse("api-videofile-list"),
        {"video": video.pk, "format": file_format.pk, "filename": "second.mov"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert VideoFile.objects.get().filename == "first.mov"
