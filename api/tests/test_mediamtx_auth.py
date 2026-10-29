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
        "path": credential.source.contribution_path if credential.source else "",
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
    assert (
        authenticate(credential, secret, path=f"contrib/live/{other.stream_id}").status_code == 401
    )
    assert (
        authenticate(
            credential, secret, path=f"/contrib/live/{credential.source.stream_id}"
        ).status_code
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
        ("read", "contrib/live", "rtsp", True),
        ("publish", "contrib/preview", "rtsp", True),
        ("publish", "contrib/live", "rtsp", False),
        ("read", "contrib/preview", "rtsp", False),
        ("publish", "contrib/preview", "srt", False),
        ("read", "contrib/live", "webrtc", False),
        ("convert", "contrib/preview", "rtsp", False),
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
    path = f"contrib/preview/{credential.source.stream_id}"
    assert authenticate(credential, secret, path=path).status_code == 401
    credential.action = StreamCredential.Action.READ
    credential.save()
    assert (
        authenticate(credential, secret, action="read", protocol="webrtc", path=path).status_code
        == 204
    )
    assert authenticate(credential, secret, action="publish", path=path).status_code == 401
    assert (
        authenticate(
            credential, secret, action="read", path=f"contrib/preview/{uuid4()}"
        ).status_code
        == 401
    )


def test_worker_cannot_access_another_source(issued):
    credential, secret = issued
    credential.action = StreamCredential.Action.CONVERT
    credential.save()
    for action, prefix in [("read", "contrib/live"), ("publish", "contrib/preview")]:
        assert (
            authenticate(
                credential, secret, action=action, protocol="rtsp", path=f"{prefix}/{uuid4()}"
            ).status_code
            == 401
        )


def test_paths_outside_contrib_prefix_are_rejected(issued):
    credential, secret = issued
    assert (
        authenticate(credential, secret, path=f"live/{credential.source.stream_id}").status_code
        == 401
    )


@pytest.fixture
def playout():
    org = Organization.objects.create(name="Contributor")
    sources = [
        LiveSource.objects.create(name="Studio A", organization=org),
        LiveSource.objects.create(name="Studio B", organization=org),
    ]
    credential = StreamCredential(label="Playout", action=StreamCredential.Action.PLAYOUT)
    secret = credential.generate_secret()
    credential.save()
    return credential, secret, sources


def read_as_playout(credential, secret, path, **overrides):
    overrides = {"action": "read", "protocol": "rtsp", "path": path, **overrides}
    return authenticate(credential, secret, **overrides).status_code


def test_playout_reads_every_enabled_sources_contribution(playout):
    credential, secret, sources = playout
    for source in sources:
        assert read_as_playout(credential, secret, source.contribution_path) == 204
    # A source added after the credential was issued needs nothing new.
    later = LiveSource.objects.create(name="Late", organization=sources[0].organization)
    assert read_as_playout(credential, secret, later.contribution_path) == 204


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": "publish"},
        {"protocol": "webrtc"},
        {"protocol": "srt"},
        {"action": "playback"},
        {"action": "api"},
    ],
)
def test_playout_only_reads_over_rtsp(playout, overrides):
    credential, secret, sources = playout
    assert read_as_playout(credential, secret, sources[0].contribution_path, **overrides) == 401


def test_playout_cannot_read_previews_unknown_or_disabled_sources(playout):
    credential, secret, sources = playout
    source = sources[0]
    stream_id = str(source.stream_id)
    for path in [
        source.preview_path,
        f"contrib/live/{uuid4()}",
        f"live/{stream_id}",
        f"contrib/live/{stream_id.upper()}",
        f"contrib/live/{{{stream_id}}}",
        f"contrib/live/{stream_id}/extra",
        "contrib/live/not-a-uuid",
        "",
    ]:
        assert read_as_playout(credential, secret, path) == 401, path
    source.enabled = False
    source.save()
    assert read_as_playout(credential, secret, source.contribution_path) == 401


def test_playout_rejects_wrong_password_revocation_and_expiry(playout):
    credential, secret, sources = playout
    path = sources[0].contribution_path
    assert read_as_playout(credential, "wrong", path) == 401
    credential.expires_at = timezone.now() - timedelta(minutes=1)
    credential.save()
    assert read_as_playout(credential, secret, path) == 401
    credential.expires_at = None
    credential.revoked_at = timezone.now()
    credential.save()
    assert read_as_playout(credential, secret, path) == 401


def test_only_the_playout_reader_may_lack_a_source(playout):
    from django.core.exceptions import ValidationError
    from django.db import IntegrityError, transaction

    *_, sources = playout
    with pytest.raises(ValidationError):
        StreamCredential(label="Bad", action=StreamCredential.Action.READ).full_clean()
    with pytest.raises(ValidationError):
        StreamCredential(
            label="Bad", action=StreamCredential.Action.PLAYOUT, source=sources[0]
        ).full_clean()
    with pytest.raises(IntegrityError), transaction.atomic():
        StreamCredential.objects.create(
            label="Bad", action=StreamCredential.Action.READ, secret_hash="x"
        )


def as_local_converter(action, path, **overrides):
    payload = {
        "user": "",
        "password": "",
        "token": "",
        "ip": "127.0.0.1",
        "action": action,
        "path": path,
        "protocol": "rtsp",
        "id": str(uuid4()),
        "query": "",
        **overrides,
    }
    return (
        Client(enforce_csrf_checks=True)
        .post(reverse("api-mediamtx-auth"), payload, content_type="application/json")
        .status_code
    )


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1"])
def test_loopback_converter_reads_originals_and_publishes_previews(playout, ip):
    _, _, sources = playout
    for source in sources:
        assert as_local_converter("read", source.contribution_path, ip=ip) == 204
        assert as_local_converter("publish", source.preview_path, ip=ip) == 204


@pytest.mark.parametrize(
    "action,kind,overrides",
    [
        ("publish", "contribution_path", {}),
        ("read", "preview_path", {}),
        ("read", "contribution_path", {"ip": "192.0.2.1"}),
        ("read", "contribution_path", {"ip": "10.0.0.5"}),
        ("read", "contribution_path", {"ip": "not-an-ip"}),
        ("read", "contribution_path", {"protocol": "webrtc"}),
        ("read", "contribution_path", {"protocol": "srt"}),
    ],
)
def test_loopback_converter_gets_nothing_else(playout, action, kind, overrides):
    _, _, sources = playout
    assert as_local_converter(action, getattr(sources[0], kind), **overrides) == 401


def test_loopback_converter_cannot_touch_unknown_or_disabled_sources(playout):
    _, _, sources = playout
    assert as_local_converter("read", f"contrib/live/{uuid4()}") == 401
    source = sources[0]
    source.enabled = False
    source.save()
    assert as_local_converter("read", source.contribution_path) == 401
    assert as_local_converter("publish", source.preview_path) == 401
