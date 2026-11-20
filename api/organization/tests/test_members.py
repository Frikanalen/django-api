import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import Organization, User

pytestmark = pytest.mark.django_db


def client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def member(organization: Organization) -> User:
    user = User.objects.create(email="member@example.test", first_name="Bo", last_name="Berg")
    organization.members.add(user)
    return user


@pytest.fixture
def outsider() -> User:
    return User.objects.create(email="outsider@example.test", first_name="Cleo", last_name="Ås")


def members_url(organization: Organization) -> str:
    return reverse("api-organization-members", args=[organization.pk])


def member_url(organization: Organization, user: User) -> str:
    return reverse("api-organization-member", args=[organization.pk, user.pk])


def test_members_see_the_editor_first(organization, editor, member):
    response = client_for(member).get(members_url(organization))
    assert response.status_code == status.HTTP_200_OK
    assert [(p["email"], p["isEditor"]) for p in response.json()] == [
        (editor.email, True),
        (member.email, False),
    ]


def test_outsiders_do_not_see_the_list(organization, outsider):
    response = client_for(outsider).get(members_url(organization))
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_editor_removes_a_member(organization, editor_client, member):
    response = editor_client.delete(member_url(organization, member))
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not organization.members.filter(pk=member.pk).exists()


def test_member_leaves(organization, member):
    response = client_for(member).delete(member_url(organization, member))
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not organization.members.filter(pk=member.pk).exists()


def test_member_cannot_remove_another_member(organization, member):
    other = User.objects.create(email="other@example.test")
    organization.members.add(other)
    response = client_for(member).delete(member_url(organization, other))
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert organization.members.filter(pk=other.pk).exists()


def test_editor_cannot_be_removed(organization, editor, editor_client):
    response = editor_client.delete(member_url(organization, editor))
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_staff_remove_members(organization, member):
    staff = User.objects.create(email="staff@example.test", is_superuser=True)
    response = client_for(staff).delete(member_url(organization, member))
    assert response.status_code == status.HTTP_204_NO_CONTENT
