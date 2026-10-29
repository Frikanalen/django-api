import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Organization, User

pytestmark = pytest.mark.django_db


def authenticated_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.parametrize("method", ["patch", "put", "delete"])
def test_anonymous_users_cannot_modify_organizations(
    organization: Organization,
    method: str,
) -> None:
    response = getattr(APIClient(), method)(
        reverse("api-organization-detail", args=[organization.pk]),
        {"name": "Unauthorized change"},
        format="json",
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    organization.refresh_from_db()
    assert organization.name == "Organization test group"


@pytest.mark.parametrize("method", ["patch", "put", "delete"])
def test_other_authenticated_users_cannot_modify_organizations(
    organization: Organization,
    method: str,
) -> None:
    other_user = User.objects.create(email="other-user@example.test")
    response = getattr(authenticated_client(other_user), method)(
        reverse("api-organization-detail", args=[organization.pk]),
        {"name": "Unauthorized change"},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    organization.refresh_from_db()
    assert organization.name == "Organization test group"


def test_organization_members_cannot_modify_the_organization(
    organization: Organization,
) -> None:
    member = User.objects.create(email="organization-member@example.test")
    organization.members.add(member)

    response = authenticated_client(member).patch(
        reverse("api-organization-detail", args=[organization.pk]),
        {"name": "Member change"},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    organization.refresh_from_db()
    assert organization.name == "Organization test group"


def test_staff_can_modify_another_editors_organization(organization: Organization) -> None:
    staff = User.objects.create(email="staff@example.test", is_superuser=True)

    response = authenticated_client(staff).patch(
        reverse("api-organization-detail", args=[organization.pk]),
        {"name": "Staff change"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    organization.refresh_from_db()
    assert organization.name == "Staff change"
