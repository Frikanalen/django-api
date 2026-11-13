"""
Password reset by email: request a link, follow it, set a new password.
"""

import re

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APIClient

from api.auth import password_reset
from fk.models import User

pytestmark = pytest.mark.django_db

EMAIL = "reset-me@example.test"
OLD_PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a brand new tune for the kettle"

# Throttles and the per-address limit need a cache that keeps things;
# the test settings' DummyCache forgets everything.
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@pytest.fixture(autouse=True)
def inline_mail(monkeypatch):
    monkeypatch.setattr(password_reset, "run_in_background", lambda f, *a, **kw: f(*a, **kw))


@pytest.fixture(autouse=True)
def fresh_cache(settings):
    settings.CACHES = LOCMEM
    settings.SITE_URL = "https://staging.frikanalen.tv"
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def account() -> User:
    user = User.objects.create_user(email=EMAIL, password=OLD_PASSWORD)
    user.first_name = "Kari"
    user.save()
    return user


def request_reset(email: str, client: APIClient | None = None):
    return (client or APIClient()).post(
        reverse("api-user-password-reset"), {"email": email}, format="json"
    )


def confirm(uid: str, token: str, password: str, client: APIClient | None = None):
    return (client or APIClient()).post(
        reverse("api-user-password-reset-confirm"),
        {"uid": uid, "token": token, "newPassword": password},
        format="json",
    )


def link_from(message) -> tuple[str, str]:
    match = re.search(
        r"https://staging\.frikanalen\.tv/password-reset/([^/\s]+)/([^/\s]+)", message.body
    )
    assert match, message.body
    return match.group(1), match.group(2)


def test_request_mails_a_link_to_the_frontend(account: User) -> None:
    response = request_reset("Reset-Me@Example.test")

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [EMAIL]
    assert message.subject == "Nytt passord for Frikanalen"
    assert "Hei Kari," in message.body
    uid, token = link_from(message)
    assert uid == urlsafe_base64_encode(force_bytes(account.pk))
    assert default_token_generator.check_token(account, token)


def test_unknown_address_gets_the_same_answer_and_no_mail(account: User) -> None:
    known = request_reset(EMAIL)
    mail.outbox.clear()
    unknown = request_reset("nobody@example.test")

    assert unknown.status_code == known.status_code
    assert unknown.json() == known.json()
    assert mail.outbox == []


def test_inactive_account_gets_no_mail(account: User) -> None:
    account.is_active = False
    account.save()

    response = request_reset(EMAIL)

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert mail.outbox == []


def test_one_mail_per_address_per_interval(account: User) -> None:
    first = request_reset(EMAIL)
    second = request_reset(EMAIL.upper())

    assert first.json() == second.json()
    assert len(mail.outbox) == 1


def test_requests_are_throttled_per_client(account: User) -> None:
    client = APIClient()
    for n in range(10):
        assert request_reset(f"someone{n}@example.test", client).status_code == 202

    assert request_reset("one-too-many@example.test", client).status_code == 429


def test_confirm_sets_password_logs_in_and_notifies(account: User) -> None:
    request_reset(EMAIL)
    uid, token = link_from(mail.outbox[0])
    client = APIClient()

    response = confirm(uid, token, NEW_PASSWORD, client)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["email"] == EMAIL
    account.refresh_from_db()
    assert account.check_password(NEW_PASSWORD)
    assert not account.check_password(OLD_PASSWORD)
    # Logged in by session: the profile endpoint answers.
    assert client.get(reverse("api-user-detail")).status_code == status.HTTP_200_OK
    assert len(mail.outbox) == 2
    assert mail.outbox[1].subject == "Passordet ditt på Frikanalen er endret"
    assert mail.outbox[1].to == [EMAIL]


def test_link_works_only_once(account: User) -> None:
    request_reset(EMAIL)
    uid, token = link_from(mail.outbox[0])
    assert confirm(uid, token, NEW_PASSWORD).status_code == status.HTTP_200_OK

    response = confirm(uid, token, "yet another fine password")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["errors"][0]["attr"] == "token"
    account.refresh_from_db()
    assert account.check_password(NEW_PASSWORD)


@pytest.mark.parametrize(
    ("uid", "token"),
    [
        ("not-base64!", "whatever"),
        (urlsafe_base64_encode(b"999999"), "whatever"),
        (None, "1a2b3c-0123456789abcdef"),
    ],
)
def test_bad_links_are_refused(account: User, uid, token) -> None:
    uid = uid or urlsafe_base64_encode(force_bytes(account.pk))

    response = confirm(uid, token, NEW_PASSWORD)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["errors"][0]["attr"] == "token"
    account.refresh_from_db()
    assert account.check_password(OLD_PASSWORD)


@override_settings(PASSWORD_RESET_TIMEOUT=-1)
def test_expired_link_is_refused(account: User) -> None:
    uid = urlsafe_base64_encode(force_bytes(account.pk))
    token = default_token_generator.make_token(account)

    response = confirm(uid, token, NEW_PASSWORD)

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_weak_password_is_refused_in_norwegian(account: User) -> None:
    uid = urlsafe_base64_encode(force_bytes(account.pk))
    token = default_token_generator.make_token(account)

    response = confirm(uid, token, "1234")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    errors = response.json()["errors"]
    assert {error["attr"] for error in errors} == {"new_password"}
    # Django has Norwegian for most validators (not, as of 5.2, the length one).
    assert any("bare tall" in error["detail"] for error in errors), errors
    account.refresh_from_db()
    assert account.check_password(OLD_PASSWORD)
    # The link still works after a rejected password.
    assert confirm(uid, token, NEW_PASSWORD).status_code == status.HTTP_200_OK
