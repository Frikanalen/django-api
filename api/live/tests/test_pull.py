"""Pulled live sources: MediaMTX fetches the picture from an address."""

import base64
import json
from io import StringIO
from ipaddress import ip_address
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from fk import pull_sources
from fk.models import LiveSource, Organization, StreamCredential, User

pytestmark = pytest.mark.django_db

URL = "srt://partner.example:9000?streamid=read:feed&passphrase=hunter2hunter2"


@pytest.fixture(autouse=True)
def fast_hashes(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def resolver(monkeypatch):
    """Host names resolve to what the test says, without DNS."""
    answers = {"partner.example": ["8.8.8.8"], "internal.example": ["192.168.3.100"]}

    def resolve(host, port):
        try:
            return [str(ip_address(host))]
        except ValueError:
            pass
        if host not in answers:
            raise OSError("no such host")
        return answers[host]

    monkeypatch.setattr(pull_sources, "_resolve", resolve)
    return answers


@pytest.fixture
def organization() -> Organization:
    return Organization.objects.create(name="Contributor", fkmember=True)


@pytest.fixture
def member(organization) -> User:
    user = User.objects.create(email="member@example.test", identity_confirmed=True)
    organization.members.add(user)
    return user


@pytest.fixture
def pulled(organization) -> LiveSource:
    source = LiveSource(name="Partner", organization=organization)
    source.set_pull_url(URL)
    source.save()
    return source


@pytest.fixture
def relay():
    credential = StreamCredential(action=StreamCredential.Action.PULL, label="Relay")
    secret = credential.generate_secret()
    credential.save()
    return credential, secret


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def basic(credential, secret):
    raw = f"{credential.key_id}:{secret}".encode()
    return {"HTTP_AUTHORIZATION": "Basic " + base64.b64encode(raw).decode()}


def fetch(source, headers):
    return Client().get(reverse("api-mediamtx-pull", args=[source.stream_id]), **headers)


def mediamtx_auth(**payload):
    return Client().post(
        reverse("api-mediamtx-auth"),
        json.dumps({"user": "", "password": "", "token": "", "query": "", **payload}),
        content_type="application/json",
    )


# Address checks


@pytest.mark.parametrize(
    "url",
    [
        "srt://partner.example:9000?streamid=x",
        "rtmp://partner.example/live/key",
        "rtmps://partner.example/live/key",
        "rtsp://user:pw@partner.example/stream",
        "https://partner.example/live/index.m3u8?token=abc",
    ],
)
def test_public_addresses_of_known_protocols_pass(url):
    pull_sources.validate(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://partner.example/index.m3u8",
        "file:///etc/passwd",
        "udp://partner.example:1234",
        "srt://internal.example:9000",
        "rtsp://127.0.0.1/stream",
        "rtsp://[::1]/stream",
        "srt://10.0.0.1:9000",
        "srt://nowhere.example:9000",
        "srt://partner.example:9000 -i file:/etc/passwd",
        "srt://:9000",
    ],
)
def test_other_protocols_and_internal_hosts_are_refused(url):
    with pytest.raises(ValidationError):
        pull_sources.validate(url)


def test_the_redacted_target_keeps_only_scheme_host_and_port():
    assert pull_sources.redact(URL) == "srt://partner.example:9000"
    assert pull_sources.redact("rtmp://u:p@partner.example/live/key") == (
        "rtmp://partner.example:1935"
    )


def test_the_address_is_stored_encrypted_and_only_this_key_opens_it(pulled, settings):
    pulled.refresh_from_db()
    assert "hunter2" not in pulled.pull_url_encrypted
    assert pulled.get_pull_url() == URL
    # A database copied to another environment, with another key.
    settings.FK_PULL_URL_KEYS = [Fernet.generate_key().decode()]
    assert pulled.get_pull_url() is None


def test_a_rotated_key_still_opens_old_addresses(pulled, settings):
    settings.FK_PULL_URL_KEYS = [Fernet.generate_key().decode(), *settings.FK_PULL_URL_KEYS]
    assert pulled.get_pull_url() == URL


# Members' API


