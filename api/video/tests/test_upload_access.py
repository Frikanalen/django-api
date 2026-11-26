"""
Who may upload a video's file.

Ingest asks GET /api/videos/<id>/upload with the uploader's own
credentials before accepting an upload, so the answers here are the
upload authorization itself.
"""

import pytest
from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from fk.models import Organization, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    return Video.objects.create(name="Upload video", creator=editor, organization=organization)


def fetch(client: APIClient, video: Video):
    return client.get(reverse("api-video-upload", args=[video.pk]))


def test_anonymous_users_are_asked_to_authenticate(video: Video) -> None:
    assert fetch(APIClient(), video).status_code == status.HTTP_401_UNAUTHORIZED


def test_organization_editor_gets_the_upload_url(editor_client: APIClient, video: Video) -> None:
    response = fetch(editor_client, video)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"uploadUrl": settings.FK_UPLOAD_URL}


def test_organization_members_may_upload(video: Video, organization: Organization) -> None:
    member = User.objects.create(email="upload-member@example.test")
    organization.members.add(member)
    client = APIClient()
    client.force_authenticate(user=member)

    assert fetch(client, video).status_code == status.HTTP_200_OK


def test_outsiders_are_denied(video: Video) -> None:
    client = APIClient()
    client.force_authenticate(user=User.objects.create(email="upload-outsider@example.test"))

    assert fetch(client, video).status_code == status.HTTP_403_FORBIDDEN


def test_staff_may_upload_to_any_video(video: Video) -> None:
    client = APIClient()
    client.force_authenticate(
        user=User.objects.create(email="upload-staff@example.test", is_superuser=True)
    )

    assert fetch(client, video).status_code == status.HTTP_200_OK


def test_a_removed_video_takes_no_uploads(editor_client: APIClient, video: Video) -> None:
    Video.objects.filter(pk=video.pk).update(is_banned=True)

    assert fetch(editor_client, video).status_code == status.HTTP_404_NOT_FOUND


def test_an_api_token_in_the_authorization_header_is_accepted(editor: User, video: Video) -> None:
    # What ingest forwards for scripted uploads (fk-cli).
    token, _ = Token.objects.get_or_create(user=editor)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    assert fetch(client, video).status_code == status.HTTP_200_OK


def test_a_session_cookie_is_accepted(editor: User, video: Video) -> None:
    # What ingest forwards for browser uploads. A GET, so no CSRF token.
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(editor)

    assert fetch(client, video).status_code == status.HTTP_200_OK


def test_a_suspended_organizations_video_takes_no_uploads(
    editor_client: APIClient, video: Video, organization: Organization
) -> None:
    Organization.objects.filter(pk=organization.pk).update(is_rogue=True)

    assert fetch(editor_client, video).status_code == status.HTTP_404_NOT_FOUND
