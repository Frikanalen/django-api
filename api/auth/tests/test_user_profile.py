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
from rest_framework.test import APIClient

from fk.models import User

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


def test_password_updates_are_broken(client: APIClient, account: User) -> None:
    """
    Known defect, pinned on purpose: UserSerializer declares password as
    a writable field but never runs it through set_password, so a PATCH
    stores the raw string in the password column. The account can then
    log in with neither the old nor the new password, and the plaintext
    sits in the database. The fix (hash it, or reject the field) should
    replace this test.
    """
    response = client.patch(reverse("api-user-detail"), {"password": "hunter2"}, format="json")

    assert response.status_code == status.HTTP_200_OK
    account.refresh_from_db()
    assert account.password == "hunter2"
    assert not account.check_password("hunter2")
    assert not account.check_password(PASSWORD)


def test_account_can_be_deleted(client: APIClient, account: User) -> None:
    response = client.delete(reverse("api-user-detail"))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not User.objects.filter(pk=account.pk).exists()
