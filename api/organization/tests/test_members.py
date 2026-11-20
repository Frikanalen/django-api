import pytest
from django.core import mail
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from api.organization import members
from fk.models import Organization, User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def mail_inline(monkeypatch):
    monkeypatch.setattr(members, "run_in_background", lambda f, *a, **kw: f(*a, **kw))


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


def test_editor_adds_a_member_by_email(organization, editor_client, outsider):
    response = editor_client.post(
        members_url(organization), {"email": "OUTSIDER@example.test"}, format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["id"] == outsider.pk
    assert organization.members.filter(pk=outsider.pk).exists()
    assert [m.to for m in mail.outbox] == [[outsider.email]]
    assert organization.name in mail.outbox[0].subject
    assert f"/organization/{organization.pk}/admin" in mail.outbox[0].body


def test_unknown_email_asks_them_to_register_first(organization, editor_client):
    response = editor_client.post(
        members_url(organization), {"email": "nobody@example.test"}, format="json"
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "registrere seg" in response.json()["errors"][0]["detail"]
    assert mail.outbox == []


def test_adding_someone_already_in_is_refused(organization, editor_client, editor, member):
    for user in (member, editor):
        response = editor_client.post(
            members_url(organization), {"email": user.email}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_members_cannot_add_members(organization, member, outsider):
    response = client_for(member).post(
        members_url(organization), {"email": outsider.email}, format="json"
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not organization.members.filter(pk=outsider.pk).exists()


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


def test_staff_manage_members(organization, outsider):
    staff = User.objects.create(email="staff@example.test", is_superuser=True)
    response = client_for(staff).post(
        members_url(organization), {"email": outsider.email}, format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED
    response = client_for(staff).delete(member_url(organization, outsider))
    assert response.status_code == status.HTTP_204_NO_CONTENT
