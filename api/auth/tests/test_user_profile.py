"""
The profile endpoint: reading, updating and deleting your own account.

The old tests called the view through APIRequestFactory and asserted on
the very same in-memory object the view had mutated, which would have
kept passing even if nothing were saved. Everything here goes through
the full stack and asserts on reloaded database state.
"""

from datetime import UTC, date, datetime

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from fk.models import Organization, User, Video

pytestmark = pytest.mark.django_db

EMAIL = "profile@example.test"
PASSWORD = "correct horse battery staple"
JOINED = datetime(2015, 1, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def account() -> User:
    user = User.objects.create_user(email=EMAIL, password=PASSWORD, date_of_birth=date(1990, 6, 7))
    user.first_name = "Kari"
    user.last_name = "Nordmann"
    user.phone_number = "+47 22 22 55 55"
    user.date_joined = JOINED
    user.save()
    return user


@pytest.fixture
def client(account: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=account)
    return client


def test_profile_returns_own_data(client: APIClient, account: User) -> None:
    response = client.get(reverse("api-user-detail"))

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "id": account.pk,
        "email": EMAIL,
        "isStaff": False,
        "identityConfirmed": False,
        "dateJoined": "2015-01-01T10:00:00Z",
        "editorOf": [],
        "memberOf": [],
        "firstName": "Kari",
        "lastName": "Nordmann",
        "dateOfBirth": "1990-06-07",
        # Phone numbers come back in E.164, not as entered.
        "phoneNumber": "+4722225555",
    }


def test_profile_updates_are_persisted(client: APIClient, account: User) -> None:
    response = client.patch(
        reverse("api-user-detail"),
        {
            "first_name": "Ola",
            "last_name": "Nordmann",
            "date_of_birth": "2000-12-15",
            "phone_number": "+47 22 22 55 66",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    account.refresh_from_db()
    assert account.first_name == "Ola"
    assert account.last_name == "Nordmann"
    assert account.date_of_birth == date(2000, 12, 15)
    assert account.phone_number == "+4722225566"


def test_email_cannot_be_changed(client: APIClient, account: User) -> None:
    # DRF silently ignores writes to read-only fields, so this is a 200
    # that must change nothing - the update succeeds sans email.
    response = client.patch(
        reverse("api-user-detail"), {"email": "changed@example.test"}, format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    account.refresh_from_db()
    assert account.email == EMAIL


def test_identity_confirmation_is_visible_but_cannot_be_changed(
    client: APIClient, account: User
) -> None:
    account.identity_confirmed = True
    account.save(update_fields=["identity_confirmed"])

    response = client.patch(reverse("api-user-detail"), {"identityConfirmed": False}, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["identityConfirmed"] is True
    account.refresh_from_db()
    assert account.identity_confirmed is True


def test_password_updates_are_hashed_and_usable(client: APIClient, account: User) -> None:
    """
    Replaces the pinned plaintext-password defect: a PATCHed password
    must be hashed, never stored verbatim, and must work for a fresh
    login while the old password stops working.
    """
    response = client.patch(reverse("api-user-detail"), {"password": "hunter2"}, format="json")

    assert response.status_code == status.HTTP_200_OK
    account.refresh_from_db()
    assert account.password != "hunter2"
    assert account.check_password("hunter2")
    assert not account.check_password(PASSWORD)

    login = APIClient().post(
        reverse("api-user-login"), {"email": EMAIL, "password": "hunter2"}, format="json"
    )
    assert login.status_code == status.HTTP_200_OK

    stale_login = APIClient().post(
        reverse("api-user-login"), {"email": EMAIL, "password": PASSWORD}, format="json"
    )
    assert stale_login.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.fixture
def organization(account: User) -> Organization:
    organization = Organization.objects.create(name="Departing member's org", editor=account)
    organization.members.add(account)
    return organization


def test_deleting_an_account_scrubs_it_but_keeps_its_content(
    client: APIClient, account: User, organization: Organization
) -> None:
    """
    Video.creator is PROTECT, so removing the row would 500 for any user
    who has uploaded - and the broadcast record has to survive a
    departure regardless. Deletion anonymizes instead, which the caller
    cannot tell apart from a real delete.
    """
    video = Video.objects.create(
        name="Video that outlives its uploader",
        creator=account,
        organization=organization,
        proper_import=True,
    )

    response = client.delete(reverse("api-user-detail"))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    video.refresh_from_db()
    assert video.creator_id == account.pk

    account.refresh_from_db()
    assert account.email == f"deleted-{account.pk}@invalid"
    assert account.first_name == ""
    assert account.last_name == ""
    assert account.phone_number == ""
    assert account.date_of_birth is None
    assert not account.identity_confirmed
    assert not account.is_superuser
    assert not account.is_active
    assert not account.has_usable_password()


def test_deleting_an_account_releases_its_organization_roles(
    client: APIClient, account: User, organization: Organization
) -> None:
    client.delete(reverse("api-user-detail"))

    organization.refresh_from_db()
    assert organization.editor_id is None
    assert not organization.members.filter(pk=account.pk).exists()


def test_a_deleted_account_can_no_longer_authenticate(client: APIClient, account: User) -> None:
    # Every user is issued an API token on creation (fkweb.signals), so
    # that credential has to be revoked along with the password.
    token = Token.objects.get(user=account)

    client.delete(reverse("api-user-detail"))

    login = APIClient().post(
        reverse("api-user-login"), {"email": EMAIL, "password": PASSWORD}, format="json"
    )
    assert login.status_code == status.HTTP_401_UNAUTHORIZED
    assert not Token.objects.filter(key=token.key).exists()
