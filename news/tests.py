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
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import User
from news.models import Bulletin
from scheduling import posters

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


def test_bulletins_are_listed_newest_first() -> None:
    older = make_bulletin("Older", CREATED)
    newer = make_bulletin("Newer", CREATED + timedelta(days=1))

    response = APIClient().get(reverse("news:bulletin-list"))

    assert response.status_code == status.HTTP_200_OK
    # Paginated like every other collection, and the timestamp is called
    # `createdTime` here as it is everywhere else.
    results = response.json()["results"]
    assert [
        {key: item[key] for key in ("id", "heading", "text", "createdTime", "isPublished")}
        for item in results
    ] == [
        {
            "id": newer.pk,
            "heading": "Newer",
            "text": "Text of Newer",
            "createdTime": "2015-01-02T10:00:00Z",
            "isPublished": True,
        },
        {
            "id": older.pk,
            "heading": "Older",
            "text": "Text of Older",
            "createdTime": "2015-01-01T10:00:00Z",
            "isPublished": True,
        },
    ]
    # What the screens need to show it: the pages and how long they take.
    assert results[0]["template"] == "standard"
    assert results[0]["pages"] == ["Text of Newer"]
    assert results[0]["pageSeconds"] == [4.6]
    assert results[0]["readingSeconds"] == 5
    assert results[0]["isCurrent"] is True


def test_the_bulletin_list_has_no_trailing_slash() -> None:
    """The whole API is trailing-slash-free; news used to be the exception."""
    assert reverse("news:bulletin-list") == "/api/news/bulletins"


def test_drafts_are_hidden_from_the_public_but_visible_to_staff() -> None:
    """
    Replaces the pinned draft leak: unpublished bulletins are only
    served to staff, who see the is_published flag to tell them apart.
    """
    make_bulletin("Published")
    draft = make_bulletin("Draft", CREATED + timedelta(days=1), is_published=False)

    public_list = APIClient().get(reverse("news:bulletin-list"))
    assert [item["heading"] for item in public_list.json()["results"]] == ["Published"]

    draft_url = reverse("news:bulletin-detail", args=[draft.pk])
    assert APIClient().get(draft_url).status_code == status.HTTP_404_NOT_FOUND

    staff = staff_client()
    staff_list = staff.get(reverse("news:bulletin-list"))
    assert [(item["heading"], item["isPublished"]) for item in staff_list.json()["results"]] == [
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


def test_a_higher_priority_comes_first() -> None:
    make_bulletin("Newest", CREATED + timedelta(days=2))
    make_bulletin("Important", CREATED, priority=5)

    response = APIClient().get(reverse("news:bulletin-list"))

    assert [item["heading"] for item in response.json()["results"]] == ["Important", "Newest"]


def test_a_placement_lists_only_what_is_showing_there_now() -> None:
    now = timezone.now()
    make_bulletin("Everywhere")
    make_bulletin("Air only", on_infographics=False)
    make_bulletin("Infographics only", on_air=False)
    make_bulletin("Not yet", starts_at=now + timedelta(hours=1))
    make_bulletin("Run out", ends_at=now - timedelta(minutes=1))
    make_bulletin("Running", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1))
    make_bulletin("Draft", is_published=False)

    def showing(placement: str, client: APIClient) -> set[str]:
        response = client.get(reverse("news:bulletin-list"), {"placement": placement})
        assert response.status_code == status.HTTP_200_OK
        return {item["heading"] for item in response.json()["results"]}

    assert showing("air", APIClient()) == {"Everywhere", "Air only", "Running"}
    assert showing("infographics", APIClient()) == {"Everywhere", "Infographics only", "Running"}
    # Staff get the same answer there: it is what the screen shows.
    assert showing("air", staff_client()) == {"Everywhere", "Air only", "Running"}

    listed = APIClient().get(reverse("news:bulletin-list")).json()["results"]
    current = {item["heading"]: item["isCurrent"] for item in listed}
    assert current["Run out"] is False and current["Not yet"] is False
    assert current["Running"] is True


def test_an_unknown_placement_is_refused() -> None:
    response = APIClient().get(reverse("news:bulletin-list"), {"placement": "website"})
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_the_list_is_never_served_from_a_cache() -> None:
    response = APIClient().get(reverse("news:bulletin-list"))
    assert "no-cache" in response["Cache-Control"]


def test_a_long_text_is_paged_in_the_answer() -> None:
    sentence = "Dette er en setning som er akkurat passe lang til å fylle litt av en side. "
    response = staff_client().post(
        reverse("news:bulletin-list"),
        {"heading": "Lang melding", "text": sentence * 8},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert len(body["pages"]) == 3
    assert all(len(page) <= posters.PAGE_LENGTH for page in body["pages"])
    assert " ".join(body["pages"]) == (sentence * 8).strip()
    assert len(body["pageSeconds"]) == 3


def test_a_text_with_too_many_pages_is_refused() -> None:
    client = staff_client()
    response = client.post(
        reverse("news:bulletin-list"),
        {"heading": "For lang", "text": "\n\n".join(["En side."] * 7)},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not Bulletin.objects.exists()

    bulletin = make_bulletin("Passe")
    response = client.patch(
        reverse("news:bulletin-detail", args=[bulletin.pk]),
        {"text": "\n\n".join(["En side."] * 7)},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_it_has_to_end_after_it_starts() -> None:
    now = timezone.now()
    bulletin = make_bulletin("Datoer", starts_at=now)
    response = staff_client().patch(
        reverse("news:bulletin-detail", args=[bulletin.pk]),
        {"ends_at": (now - timedelta(hours=1)).isoformat()},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
