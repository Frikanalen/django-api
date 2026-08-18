"""
Who may read a video's upload token.

The token authorizes uploads through fkupload, so this endpoint is the
one place where organization membership gates *reading*: members and
staff see the token, everyone else must not.
"""

import pytest
from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Organization, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    return Video.objects.create(
        name="Upload token video",
        creator=editor,
        organization=organization,
        proper_import=True,
    )


def fetch_token(client: APIClient, video: Video):
    return client.get(reverse("api-video-upload-token-detail", args=[video.pk]))


def verify_token(client: APIClient, video: Video, upload_token: str):
    return client.post(
        reverse("api-video-upload-token-verification", args=[video.pk]),
        {"uploadToken": upload_token},
        format="json",
    )


def test_anonymous_users_are_asked_to_authenticate(video: Video) -> None:
    response = fetch_token(APIClient(), video)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_organization_members_can_read_the_token(editor_client: APIClient, video: Video) -> None:
    response = fetch_token(editor_client, video)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "uploadToken": video.upload_token,
        "uploadUrl": settings.FK_UPLOAD_URL,
    }


def test_outsiders_are_denied(video: Video) -> None:
    outsider = User.objects.create(email="outsider@example.test")
    client = APIClient()
    client.force_authenticate(user=outsider)

    response = fetch_token(client, video)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert video.upload_token not in response.content.decode()


def test_staff_can_read_any_token(video: Video) -> None:
    # User.is_staff is a read-only property aliasing is_superuser.
    staff = User.objects.create(email="upload-staff@example.test", is_superuser=True)
    client = APIClient()
    client.force_authenticate(user=staff)

    response = fetch_token(client, video)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["uploadToken"] == video.upload_token


def test_authenticated_client_can_verify_the_correct_token(
    editor_client: APIClient, video: Video
) -> None:
    response = verify_token(editor_client, video, video.upload_token)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not response.content


def test_anonymous_client_cannot_verify_a_token(video: Video) -> None:
    response = verify_token(APIClient(), video, video.upload_token)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_incorrect_token_is_indistinguishable_from_a_missing_video(
    editor_client: APIClient, video: Video
) -> None:
    invalid_token = verify_token(editor_client, video, "x" * 32)
    missing_video = editor_client.post(
        reverse("api-video-upload-token-verification", args=[video.pk + 1]),
        {"uploadToken": video.upload_token},
        format="json",
    )

    assert invalid_token.status_code == status.HTTP_404_NOT_FOUND
    assert invalid_token.json() == missing_video.json()
