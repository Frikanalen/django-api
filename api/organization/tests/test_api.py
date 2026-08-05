import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Organization, User

pytestmark = pytest.mark.django_db


def result_ids(response) -> list[int]:
    return [item["id"] for item in response.data["results"]]


def test_anonymous_users_can_list_organizations(organization: Organization) -> None:
    response = APIClient().get(reverse("api-organization-list"))

    assert response.status_code == status.HTTP_200_OK
    assert result_ids(response) == [organization.pk]


def test_list_uses_model_ordering(editor: User) -> None:
    organizations = {
        name: Organization.objects.create(name=name, editor=editor)
        for name in ("Zulu", "Alpha", "Middle")
    }

    response = APIClient().get(reverse("api-organization-list"))

    assert response.status_code == status.HTTP_200_OK
    assert result_ids(response) == [
        organizations[name].pk for name in ("Alpha", "Middle", "Zulu")
    ]


def test_list_uses_default_pagination_limit(editor: User) -> None:
    for index in range(51):
        Organization.objects.create(name=f"Organization {index:02d}", editor=editor)

    response = APIClient().get(reverse("api-organization-list"))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 51
    assert len(response.data["results"]) == 50
    assert response.data["next"] is not None


def test_list_honors_limit_and_offset(editor: User) -> None:
    organizations = [
        Organization.objects.create(name=f"Organization {index}", editor=editor)
        for index in range(4)
    ]

    response = APIClient().get(
        reverse("api-organization-list"),
        {"limit": 2, "offset": 1},
    )

    assert response.status_code == status.HTTP_200_OK
    assert result_ids(response) == [organizations[1].pk, organizations[2].pk]
    assert response.data["count"] == 4


def test_authenticated_user_can_create_an_organization(
    editor_client: APIClient,
    editor: User,
) -> None:
    response = editor_client.post(
        reverse("api-organization-list"),
        {
            "name": "New organization",
            "homepage": "https://new.example.test",
            "description": "Created through the API",
            "postal_address": "Postboks 3",
            "street_address": "Testveien 4",
            "fkmember": True,
            "editor_id": 999999,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    created = Organization.objects.get(pk=response.data["id"])
    assert created.editor == editor
    assert created.name == "New organization"
    assert created.homepage == "https://new.example.test"
    assert created.description == "Created through the API"
    assert created.postal_address == "Postboks 3"
    assert created.street_address == "Testveien 4"
    assert created.fkmember is False
    assert response.data["editor_id"] == editor.pk


def test_anonymous_user_cannot_create_an_organization() -> None:
    response = APIClient().post(
        reverse("api-organization-list"),
        {"name": "Anonymous organization"},
        format="json",
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.data["errors"][0]["code"] == "not_authenticated"
    assert not Organization.objects.exists()


def test_create_requires_a_name(editor_client: APIClient) -> None:
    response = editor_client.post(reverse("api-organization-list"), {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["errors"] == [
        {"code": "required", "detail": "This field is required.", "attr": "name"}
    ]
    assert not Organization.objects.exists()


def test_anonymous_user_can_retrieve_an_organization(organization: Organization) -> None:
    response = APIClient().get(
        reverse("api-organization-detail", args=[organization.pk])
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["id"] == organization.pk
    assert response.data["name"] == organization.name


def test_retrieve_unknown_organization_returns_not_found() -> None:
    response = APIClient().get(reverse("api-organization-detail", args=[999999]))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.data["errors"][0]["code"] == "not_found"


def test_editor_can_partially_update_their_organization(
    editor_client: APIClient,
    organization: Organization,
) -> None:
    response = editor_client.patch(
        reverse("api-organization-detail", args=[organization.pk]),
        {"name": "Renamed organization", "fkmember": False, "editor_id": None},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    organization.refresh_from_db()
    assert organization.name == "Renamed organization"
    assert organization.fkmember is True
    assert organization.editor_id == response.data["editor_id"]


def test_editor_can_replace_their_organization(
    editor_client: APIClient,
    organization: Organization,
    editor: User,
) -> None:
    response = editor_client.put(
        reverse("api-organization-detail", args=[organization.pk]),
        {"name": "Replacement organization"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    organization.refresh_from_db()
    assert organization.name == "Replacement organization"
    assert organization.description == "An organization used by the API tests."
    assert organization.editor == editor


def test_editor_can_delete_their_organization(
    editor_client: APIClient,
    organization: Organization,
) -> None:
    response = editor_client.delete(
        reverse("api-organization-detail", args=[organization.pk])
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not Organization.objects.filter(pk=organization.pk).exists()
