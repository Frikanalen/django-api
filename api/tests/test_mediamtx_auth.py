from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth.hashers import check_password
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from fk.models import LiveSource, Organization, Scheduleitem, StreamCredential

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fast_hashes(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def issued():
    org = Organization.objects.create(name="Contributor")
    source = LiveSource.objects.create(name="Studio", organization=org)
    credential = StreamCredential(source=source, label="Encoder")
    secret = credential.generate_secret()
    credential.save()
    return credential, secret


def authenticate(credential, secret, **overrides):
    payload = {
        "user": str(credential.key_id),
        "password": secret,
        "token": "",
        "ip": "192.0.2.1",
        "action": "publish",
        "path": f"live/{credential.source.stream_id}",
        "protocol": "srt",
        "id": str(uuid4()),
        "query": "",
        "userAgent": "",
        **overrides,
    }
    return Client(enforce_csrf_checks=True).post(
        reverse("api-mediamtx-auth"),
        payload,
        content_type="application/json",
    )


@pytest.mark.parametrize("protocol", ["srt", "rtmp", "rtsp", "webrtc", "moq"])
def test_accepts_publish_without_booking_or_django_session(issued, protocol):
    credential, secret = issued
    assert not Scheduleitem.objects.exists()
    response = authenticate(credential, secret, protocol=protocol)
    assert response.status_code == 204
    assert response.content == b""
    assert "no-store" in response["Cache-Control"]
    credential.refresh_from_db()
    assert credential.secret_hash != secret
    assert check_password(secret, credential.secret_hash)


@pytest.mark.parametrize(
    "overrides",
    [
        {"user": ""},
        {"password": ""},
        {"password": "incorrect"},
        {"user": str(uuid4())},
        {"user": "not-a-uuid"},
        {"user": None},
        {"password": []},
        {"password": "x" * 257},
        {"action": "read"},
        {"action": "playback"},
        {"action": "api"},
        {"action": "metrics"},
        {"action": "pprof"},
        {"action": "unknown"},
        {"action": []},
        {"path": ""},
        {"path": "live/another-source"},
        {"path": "live/../other"},
        {"path": None},
        {"path": []},
    ],
)
def test_denies_invalid_credentials_actions_and_paths(issued, overrides):
    credential, secret = issued
    response = authenticate(credential, secret, **overrides)
    assert response.status_code == 401
    assert response.content == b""
    assert secret.encode() not in response.content


@pytest.mark.parametrize("state", ["revoked", "expired", "disabled"])
def test_denies_inactive_credentials_or_sources(issued, state):
    credential, secret = issued
    if state == "revoked":
        credential.revoked_at = timezone.now()
    elif state == "expired":
        credential.expires_at = timezone.now() - timedelta(seconds=1)
    else:
        credential.source.enabled = False
        credential.source.save()
    credential.save()
    assert authenticate(credential, secret).status_code == 401


def test_expiry_is_returned_in_mediamtx_wire_format(issued):
    credential, secret = issued
    credential.expires_at = timezone.now() + timedelta(hours=1)
    credential.save()
    response = authenticate(credential, secret)
    assert response.status_code == 200
    assert response.json() == {"expireAt": credential.expires_at.isoformat()}


def test_reader_credentials_cannot_publish(issued):
    credential, secret = issued
    credential.action = StreamCredential.Action.READ
    credential.save()
    assert authenticate(credential, secret, action="read").status_code == 204
    assert authenticate(credential, secret, action="publish").status_code == 401


def test_another_sources_valid_path_is_not_authorized(issued):
    credential, secret = issued
    other = LiveSource.objects.create(
        name="Other studio", organization=credential.source.organization
    )
    assert authenticate(credential, secret, path=f"live/{other.stream_id}").status_code == 401
    assert (
        authenticate(credential, secret, path=f"/live/{credential.source.stream_id}").status_code
        == 401
    )


@pytest.mark.parametrize("body", ["{", "[]", "null", '"text"', "{}", '{"password": null}'])
def test_malformed_payloads_fail_closed(body):
    response = Client(enforce_csrf_checks=True).post(
        reverse("api-mediamtx-auth"),
        body,
        content_type="application/json",
    )
    assert response.status_code == 401


def test_only_json_post_is_accepted():
    client = Client(enforce_csrf_checks=True)
    url = reverse("api-mediamtx-auth")
    assert client.get(url).status_code == 405
    assert client.post(url, {"user": "someone"}).status_code == 401


@pytest.mark.parametrize(
    "action,prefix,protocol,allowed",
    [
        ("read", "live", "rtsp", True),
        ("publish", "preview", "rtsp", True),
        ("publish", "live", "rtsp", False),
        ("read", "preview", "rtsp", False),
        ("publish", "preview", "srt", False),
        ("read", "live", "webrtc", False),
        ("convert", "preview", "rtsp", False),
    ],
)
def test_conversion_worker_has_only_its_two_rtsp_operations(
    issued, action, prefix, protocol, allowed
):
    credential, secret = issued
    credential.action = StreamCredential.Action.CONVERT
    credential.save()
    response = authenticate(
        credential,
        secret,
        action=action,
        protocol=protocol,
        path=f"{prefix}/{credential.source.stream_id}",
    )
    assert response.status_code == (204 if allowed else 401)


def test_read_key_can_read_same_source_preview_but_encoder_cannot_publish_it(issued):
    credential, secret = issued
    path = f"preview/{credential.source.stream_id}"
    assert authenticate(credential, secret, path=path).status_code == 401
    credential.action = StreamCredential.Action.READ
    credential.save()
    assert (
        authenticate(credential, secret, action="read", protocol="webrtc", path=path).status_code
        == 204
    )
    assert authenticate(credential, secret, action="publish", path=path).status_code == 401
    assert (
        authenticate(credential, secret, action="read", path=f"preview/{uuid4()}").status_code
        == 401
    )


def test_worker_cannot_access_another_source(issued):
    credential, secret = issued
    credential.action = StreamCredential.Action.CONVERT
    credential.save()
    for action, prefix in [("read", "live"), ("publish", "preview")]:
        assert (
            authenticate(
                credential, secret, action=action, protocol="rtsp", path=f"{prefix}/{uuid4()}"
            ).status_code
            == 401
        )
