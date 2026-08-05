"""
Behavior of video creation through the API: field parsing, creator
inference, and how the organization is chosen when the payload leaves
it out (the untested half of VideoSerializer.validate).
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Category, Organization, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def category() -> Category:
    return Category.objects.create(id=1, name="News")


def create_video(client: APIClient, payload: dict):
    return client.post(reverse("api-video-list"), payload, format="json")


def test_create_parses_fields_and_infers_the_creator(
    editor_client: APIClient,
    editor: User,
    organization: Organization,
    category: Category,
) -> None:
    response = create_video(
        editor_client,
        {
            "name": "Created video",
            "duration": "01:2.3",
            "organization": organization.pk,
            "categories": ["News"],
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    video = Video.objects.get(pk=response.json()["id"])
    assert video.creator == editor
    assert video.organization == organization
    assert video.duration == timedelta(minutes=1, seconds=2.3)
    assert [c.pk for c in video.categories.all()] == [category.pk]

    payload = response.json()
    assert payload["name"] == "Created video"
    assert payload["duration"] == "00:01:02.300000"
    assert payload["durationSec"] == 62.3
    assert payload["categories"] == ["News"]
    assert payload["organization"] == organization.pk
    assert payload["creator"] == editor.email


def test_creator_attribution_cannot_be_spoofed_on_create(
    editor_client: APIClient,
    editor: User,
    organization: Organization,
) -> None:
    other = User.objects.create(email="somebody-else@example.test")

    response = create_video(
        editor_client,
        {"name": "Attributed video", "categories": [], "creator": other.email},
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert Video.objects.get(name="Attributed video").creator == editor


def test_creator_cannot_be_reassigned_after_creation(
    editor_client: APIClient,
    editor: User,
    organization: Organization,
) -> None:
    video = Video.objects.create(name="Owned video", creator=editor, organization=organization)
    other = User.objects.create(email="somebody-else@example.test")

    response = editor_client.patch(
        reverse("api-video-detail", args=[video.pk]),
        {"creator": other.email},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    video.refresh_from_db()
    assert video.creator == editor


def test_create_without_organization_uses_the_users_only_membership(
    editor_client: APIClient,
    organization: Organization,
) -> None:
    response = create_video(editor_client, {"name": "Inferred org", "categories": []})

    assert response.status_code == status.HTTP_201_CREATED
    assert Video.objects.get(name="Inferred org").organization == organization


def test_create_without_organization_fails_for_a_user_with_none(
    category: Category,
) -> None:
    loner = User.objects.create(email="no-org@example.test")
    client = APIClient()
    client.force_authenticate(user=loner)

    response = create_video(client, {"name": "Orphan video", "categories": []})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert [error["attr"] for error in response.json()["errors"]] == ["organization"]
    assert not Video.objects.exists()


def test_create_without_organization_fails_for_a_user_with_several(
    editor_client: APIClient,
    editor: User,
    organization: Organization,
) -> None:
    second = Organization.objects.create(name="Second organization", editor=editor)
    second.members.add(editor)

    response = create_video(editor_client, {"name": "Ambiguous video", "categories": []})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not Video.objects.exists()