def test_a_member_creates_a_pulled_source_and_never_sees_the_address_again(member, organization):
    response = client_for(member).post(
        reverse("api-organization-live-source-list", args=[organization.pk]),
        {"name": "Partner", "pull_url": URL},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert "pull_url" not in response.data
    assert response.data["is_pulled"] is True
    assert response.data["pull_target"] == "srt://partner.example:9000"
    assert "hunter2" not in json.dumps(response.data)
    assert LiveSource.objects.get(pk=response.data["id"]).get_pull_url() == URL

    listed = client_for(member).get(
        reverse("api-organization-live-source-list", args=[organization.pk])
    )
    assert "hunter2" not in listed.content.decode()


def test_an_internal_address_is_refused_with_a_reason(member, organization):
    response = client_for(member).post(
        reverse("api-organization-live-source-list", args=[organization.pk]),
        {"name": "Sneaky", "pull_url": "rtsp://internal.example/stream"},
        format="json",
    )
    assert response.status_code == 400
    assert not LiveSource.objects.filter(name="Sneaky").exists()


def test_pulling_is_refused_where_no_key_is_set_up(member, organization, settings):
    settings.FK_PULL_URL_KEYS = []
    response = client_for(member).post(
        reverse("api-organization-live-source-list", args=[organization.pk]),
        {"name": "Partner", "pull_url": URL},
        format="json",
    )
    assert response.status_code == 400


def test_switching_to_pull_revokes_encoder_keys_and_back_clears_the_address(member, organization):
    source = LiveSource.objects.create(name="Studio", organization=organization)
    key = StreamCredential(source=source, label="ATEM")
    key.generate_secret()
    key.save()
    detail = reverse("api-live-source-detail", args=[source.pk])

    response = client_for(member).patch(detail, {"pull_url": URL}, format="json")
    assert response.status_code == 200, response.content
    key.refresh_from_db()
    assert key.revoked_at is not None

    response = client_for(member).patch(detail, {"pull_url": ""}, format="json")
    assert response.status_code == 200
    source.refresh_from_db()
    assert (source.is_pulled, source.pull_target, source.get_pull_url()) == (False, "", None)


def test_renaming_a_pulled_source_keeps_its_address(member, pulled):
    detail = reverse("api-live-source-detail", args=[pulled.pk])
    assert client_for(member).patch(detail, {"name": "Hall"}, format="json").status_code == 200
    pulled.refresh_from_db()
    assert pulled.get_pull_url() == URL


def test_a_pulled_source_takes_no_encoder_keys(member, pulled):
    response = client_for(member).post(
        reverse("api-live-source-encoder-keys", args=[pulled.pk]), {"label": "ATEM"}, format="json"
    )
    assert response.status_code == 403


# The relay's address lookup


def test_the_relay_gets_the_address_as_plain_text(pulled, relay):
    response = fetch(pulled, basic(*relay))
    assert response.status_code == 200
    assert response.content.decode() == URL
    assert "no-store" in response["Cache-Control"]


def test_the_relay_is_told_there_is_nothing_to_pull_for_a_pushed_source(organization, relay):
    pushed = LiveSource.objects.create(name="Studio", organization=organization)
    response = fetch(pushed, basic(*relay))
    assert (response.status_code, response.content) == (204, b"")


def test_a_disabled_pulled_source_is_not_pulled(pulled, relay):
    pulled.enabled = False
    pulled.save()
    assert fetch(pulled, basic(*relay)).status_code == 204


@pytest.mark.parametrize("case", ["none", "wrong", "revoked", "playout", "garbage"])
def test_only_the_relay_credential_gets_an_address(pulled, relay, case):
    credential, secret = relay
    if case == "none":
        headers = {}
    elif case == "wrong":
        headers = basic(credential, "not-it")
    elif case == "revoked":
        credential.revoked_at = credential.created_at
        credential.save()
        headers = basic(credential, secret)
    elif case == "playout":
        playout = StreamCredential(action=StreamCredential.Action.PLAYOUT, label="Playout")
        playout_secret = playout.generate_secret()
        playout.save()
        headers = basic(playout, playout_secret)
    else:
        headers = {"HTTP_AUTHORIZATION": "Basic !!!"}
    response = fetch(pulled, headers)
    assert response.status_code == 401
    assert "hunter2" not in response.content.decode()


def test_the_relay_credential_is_no_good_to_mediamtx(pulled, relay):
    credential, secret = relay
    response = mediamtx_auth(
        user=str(credential.key_id),
        password=secret,
        ip="192.0.2.1",
        action="read",
        path=pulled.contribution_path,
        protocol="rtsp",
    )
    assert response.status_code == 401


# MediaMTX publishing


def test_the_local_relay_may_publish_a_pulled_source(pulled):
    response = mediamtx_auth(
        ip="127.0.0.1", action="publish", path=pulled.contribution_path, protocol="rtsp"
    )
    assert response.status_code == 204


def test_the_local_relay_may_not_publish_a_pushed_source(organization):
    pushed = LiveSource.objects.create(name="Studio", organization=organization)
    response = mediamtx_auth(
        ip="127.0.0.1", action="publish", path=pushed.contribution_path, protocol="rtsp"
    )
    assert response.status_code == 401


def test_an_encoder_key_cannot_publish_to_a_pulled_source(pulled):
    # Issued before the switch and somehow not revoked.
    key = StreamCredential(source=pulled, label="ATEM")
    secret = key.generate_secret()
    key.save()
    response = mediamtx_auth(
        user=str(key.key_id),
        password=secret,
        ip="192.0.2.1",
        action="publish",
        path=pulled.contribution_path,
        protocol="srt",
    )
    assert response.status_code == 401


# Provisioning


def test_ensure_pull_credential_issues_once_and_keeps_it():
    out = StringIO()
    call_command("ensure_pull_credential", stdout=out)
    issued = json.loads(out.getvalue())
    assert issued["changed"] is True
    credential = StreamCredential.objects.get(key_id=issued["username"])
    assert credential.action == StreamCredential.Action.PULL
    assert credential.source is None

    out = StringIO()
    call_command("ensure_pull_credential", "--keep", issued["username"], stdout=out)
    assert json.loads(out.getvalue()) == {"changed": False, "username": issued["username"]}

    # The playout command leaves the relay's credential alone.
    call_command("ensure_playout_credential", stdout=StringIO())
    credential.refresh_from_db()
    assert credential.revoked_at is None


def test_unknown_stream_ids_look_like_pushed_ones(relay):
    response = Client().get(reverse("api-mediamtx-pull", args=[uuid4()]), **basic(*relay))
    assert response.status_code == 204
