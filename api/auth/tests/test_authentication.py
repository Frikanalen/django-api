"""
Authentication flows: token issuance and the session login/logout
endpoints. UserLogin/UserLogout are what the frontend uses and had no
coverage at all.
"""

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import User

pytestmark = pytest.mark.django_db

EMAIL = "auth-flow@example.test"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def account() -> User:
    return User.objects.create_user(email=EMAIL, password=PASSWORD)


def obtain_token(payload: dict):
    return APIClient().post(reverse("api-token-auth"), payload, format="json")


def test_token_endpoint_requires_credentials(db) -> None:
    response = obtain_token({})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert [(error["code"], error["attr"]) for error in response.json()["errors"]] == [
        ("required", "username"),
        ("required", "password"),
    ]


def test_token_is_not_issued_for_a_wrong_password(account: User) -> None:
    response = obtain_token({"username": EMAIL, "password": "wrong"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert [(error["code"], error["detail"]) for error in response.json()["errors"]] == [
        ("authorization", "Unable to log in with provided credentials.")
    ]


def test_token_authenticates_its_owner(account: User) -> None:
    # The endpoint takes the email in a field called 'username'.
    response = obtain_token({"username": EMAIL, "password": PASSWORD})

    assert response.status_code == status.HTTP_200_OK
    assert set(response.json().keys()) == {"token"}
    token = response.json()["token"]
    # Tokens are minted by the post_save signal at registration; the
    # endpoint hands out that same token rather than a fresh one.
    assert token == account.auth_token.key

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    profile = client.get(reverse("api-user-detail"))
    assert profile.status_code == status.HTTP_200_OK
    assert profile.json()["email"] == EMAIL


def session_login(client: APIClient, payload: dict):
    return client.post(reverse("api-user-login"), payload, format="json")


def test_session_login_sets_a_session_and_returns_the_profile(account: User) -> None:
    client = APIClient()

    response = session_login(client, {"email": EMAIL, "password": PASSWORD})

    assert response.status_code == status.HTTP_200_OK
    assert client.session["_auth_user_id"] == str(account.pk)
    payload = response.json()
    assert payload["email"] == EMAIL
    assert set(payload.keys()) == {
        "id",
        "email",
        "isStaff",
        "dateJoined",
        "editorOf",
        "memberOf",
        "firstName",
        "lastName",
        "dateOfBirth",
        "phoneNumber",
    }


def test_session_login_rejects_a_wrong_password(account: User) -> None:
    client = APIClient()

    response = session_login(client, {"email": EMAIL, "password": "wrong"})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["errors"][0]["detail"] == "Incorrect email or password."
    assert "_auth_user_id" not in client.session


def test_session_login_rejects_an_inactive_user_as_bad_credentials(account: User) -> None:
    """
    UserLogin has a dedicated 'User is disabled' branch, but the default
    ModelBackend refuses inactive users before it is reached, so they
    get the same 401 as a wrong password. Pinned so a change of auth
    backend (which would suddenly activate that branch) shows up.
    """
    account.is_active = False
    account.save()
    client = APIClient()

    response = session_login(client, {"email": EMAIL, "password": PASSWORD})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["errors"][0]["detail"] == "Incorrect email or password."


def test_session_logout_clears_the_session(account: User) -> None:
    client = APIClient()
    session_login(client, {"email": EMAIL, "password": PASSWORD})

    response = client.post(reverse("api-user-logout"))

    assert response.status_code == status.HTTP_200_OK
    assert "_auth_user_id" not in client.session


def test_logout_requires_authentication(db) -> None:
    response = APIClient().post(reverse("api-user-logout"))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
