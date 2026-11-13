"""
Passkeys and security keys: adding them, logging in with a passkey alone,
and a key as the second step after the password.

`SoftKey` plays the authenticator and the browser: it answers the options
the API hands out the way navigator.credentials would, with a real P-256
key, so py_webauthn checks real signatures.
"""

import hashlib
import json
import os
import struct

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp_webauthn.models import WebAuthnCredential
from rest_framework import status
from rest_framework.test import APIClient
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from api.auth.tests.test_two_factor import EMAIL, PASSWORD, enable, errors, status_of
from fk.models import User

pytestmark = pytest.mark.django_db

UP, UV, AT = 0x01, 0x04, 0x40


class SoftKey:
    """A software authenticator holding one credential."""

    def __init__(self, *, verifies_user: bool = True, discoverable: bool = True):
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = 0
        self.verifies_user = verifies_user
        self.discoverable = discoverable
        self.user_handle = b""

    @property
    def rp_id_hash(self) -> bytes:
        return hashlib.sha256(settings.OTP_WEBAUTHN_RP_ID.encode()).digest()

    def _flags(self) -> int:
        return UP | (UV if self.verifies_user else 0)

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": settings.OTP_WEBAUTHN_ALLOWED_ORIGINS[0],
                "crossOrigin": False,
            }
        ).encode()

    def create(self, options: dict) -> dict:
        assert options["rp"]["id"] == settings.OTP_WEBAUTHN_RP_ID
        self.user_handle = base64url_to_bytes(options["user"]["id"])
        numbers = self.private_key.public_key().public_numbers()
        cose_key = cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )
        auth_data = (
            self.rp_id_hash
            + bytes([self._flags() | AT])
            + struct.pack(">I", self.sign_count)
            + bytes(16)  # AAGUID
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + cose_key
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client_data = self._client_data("webauthn.create", options["challenge"])
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(attestation),
                "transports": ["usb"],
            },
            "clientExtensionResults": {"credProps": {"rk": self.discoverable}},
        }

    def get(self, options: dict) -> dict:
        allowed = [base64url_to_bytes(c["id"]) for c in options.get("allowCredentials", [])]
        if allowed:
            assert self.credential_id in allowed, "the browser would not offer this key"
        else:
            assert self.discoverable, "only a passkey can answer without being named"
        self.sign_count += 1
        auth_data = self.rp_id_hash + bytes([self._flags()]) + struct.pack(">I", self.sign_count)
        client_data = self._client_data("webauthn.get", options["challenge"])
        signature = self.private_key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": bytes_to_base64url(self.user_handle) if self.discoverable else None,
            },
            "clientExtensionResults": {},
        }


@pytest.fixture(autouse=True)
def passkeys_on(settings) -> None:
    settings.PASSKEYS_ENABLED = True


@pytest.fixture
def account() -> User:
    return User.objects.create_user(email=EMAIL, password=PASSWORD)


@pytest.fixture
def client(account: User) -> APIClient:
    client = APIClient()
    assert client.login(username=EMAIL, password=PASSWORD)
    return client


def add_key(client: APIClient, key: SoftKey, name: str = "YubiKey") -> dict:
    begin = client.post(reverse("api-user-2fa-webauthn-register-begin"))
    assert begin.status_code == status.HTTP_200_OK, begin.json()
    complete = client.post(
        reverse("api-user-2fa-webauthn-register-complete"),
        {"name": name, "credential": key.create(begin.json())},
        format="json",
    )
    assert complete.status_code == status.HTTP_201_CREATED, complete.json()
    return complete.json()


def passkey_login(session: APIClient, key: SoftKey):
    begin = session.post(reverse("api-user-login-passkey-begin"))
    assert begin.status_code == status.HTTP_200_OK, begin.json()
    return session.post(
        reverse("api-user-login-passkey-complete"),
        {"credential": key.get(begin.json())},
        format="json",
    )


def second_factor_with(session: APIClient, key: SoftKey):
    begin = session.post(reverse("api-user-2fa-webauthn-begin"))
    assert begin.status_code == status.HTTP_200_OK, begin.json()
    return session.post(
        reverse("api-user-2fa-webauthn-complete"),
        {"credential": key.get(begin.json())},
        format="json",
    )


# Adding a key


def test_adding_the_first_key_turns_two_factor_on(client: APIClient) -> None:
    body = add_key(client, SoftKey())

    assert len(body["recoveryCodes"]) == 10
    assert body["method"]["kind"] == "webauthn"
    assert body["method"]["name"] == "YubiKey"
    assert body["method"]["passkey"] is True
    state = status_of(client)
    assert state["enabled"] and state["verified"]


