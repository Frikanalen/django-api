"""
Bulletin API tests.

The previous version of this file generated bulletins with a home-grown
random-prose engine (unseeded) whose output no test ever inspected.
These tests use fixed data and assert the actual contract: payload
shape, ordering, and who gets to write.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import User
from news.models import Bulletin

pytestmark = pytest.mark.django_db

CREATED = datetime(2015, 1, 1, 10, 0, tzinfo=UTC)


def make_bulletin(heading: str, created: datetime = CREATED, **fields) -> Bulletin:
    fields.setdefault("is_published", True)
    bulletin = Bulletin.objects.create(heading=heading, text=f"Text of {heading}", **fields)
    # created is auto_now_add; pin it for deterministic ordering.
    Bulletin.objects.filter(pk=bulletin.pk).update(created=created)
    return bulletin


def staff_client() -> APIClient:
    client = APIClient()
    client.force_authenticate(
        user=User.objects.create(email="news-staff@example.test", is_superuser=True)
    )
    return client


def test_bulletins_are_listed_newest_first_without_pagination() -> None:
    older = make_bulletin("Older", CREATED)
    newer = make_bulletin("Newer", CREATED + timedelta(days=1))

    response = APIClient().get(reverse("news:bulletin-list"))

    assert response.status_code == status.HTTP_200_OK
    # A plain list: this endpoint has pagination disabled.
    assert response.json() == [
        {
            "id": newer.pk,
            "heading": "Newer",
            "text": "Text of Newer",
            "created": "2015-01-02T10:00:00Z",
            "isPublished": True,
        },
        {
            "id": older.pk,
            "heading": "Older",
            "text": "Text of Older",
            "created": "2015-01-01T10:00:00Z",
            "isPublished": True,
        },
    ]


def test_drafts_are_hidden_from_the_public_but_visible_to_staff() -> None:
    """
    Replaces the pinned draft leak: unpublished bulletins are only
    served to staff, who see the is_published flag to tell them apart.
    """
    make_bulletin("Published")
    draft = make_bulletin("Draft", CREATED + timedelta(days=1), is_published=False)

    public_list = APIClient().get(reverse("news:bulletin-list"))
    assert [item["heading"] for item in public_list.json()] == ["Published"]

    draft_url = reverse("news:bulletin-detail", args=[draft.pk])
    assert APIClient().get(draft_url).status_code == status.HTTP_404_NOT_FOUND

    staff = staff_client()
    staff_list = staff.get(reverse("news:bulletin-list"))
    assert [(item["heading"], item["isPublished"]) for item in staff_list.json()] == [
        ("Draft", False),
        ("Published", True),
    ]
    assert staff.get(draft_url).status_code == status.HTTP_200_OK


def test_staff_can_create_bulletins() -> None:
    response = staff_client().post(
        reverse("news:bulletin-list"),
        {"heading": "Fresh news", "text": "Something happened", "is_published": True},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    bulletin = Bulletin.objects.get()
    assert bulletin.heading == "Fresh news"
    assert bulletin.text == "Something happened"
    assert bulletin.is_published


def test_bulletins_are_created_as_drafts_unless_published_explicitly() -> None:
    response = staff_client().post(
        reverse("news:bulletin-list"),
        {"heading": "Quiet news", "text": "Not yet announced"},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert not Bulletin.objects.get().is_published


def test_non_staff_users_cannot_write() -> None:
    client = APIClient()
    client.force_authenticate(user=User.objects.create(email="news-reader@example.test"))

    response = client.post(
        reverse("news:bulletin-list"), {"heading": "h", "text": "t"}, format="json"
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not Bulletin.objects.exists()


def test_anonymous_users_cannot_write() -> None:
    response = APIClient().post(
        reverse("news:bulletin-list"), {"heading": "h", "text": "t"}, format="json"
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert not Bulletin.objects.exists()


def test_staff_can_update_and_delete() -> None:
    bulletin = make_bulletin("Original")
    client = staff_client()
    url = reverse("news:bulletin-detail", args=[bulletin.pk])

    patch_response = client.patch(url, {"heading": "Corrected"}, format="json")
    assert patch_response.status_code == status.HTTP_200_OK
    bulletin.refresh_from_db()
    assert bulletin.heading == "Corrected"

    delete_response = client.delete(url)
    assert delete_response.status_code == status.HTTP_204_NO_CONTENT
    assert not Bulletin.objects.exists()
