import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import User

pytestmark = pytest.mark.django_db

VALID_USER = {
    "email": "new-user@example.test",
    "password": "correct horse battery staple",
    "first_name": "John",
    "last_name": "Smith",
}


def register(client: APIClient, payload: dict):
    return client.post(reverse("api-user-create"), payload, format="json")


def test_registration_creates_a_working_logged_in_account() -> None:
    client = APIClient()

    response = register(client, VALID_USER)

    assert response.status_code == status.HTTP_201_CREATED
    user = User.objects.get(email=VALID_USER["email"])
    assert user.first_name == "John"
    assert user.last_name == "Smith"
    assert user.check_password(VALID_USER["password"])
    # UserCreate.perform_create logs the new user in on the spot.
    assert client.session["_auth_user_id"] == str(user.pk)
    assert response.json() == {
        "id": user.pk,
        "email": VALID_USER["email"],
        "firstName": "John",
        "lastName": "Smith",
    }


def test_registration_does_not_collect_date_of_birth() -> None:
    """
    Policy: registration does not ask for a date of birth, so the field
    is not part of the serializer and a submitted one is ignored like
    any other unknown key. (The profile endpoint still allows setting
    it later.)
    """
    response = register(APIClient(), {**VALID_USER, "date_of_birth": "2000-02-01"})

    assert response.status_code == status.HTTP_201_CREATED
    assert User.objects.get(email=VALID_USER["email"]).date_of_birth is None
    assert "dateOfBirth" not in response.json()


def test_duplicate_email_is_rejected() -> None:
    client = APIClient()
    register(client, VALID_USER)

    response = register(APIClient(), VALID_USER)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert User.objects.filter(email=VALID_USER["email"]).count() == 1


def test_invalid_email_is_rejected() -> None:
    response = register(APIClient(), {**VALID_USER, "email": "not-an-address"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not User.objects.exists()


@pytest.mark.parametrize("missing_field", ["email", "first_name", "last_name", "password"])
def test_mandatory_fields_are_enforced(missing_field: str) -> None:
    payload = {key: value for key, value in VALID_USER.items() if key != missing_field}

    response = register(APIClient(), payload)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert [error["attr"] for error in response.json()["errors"]] == [missing_field]
    assert not User.objects.exists()
