from uuid import uuid4

import pytest
from django.core import signing
from django.urls import reverse
from rest_framework.test import APIClient

from fk import receiver
from fk.models import LiveSource, Organization, User

pytestmark = pytest.mark.django_db

SRT_ID = "b6f4a1c2-0000-4000-8000-000000000001"


@pytest.fixture
def member() -> User:
    return User.objects.create(email="member@example.test")


@pytest.fixture
def source(member) -> LiveSource:
    organization = Organization.objects.create(name="Contributor")
    organization.members.add(member)
    return LiveSource.objects.create(name="Studio", organization=organization)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def get_signal(user, source):
    return client_for(user).get(reverse("api-live-source-signal", args=[source.pk]))


@pytest.fixture
def receiver_api(monkeypatch):
    """Stands in for MediaMTX's API: a dict of path to body, missing means 404."""
    answers: dict[str, dict] = {}
    asked: list[str] = []

    def get(path):
        asked.append(path)
        return answers.get(path)

    monkeypatch.setattr(receiver, "_get", get)
    answers["asked"] = asked  # type: ignore[assignment]
    return answers


def online_path(live_source, **overrides):
    return {
        "name": live_source.contribution_path,
        "online": True,
        "onlineTime": "2026-11-13T10:00:00Z",
        "source": {"type": "srtConn", "id": SRT_ID},
        "tracks2": [
            {
                "codec": "H264",
                "codecProps": {"width": 1920, "height": 1080, "profile": "High", "level": "4.2"},
            },
            {"codec": "MPEG-4 Audio", "codecProps": {"sampleRate": 48000, "channelCount": 2}},
        ],
        "inboundBytes": 123456789,
        "inboundFramesInError": 3,
        **overrides,
    }


def test_reports_the_signal_as_the_encoder_sends_it(member, source, receiver_api):
    receiver_api[f"/v3/paths/get/{source.contribution_path}"] = online_path(source)
    receiver_api[f"/v3/srt/conns/get/{SRT_ID}"] = {
        "packetsReceived": 100000,
        "packetsReceivedLoss": 12,
        "packetsReceivedRetrans": 10,
        "packetsReceivedDrop": 2,
        "msRTT": 23.5,
    }

    response = get_signal(member, source)

    assert response.status_code == 200
    body = response.json()
    assert body.pop("measuredAt")
    assert body == {
        "available": True,
        "receiving": True,
        "since": "2026-11-13T10:00:00Z",
        "protocol": "SRT",
        "video": {"codec": "H264", "width": 1920, "height": 1080, "profile": "High"},
        "audio": {"codec": "MPEG-4 Audio", "sampleRate": 48000, "channelCount": 2},
        "bytesReceived": 123456789,
        "framesInError": 3,
        "srt": {
            "packetsReceived": 100000,
            "packetsLost": 12,
            "packetsRetransmitted": 10,
            "packetsDropped": 2,
            "roundTripMs": 23.5,
        },
    }


def test_rtmp_has_no_transport_counters_and_may_lack_audio(member, source, receiver_api):
    receiver_api[f"/v3/paths/get/{source.contribution_path}"] = online_path(
        source,
        source={"type": "rtmpConn", "id": str(uuid4())},
        tracks2=[{"codec": "H264", "codecProps": {"width": 1280, "height": 720}}],
    )

    body = get_signal(member, source).json()

    assert body["protocol"] == "RTMP"
    assert body["srt"] is None
    assert body["audio"] is None
    assert body["video"]["height"] == 720
    assert receiver_api["asked"] == [f"/v3/paths/get/{source.contribution_path}"]


def test_a_pulled_source_does_not_name_the_relays_protocol(member, source, receiver_api):
    source.set_pull_url("rtsp://camera.example.test/stream")
    source.save()
    receiver_api[f"/v3/paths/get/{source.contribution_path}"] = online_path(
        source, source={"type": "rtspSession", "id": str(uuid4())}
    )

    assert get_signal(member, source).json()["protocol"] == ""


@pytest.mark.parametrize("path", [None, {"online": False}])
def test_nothing_arriving(member, source, receiver_api, path):
    if path is not None:
        receiver_api[f"/v3/paths/get/{source.contribution_path}"] = path

    body = get_signal(member, source).json()

    assert body["receiving"] is False
    assert body["video"] is None


def test_a_disabled_source_is_not_asked_about(member, source, receiver_api):
    source.enabled = False
    source.save()

    assert get_signal(member, source).json()["receiving"] is False
    assert receiver_api["asked"] == []


def test_an_unconfigured_receiver_is_an_answer_not_an_error(member, source, settings):
    settings.FK_MEDIAMTX_API_URL = ""

    response = get_signal(member, source)

    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["receiving"] is False


def test_only_the_organizations_own_people(source, receiver_api):
    outsider = User.objects.create(email="outsider@example.test")

    assert get_signal(outsider, source).status_code == 404
    assert APIClient().get(reverse("api-live-source-signal", args=[source.pk])).status_code in (
        401,
        403,
    )


def mediamtx(**payload):
    return APIClient(enforce_csrf_checks=True).post(
        reverse("api-mediamtx-auth"),
        {
            "user": "",
            "password": "",
            "token": "",
            "ip": "10.1.2.3",
            "action": "api",
            "path": "",
            "protocol": "",
            "id": None,
            "query": "",
            **payload,
        },
        format="json",
    )


def test_mediamtx_lets_djangos_own_token_use_its_api():
    assert mediamtx(token=receiver.issue_api_token()).status_code == 204


def test_mediamtx_api_is_closed_to_everything_else(member, source):
    preview = client_for(member).post(reverse("api-live-source-preview-token", args=[source.pk]))

    assert mediamtx().status_code == 401
    assert mediamtx(token="nonsense").status_code == 401
    assert mediamtx(token=preview.json()["token"]).status_code == 401
    # Loopback stands in for a credential for the converters, never for the API.
    assert mediamtx(ip="127.0.0.1").status_code == 401
    # Nor does the API token open anything but the API.
    assert mediamtx(token=receiver.issue_api_token(), action="read").status_code == 401
    assert mediamtx(token=receiver.issue_api_token(), action="publish").status_code == 401


def test_api_token_expires(monkeypatch):
    token = receiver.issue_api_token()
    monkeypatch.setattr(receiver, "TOKEN_TTL", receiver.TOKEN_TTL * 0 - receiver.TOKEN_TTL)

    assert receiver.verify_api_token(token) is False
    assert isinstance(signing.loads(token, salt=receiver.SALT), str)
