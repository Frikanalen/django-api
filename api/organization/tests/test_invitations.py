import re
from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.organization import invitations
from fk.models import Organization, OrganizationInvitation, User

pytestmark = pytest.mark.django_db

STRONG = "kaia-om-kvelden-1987"


@pytest.fixture(autouse=True)
def mail_inline(monkeypatch):
    monkeypatch.setattr(invitations, "run_in_background", lambda f, *a, **kw: f(*a, **kw))


def client_for(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


@pytest.fixture
def member(organization: Organization) -> User:
    user = User.objects.create(email="member@example.test", first_name="Bo", last_name="Berg")
    organization.members.add(user)
    return user


@pytest.fixture
def existing() -> User:
    return User.objects.create(email="existing@example.test", first_name="Cleo", last_name="Ås")


def invitations_url(organization: Organization) -> str:
    return reverse("api-organization-invitations", args=[organization.pk])


def invite(client: APIClient, organization: Organization, email: str):
    return client.post(invitations_url(organization), {"email": email}, format="json")


def token_from_last_mail() -> str:
    match = re.search(r"/invitation/([\w-]+)", mail.outbox[-1].body)
    assert match
    return match.group(1)


def accept(client: APIClient, token: str, data: dict | None = None):
    return client.post(reverse("api-invitation-accept", args=[token]), data or {}, format="json")


def test_the_editor_cannot_tell_whether_an_address_has_an_account(
    organization, editor_client, existing
):
    known = invite(editor_client, organization, existing.email)
    unknown = invite(editor_client, organization, "nobody@example.test")

    assert known.status_code == unknown.status_code == status.HTTP_201_CREATED
    assert set(known.json()) == set(unknown.json()) == {"id", "email", "createdAt", "expiresAt"}
    # Nobody joins until they accept, and the list shows addresses only.
    assert not organization.members.filter(pk=existing.pk).exists()
    assert [m.to for m in mail.outbox] == [[existing.email], ["nobody@example.test"]]
    listed = editor_client.get(invitations_url(organization)).json()
    assert sorted(i["email"] for i in listed) == sorted([existing.email, "nobody@example.test"])


def test_inviting_again_replaces_the_old_link(organization, editor_client):
    invite(editor_client, organization, "nobody@example.test")
    first = token_from_last_mail()
    invite(editor_client, organization, "NOBODY@example.test")
    second = token_from_last_mail()

    assert OrganizationInvitation.objects.count() == 1
    assert accept(client_for(), first).status_code == status.HTTP_404_NOT_FOUND
    assert client_for().get(reverse("api-invitation", args=[second])).status_code == 200


def test_inviting_someone_already_in_is_refused(organization, editor_client, editor, member):
    for user in (member, editor):
        assert invite(editor_client, organization, user.email).status_code == 400


def test_members_cannot_invite(organization, member):
    response = invite(client_for(member), organization, "nobody@example.test")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert client_for(member).get(invitations_url(organization)).status_code == 403


def test_the_link_shows_what_it_is_for(organization, editor_client, editor):
    invite(editor_client, organization, "nobody@example.test")
    response = client_for().get(reverse("api-invitation", args=[token_from_last_mail()]))

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["organizationId"] == organization.pk
    assert response.json()["organizationName"] == organization.name
    assert response.json()["invitedBy"] == "Ada Lovelace"
    assert response.json()["email"] == "nobody@example.test"


def test_a_logged_in_user_accepts(organization, editor_client, existing):
    invite(editor_client, organization, existing.email)
    token = token_from_last_mail()

    response = accept(client_for(existing), token)
    assert response.status_code == status.HTTP_200_OK
    assert organization.members.filter(pk=existing.pk).exists()
    # Used once.
    assert accept(client_for(existing), token).status_code == status.HTTP_404_NOT_FOUND
    assert editor_client.get(invitations_url(organization)).json() == []


def test_someone_without_an_account_creates_one_from_the_link(organization, editor_client):
    invite(editor_client, organization, "nobody@example.test")
    client = client_for()

    response = accept(
        client,
        token_from_last_mail(),
        {"firstName": "Dag", "lastName": "Dahl", "password": STRONG},
    )
    assert response.status_code == status.HTTP_200_OK
    user = User.objects.get(email="nobody@example.test")
    assert user.check_password(STRONG)
    assert organization.members.filter(pk=user.pk).exists()
    # Logged in by the same request.
    assert client.get(reverse("api-user-detail")).json()["email"] == "nobody@example.test"


def test_an_address_with_an_account_must_log_in_first(organization, editor_client, existing):
    invite(editor_client, organization, existing.email)
    response = accept(
        client_for(),
        token_from_last_mail(),
        {"firstName": "Not", "lastName": "Cleo", "password": STRONG},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not organization.members.filter(pk=existing.pk).exists()


def test_a_new_account_needs_a_name_and_a_strong_password(organization, editor_client):
    invite(editor_client, organization, "nobody@example.test")
    token = token_from_last_mail()
    assert accept(client_for(), token, {"password": STRONG}).status_code == 400
    assert (
        accept(
            client_for(), token, {"firstName": "Dag", "lastName": "Dahl", "password": "1234"}
        ).status_code
        == 400
    )
    assert not User.objects.filter(email="nobody@example.test").exists()


def test_expired_and_withdrawn_invitations_do_nothing(organization, editor_client, existing):
    invite(editor_client, organization, existing.email)
    token = token_from_last_mail()
    invitation = OrganizationInvitation.objects.get()
    invitation.expires_at = timezone.now() - timedelta(minutes=1)
    invitation.save()
    assert accept(client_for(existing), token).status_code == status.HTTP_404_NOT_FOUND

    invite(editor_client, organization, existing.email)
    token = token_from_last_mail()
    invitation = OrganizationInvitation.objects.open().get()
    url = reverse("api-organization-invitation", args=[organization.pk, invitation.pk])
    assert editor_client.delete(url).status_code == status.HTTP_204_NO_CONTENT
    assert accept(client_for(existing), token).status_code == status.HTTP_404_NOT_FOUND
    assert not organization.members.filter(pk=existing.pk).exists()


def test_only_the_token_is_stored_as_a_hash(organization, editor_client):
    invite(editor_client, organization, "nobody@example.test")
    token = token_from_last_mail()
    assert OrganizationInvitation.objects.get().token_hash != token
