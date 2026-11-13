"""
Optional two-factor authentication: setting it up, and every way in asking
for the code once it is on.
"""

import time

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.test import Client
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework import status
from rest_framework.test import APIClient

from fk.models import User

pytestmark = pytest.mark.django_db

EMAIL = "two-factor@example.test"
PASSWORD = "correct horse battery staple"


def code(device: TOTPDevice, steps_ahead: int = 0) -> str:
    """The app's code, `steps_ahead` 30-second windows from now.

    A TOTPDevice refuses a code from a window it has already accepted one
    from, so a test that needs several codes takes them from later windows
    (within the device's tolerance of one).
    """
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time() + steps_ahead * device.step
    return f"{totp.token():0{device.digits}d}"


@pytest.fixture
def account() -> User:
    return User.objects.create_user(email=EMAIL, password=PASSWORD)


@pytest.fixture
def client(account: User) -> APIClient:
    client = APIClient()
    assert client.login(username=EMAIL, password=PASSWORD)
    return client


def enable(client: APIClient) -> tuple[TOTPDevice, list[str]]:
    setup = client.post(reverse("api-user-2fa-setup"))
    assert setup.status_code == status.HTTP_200_OK, setup.json()
    device = TOTPDevice.objects.get(confirmed=False)
    confirm = client.post(reverse("api-user-2fa-confirm"), {"code": code(device)}, format="json")
    assert confirm.status_code == status.HTTP_201_CREATED, confirm.json()
    device.refresh_from_db()
    return device, confirm.json()["recoveryCodes"]


def login(payload: dict):
    return APIClient().post(reverse("api-user-login"), payload, format="json")


def errors(response) -> set[tuple[str, str]]:
    return {(e["attr"], e["code"]) for e in response.json()["errors"]}


# Setting it up


def test_setup_returns_a_secret_and_a_qr_code(client: APIClient) -> None:
    response = client.post(reverse("api-user-2fa-setup"))

    body = response.json()
    assert body["otpauthUrl"].startswith("otpauth://totp/Frikanalen")
    assert body["qrCode"].startswith("data:image/svg+xml;base64,")
    assert len(body["secret"]) >= 16
    # Nothing is protected until the app has proved itself.
    assert client.get(reverse("api-user-2fa")).json() == {"enabled": False, "recoveryCodesLeft": 0}