def test_registration_asks_for_a_passkey_and_skips_known_keys(client: APIClient) -> None:
    key = SoftKey()
    add_key(client, key)

    options = client.post(reverse("api-user-2fa-webauthn-register-begin")).json()

    assert options["authenticatorSelection"]["residentKey"] == "preferred"
    assert [c["id"] for c in options["excludeCredentials"]] == [
        bytes_to_base64url(key.credential_id)
    ]


def test_a_key_next_to_an_app_keeps_the_codes(client: APIClient) -> None:
    enable(client)

    body = add_key(client, SoftKey())

    assert body["recoveryCodes"] is None
    assert [m["kind"] for m in status_of(client)["methods"]] == ["totp", "webauthn"]


def test_a_tampered_answer_is_refused(client: APIClient) -> None:
    begin = client.post(reverse("api-user-2fa-webauthn-register-begin")).json()
    credential = SoftKey().create({**begin, "challenge": bytes_to_base64url(b"not the one")})

    response = client.post(
        reverse("api-user-2fa-webauthn-register-complete"),
        {"credential": credential},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not WebAuthnCredential.objects.exists()


def test_a_ceremony_cannot_be_completed_twice(client: APIClient) -> None:
    key = SoftKey()
    begin = client.post(reverse("api-user-2fa-webauthn-register-begin")).json()
    credential = key.create(begin)
    url = reverse("api-user-2fa-webauthn-register-complete")

    assert client.post(url, {"credential": credential}, format="json").status_code == 201
    again = client.post(url, {"credential": credential}, format="json")

    assert errors(again) == {("credential", "invalid_state")}


def test_an_unverified_session_cannot_add_a_key(client: APIClient) -> None:
    add_key(client, SoftKey())
    other = APIClient()
    assert other.login(username=EMAIL, password=PASSWORD)

    response = other.post(reverse("api-user-2fa-webauthn-register-begin"))

    assert response.status_code == status.HTTP_403_FORBIDDEN


# Passkey login


def test_passkey_login_without_email_or_password(client: APIClient) -> None:
    key = SoftKey()
    add_key(client, key)
    session = APIClient()

    response = passkey_login(session, key)

    assert response.status_code == status.HTTP_200_OK, response.json()
    assert response.json()["email"] == EMAIL
    assert status_of(session)["verified"]
    assert WebAuthnCredential.objects.get().last_used_at is not None


def test_passkey_login_needs_user_verification(client: APIClient) -> None:
    key = SoftKey()
    add_key(client, key)
    key.verifies_user = False  # a touch, but no PIN or fingerprint

    response = passkey_login(APIClient(), key)

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_passkey_login_with_an_unknown_key() -> None:
    response = passkey_login(APIClient(), SoftKey())

    assert errors(response) == {("credential", "credential_not_found")}


def test_passkey_login_refuses_a_deactivated_user(client: APIClient, account: User) -> None:
    key = SoftKey()
    add_key(client, key)
    account.is_active = False
    account.save()

    assert passkey_login(APIClient(), key).status_code == status.HTTP_403_FORBIDDEN


def test_a_replayed_answer_is_refused(client: APIClient) -> None:
    key = SoftKey()
    add_key(client, key)
    session = APIClient()
    begin = session.post(reverse("api-user-login-passkey-begin")).json()
    answer = key.get(begin)
    url = reverse("api-user-login-passkey-complete")

    assert session.post(url, {"credential": answer}, format="json").status_code == 200
    replay = APIClient()
    replay.post(reverse("api-user-login-passkey-begin"))
    assert replay.post(url, {"credential": answer}, format="json").status_code == 400


# A key as the second step


def test_login_with_password_then_security_key(client: APIClient) -> None:
    key = SoftKey(discoverable=False)  # an older key that stores nothing
    add_key(client, key)
    session = APIClient()
    url = reverse("api-user-login")
    payload = {"email": EMAIL, "password": PASSWORD}

    first = session.post(url, payload, format="json")
    assert errors(first) == {("otp", "otp_required")}

    assert second_factor_with(session, key).status_code == status.HTTP_204_NO_CONTENT
    done = session.post(url, payload, format="json")

    assert done.status_code == status.HTTP_200_OK, done.json()
    assert status_of(session)["verified"]


def test_the_security_key_step_needs_the_password_first(client: APIClient) -> None:
    add_key(client, SoftKey())

    response = APIClient().post(reverse("api-user-2fa-webauthn-begin"))

    assert errors(response) == {("detail", "no_pending_login")}


def test_an_app_only_account_is_told_to_use_a_code(client: APIClient) -> None:
    enable(client)
    session = APIClient()
    session.post(reverse("api-user-login"), {"email": EMAIL, "password": PASSWORD}, format="json")

    response = session.post(reverse("api-user-2fa-webauthn-begin"))

    assert errors(response) == {("detail", "no_security_key")}


def test_a_passed_key_step_counts_once(client: APIClient) -> None:
    key = SoftKey()
    add_key(client, key)
    session = APIClient()
    url = reverse("api-user-login")
    payload = {"email": EMAIL, "password": PASSWORD}
    session.post(url, payload, format="json")
    second_factor_with(session, key)
    assert session.post(url, payload, format="json").status_code == status.HTTP_200_OK
    session.post(reverse("api-user-logout"))

    again = session.post(url, payload, format="json")

    assert errors(again) == {("otp", "otp_required")}


def test_another_users_key_does_not_pass(client: APIClient) -> None:
    add_key(client, SoftKey())
    User.objects.create_user(email="other@example.test", password=PASSWORD)
    stranger = APIClient()
    assert stranger.login(username="other@example.test", password=PASSWORD)
    their_key = SoftKey()
    add_key(stranger, their_key)
    session = APIClient()
    session.post(reverse("api-user-login"), {"email": EMAIL, "password": PASSWORD}, format="json")

    begin = session.post(reverse("api-user-2fa-webauthn-begin")).json()
    answer = their_key.get({**begin, "allowCredentials": []})
    response = session.post(
        reverse("api-user-2fa-webauthn-complete"), {"credential": answer}, format="json"
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert (
        session.post(
            reverse("api-user-login"), {"email": EMAIL, "password": PASSWORD}, format="json"
        ).status_code
        == status.HTTP_400_BAD_REQUEST
    )


def test_password_reset_then_security_key(client: APIClient, account: User) -> None:
    key = SoftKey()
    add_key(client, key)
    account.refresh_from_db()
    url = reverse("api-user-password-reset-confirm")
    payload = {
        "uid": urlsafe_base64_encode(force_bytes(account.pk)),
        "token": default_token_generator.make_token(account),
        "newPassword": "a brand new tune for the kettle",
    }
    session = APIClient()

    assert errors(session.post(url, payload, format="json")) == {("otp", "otp_required")}
    assert second_factor_with(session, key).status_code == status.HTTP_204_NO_CONTENT
    done = session.post(url, payload, format="json")

    assert done.status_code == status.HTTP_200_OK, done.json()
    account.refresh_from_db()
    assert account.check_password("a brand new tune for the kettle")


def test_a_key_verifies_a_logged_in_session(client: APIClient) -> None:
    key = SoftKey()
    add_key(client, key)
    other = APIClient()
    assert other.login(username=EMAIL, password=PASSWORD)
    assert not status_of(other)["verified"]

    assert second_factor_with(other, key).status_code == status.HTTP_204_NO_CONTENT

    assert status_of(other)["verified"]


def test_removing_a_key(client: APIClient) -> None:
    key = SoftKey()
    add_key(client, key)
    method = status_of(client)["methods"][0]["id"]

    assert client.delete(reverse("api-user-2fa-method", args=[method])).status_code == 204

    assert not WebAuthnCredential.objects.exists()
    assert status_of(client)["enabled"] is False
    assert passkey_login(APIClient(), key).status_code == status.HTTP_400_BAD_REQUEST


# Switched off (PASSKEYS_ENABLED=False, as in production for now)


def test_switched_off_the_ceremonies_do_not_exist(settings, client: APIClient) -> None:
    settings.PASSKEYS_ENABLED = False

    assert APIClient().get(reverse("api-user-login-passkey")).json() == {"enabled": False}
    for name in (
        "api-user-login-passkey-begin",
        "api-user-2fa-webauthn-register-begin",
        "api-user-2fa-webauthn-begin",
    ):
        assert client.post(reverse(name)).status_code == status.HTTP_404_NOT_FOUND
    assert status_of(client)["passkeys"] is False


def test_switched_off_a_registered_key_does_not_count(settings, client: APIClient) -> None:
    add_key(client, SoftKey())
    settings.PASSKEYS_ENABLED = False

    assert status_of(client)["enabled"] is False
    assert status_of(client)["methods"] == []
    response = APIClient().post(
        reverse("api-user-login"), {"email": EMAIL, "password": PASSWORD}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK


def test_switched_on_the_login_page_is_told(client: APIClient) -> None:
    assert APIClient().get(reverse("api-user-login-passkey")).json() == {"enabled": True}
    assert status_of(client)["passkeys"] is True
