import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Organization, Series, User, Video

pytestmark = pytest.mark.django_db


@pytest.fixture
def editor() -> User:
    return User.objects.create(
        email="series-editor@example.test",
        first_name="Ada",
        last_name="Lovelace",
    )


@pytest.fixture
def organization(editor: User) -> Organization:
    organization = Organization.objects.create(name="Series organization", editor=editor)
    organization.members.add(editor)
    return organization


@pytest.fixture
def editor_client(editor: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(editor)
    return client


def test_member_can_create_and_public_can_read_series(
    editor_client: APIClient,
    organization: Organization,
) -> None:
    response = editor_client.post(
        reverse("api-series-list"),
        {
            "name": "Havna vår",
            "synopsis": "Historier fra havna.",
            "organization": organization.pk,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["episodeCount"] == 0
    series = Series.objects.get()
    assert series.organization == organization
    Series.objects.filter(pk=series.pk).update(image_url="https://example.test/havna.jpg")

    payload = APIClient().get(reverse("api-series-detail", args=[series.pk])).json()
    assert payload["name"] == "Havna vår"
    assert payload["synopsis"] == "Historier fra havna."
    assert payload["imageUrl"] == "https://example.test/havna.jpg"
    assert payload["organization"]["id"] == organization.pk
    assert payload["episodeCount"] == 0


def test_series_list_can_be_filtered_by_organization(
    editor: User,
    organization: Organization,
) -> None:
    other = Organization.objects.create(name="Other organization", editor=editor)
    wanted = Series.objects.create(name="Wanted", organization=organization)
    Series.objects.create(name="Not wanted", organization=other)

    response = APIClient().get(
        reverse("api-series-list"),
        {"organization": organization.pk},
    )

    assert [item["id"] for item in response.json()["results"]] == [wanted.pk]


def test_outsider_cannot_create_series_for_another_organization(
    organization: Organization,
) -> None:
    outsider = User.objects.create(email="series-outsider@example.test")
    client = APIClient()
    client.force_authenticate(outsider)

    response = client.post(
        reverse("api-series-list"),
        {"name": "Borrowed", "organization": organization.pk},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not Series.objects.exists()


def test_artwork_url_cannot_be_set_through_series_writes(
    editor_client: APIClient,
    organization: Organization,
) -> None:
    series = Series.objects.create(name="Managed artwork", organization=organization)

    response = editor_client.patch(
        reverse("api-series-detail", args=[series.pk]),
        {
            "imageUrl": "https://example.test/member-supplied.jpg",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    series.refresh_from_db()
    assert series.image_url == ""


def test_video_can_be_assigned_to_its_organizations_series(
    editor_client: APIClient,
    editor: User,
    organization: Organization,
) -> None:
    series = Series.objects.create(name="Havna vår", organization=organization)
    video = Video.objects.create(name="Episode", creator=editor, organization=organization)

    response = editor_client.patch(
        reverse("api-video-detail", args=[video.pk]),
        {"seriesId": series.pk, "episodeNumber": 3},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    video.refresh_from_db()
    assert video.series == series
    assert video.episode_number == 3
    assert response.json()["series"]["id"] == series.pk
    assert response.json()["episodeNumber"] == 3


def test_video_rejects_a_series_owned_by_another_organization(
    editor_client: APIClient,
    editor: User,
    organization: Organization,
) -> None:
    other = Organization.objects.create(name="Other organization", editor=editor)
    series = Series.objects.create(name="Other series", organization=other)
    video = Video.objects.create(name="Episode", creator=editor, organization=organization)

    response = editor_client.patch(
        reverse("api-video-detail", args=[video.pk]),
        {"seriesId": series.pk, "episodeNumber": 1},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    video.refresh_from_db()
    assert video.series is None


def test_episode_number_must_be_unique_within_a_series(
    editor_client: APIClient,
    editor: User,
    organization: Organization,
) -> None:
    series = Series.objects.create(name="Havna vår", organization=organization)
    Video.objects.create(
        name="First episode",
        creator=editor,
        organization=organization,
        series=series,
        episode_number=1,
    )
    video = Video.objects.create(name="Second episode", creator=editor, organization=organization)

    response = editor_client.patch(
        reverse("api-video-detail", args=[video.pk]),
        {"seriesId": series.pk, "episodeNumber": 1},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not Video.objects.filter(pk=video.pk, series=series).exists()


def test_clearing_series_also_clears_episode_number(
    editor_client: APIClient,
    editor: User,
    organization: Organization,
) -> None:
    series = Series.objects.create(name="Havna vår", organization=organization)
    video = Video.objects.create(
        name="Episode",
        creator=editor,
        organization=organization,
        series=series,
        episode_number=4,
    )

    response = editor_client.patch(
        reverse("api-video-detail", args=[video.pk]),
        {"seriesId": None},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    video.refresh_from_db()
    assert video.series is None
    assert video.episode_number is None


def test_series_with_episodes_cannot_be_deleted(
    editor_client: APIClient,
    editor: User,
    organization: Organization,
) -> None:
    series = Series.objects.create(name="Havna vår", organization=organization)
    Video.objects.create(
        name="Episode",
        creator=editor,
        organization=organization,
        series=series,
    )

    response = editor_client.delete(reverse("api-series-detail", args=[series.pk]))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Series.objects.filter(pk=series.pk).exists()