def test_confirm_needs_a_right_code(client: APIClient) -> None:
    client.post(reverse("api-user-2fa-setup"))

    response = client.post(reverse("api-user-2fa-confirm"), {"code": "000000"}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not TOTPDevice.objects.filter(confirmed=True).exists()


def test_confirm_turns_it_on_and_hands_out_recovery_codes(client: APIClient) -> None:
    _, recovery = enable(client)

    assert len(recovery) == 10
    assert len(set(recovery)) == 10
    assert client.get(reverse("api-user-2fa")).json() == {"enabled": True, "recoveryCodesLeft": 10}


def test_setup_again_is_refused_while_on(client: APIClient) -> None:
    enable(client)

    assert client.post(reverse("api-user-2fa-setup")).status_code == status.HTTP_400_BAD_REQUEST


# Logging in


def test_login_without_two_factor_is_unchanged(account: User) -> None:
    assert login({"email": EMAIL, "password": PASSWORD}).status_code == status.HTTP_200_OK


def test_login_asks_for_the_code(client: APIClient) -> None:
    enable(client)

    response = login({"email": EMAIL, "password": PASSWORD})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert errors(response) == {("otp", "otp_required")}


def test_a_wrong_password_never_reveals_two_factor(client: APIClient) -> None:
    enable(client)

    response = login({"email": EMAIL, "password": "wrong"})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_login_with_the_code(client: APIClient) -> None:
    device, _ = enable(client)
    session = APIClient()

    response = session.post(
        reverse("api-user-login"),
        {"email": EMAIL, "password": PASSWORD, "otp": code(device, 1)},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert session.get(reverse("api-user-detail")).status_code == status.HTTP_200_OK


def test_login_with_a_wrong_code(client: APIClient) -> None:
    enable(client)

    response = login({"email": EMAIL, "password": PASSWORD, "otp": "123456"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert errors(response) == {("otp", "invalid")}


def test_a_recovery_code_works_once(client: APIClient) -> None:
    _, recovery = enable(client)
    payload = {"email": EMAIL, "password": PASSWORD, "otp": recovery[0].upper()}

    assert login(payload).status_code == status.HTTP_200_OK
    assert login(payload).status_code == status.HTTP_400_BAD_REQUEST
    assert StaticToken.objects.count() == 9


def test_api_token_asks_for_the_code(client: APIClient) -> None:
    device, _ = enable(client)
    url = reverse("api-token-auth")

    without = APIClient().post(url, {"username": EMAIL, "password": PASSWORD}, format="json")
    with_code = APIClient().post(
        url, {"username": EMAIL, "password": PASSWORD, "otp": code(device, 1)}, format="json"
    )

    assert without.status_code == status.HTTP_400_BAD_REQUEST
    assert errors(without) == {("otp", "otp_required")}
    assert with_code.status_code == status.HTTP_200_OK
    assert "token" in with_code.json()


def test_password_reset_asks_for_the_code(client: APIClient, account: User) -> None:
    device, _ = enable(client)
    account.refresh_from_db()  # the token covers last_login, which logging in moved
    url = reverse("api-user-password-reset-confirm")
    payload = {
        "uid": urlsafe_base64_encode(force_bytes(account.pk)),
        "token": default_token_generator.make_token(account),
        "newPassword": "a brand new tune for the kettle",
    }

    without = APIClient().post(url, payload, format="json")
    account.refresh_from_db()
    assert errors(without) == {("otp", "otp_required")}
    assert account.check_password(PASSWORD)

    with_code = APIClient().post(url, {**payload, "otp": code(device, 1)}, format="json")
    assert with_code.status_code == status.HTTP_200_OK
    account.refresh_from_db()
    assert account.check_password("a brand new tune for the kettle")


def test_admin_login_asks_for_the_code(client: APIClient, account: User) -> None:
    account.is_superuser = True
    account.save()
    device, _ = enable(client)
    url = reverse("admin:login")

    browser = Client()
    page = browser.post(url, {"username": EMAIL, "password": PASSWORD})
    assert page.status_code == status.HTTP_200_OK  # the form again, with an error
    assert "_auth_user_id" not in browser.session

    page = browser.post(
        url, {"username": EMAIL, "password": PASSWORD, "otp_token": code(device, 1)}
    )
    assert page.status_code == status.HTTP_302_FOUND
    assert browser.session["_auth_user_id"] == str(account.pk)


def test_admin_login_without_two_factor_needs_no_code(account: User) -> None:
    account.is_superuser = True
    account.save()

    page = Client().post(reverse("admin:login"), {"username": EMAIL, "password": PASSWORD})

    assert page.status_code == status.HTTP_302_FOUND


# Turning it off, new recovery codes


def test_disable_needs_a_code(client: APIClient) -> None:
    device, _ = enable(client)

    refused = client.post(reverse("api-user-2fa-disable"), {"code": "000000"}, format="json")
    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    # A failure locks the device for a moment; let it pass for the test.
    device.refresh_from_db()
    device.throttle_reset()

    done = client.post(reverse("api-user-2fa-disable"), {"code": code(device, 1)}, format="json")
    assert done.status_code == status.HTTP_204_NO_CONTENT
    assert login({"email": EMAIL, "password": PASSWORD}).status_code == status.HTTP_200_OK


def test_new_recovery_codes_replace_the_old(client: APIClient) -> None:
    device, old = enable(client)

    response = client.post(
        reverse("api-user-2fa-recovery-codes"), {"code": code(device, 1)}, format="json"
    )

    new = response.json()["recoveryCodes"]
    assert len(new) == 10
    assert not set(old) & set(new)
    stale = login({"email": EMAIL, "password": PASSWORD, "otp": old[0]})
    assert stale.status_code == status.HTTP_400_BAD_REQUEST


def test_two_factor_endpoints_need_a_login(db) -> None:
    assert APIClient().get(reverse("api-user-2fa")).status_code in (401, 403)
    assert APIClient().post(reverse("api-user-2fa-setup")).status_code in (401, 403)


def test_after_a_wrong_code_the_app_backs_off(client: APIClient) -> None:
    device, _ = enable(client)
    login({"email": EMAIL, "password": PASSWORD, "otp": "000000"})

    response = login({"email": EMAIL, "password": PASSWORD, "otp": code(device, 1)})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert errors(response) == {("otp", "throttled")}
