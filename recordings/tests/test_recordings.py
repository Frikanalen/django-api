"""Recording requests: MediaMTX paths, the hand-off to ingest, playback
access and what ingest reads and reports."""

import datetime
from datetime import timedelta
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from fk import receiver as receiver_module
from fk.models import LiveSource, Organization, User, Video
from recordings import playback, reconcile
from recordings.models import RecordingRequest

pytestmark = pytest.mark.django_db
State = RecordingRequest.State
UTC = datetime.UTC
START = datetime.datetime(2026, 11, 19, 13, 0, tzinfo=UTC)  # 14:00 Oslo


@pytest.fixture
def recording(settings) -> RecordingRequest:
    settings.FK_MEDIAMTX_PLAYBACK_URL = "http://mediamtx-playback:9996"
    settings.FK_INGEST_INTERNAL_URL = "http://ingest:8000"
    editor = User.objects.create(email="editor@example.test", identity_confirmed=True)
    org = Organization.objects.create(name="Frikanalen", editor=editor)
    source = LiveSource.objects.create(name="Democracy Now!", organization=org)
    video = Video.objects.create(
        name="Democracy Now! (opptak)",
        organization=org,
        creator=editor,
        duration=timedelta(hours=1),
    )
    return RecordingRequest.objects.create(
        source=source, video=video, starttime=START, endtime=START + timedelta(hours=1)
    )


class FakeReceiver:
    def __init__(self):
        self.paths: dict[str, dict] = {}
        self.removed: list[str] = []
        self.silent: set[str] = set()

    def path_config(self, name):
        return self.paths.get(name)

    def add_path(self, name, config):
        self.paths[name] = config

    def remove_path(self, name):
        self.removed.append(name)
        self.paths.pop(name, None)

    def replace_path(self, name, config):
        if name not in self.paths:  # as MediaMTX: 400, not configured
            raise receiver_module.ReceiverUnavailable("receiver answered 400")
        self.paths[name] = config

    def config_path_names(self, prefix):
        return [name for name in self.paths if name.startswith(prefix)]

    def recorded(self, name):
        return name not in self.silent


@pytest.fixture
def mediamtx(monkeypatch) -> FakeReceiver:
    fake = FakeReceiver()
    for name in (
        "path_config",
        "add_path",
        "remove_path",
        "replace_path",
        "config_path_names",
        "recorded",
    ):
        monkeypatch.setattr(reconcile.receiver, name, getattr(fake, name))
    return fake


@pytest.fixture
def ingest(monkeypatch) -> list[str]:
    calls: list[str] = []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout):
        calls.append(request.full_url)
        return Answer()

    monkeypatch.setattr(reconcile.urllib.request, "urlopen", urlopen)
    return calls


def test_records_its_window_and_hands_it_to_ingest(recording, mediamtx, ingest) -> None:
    reconcile.reconcile(START - timedelta(minutes=2))
    assert mediamtx.paths == {}

    reconcile.reconcile(START - timedelta(seconds=50))
    path = f"contrib/record/{recording.pk}"
    assert mediamtx.paths[path] == {
        "source": f"srt://127.0.0.1:8890?streamid=read:{recording.source.contribution_path}",
        "sourceOnDemand": False,
        "record": True,
    }
    recording.refresh_from_db()
    assert recording.state == State.RECORDING

    # MediaMTX restarted and forgot it: put back.
    mediamtx.paths.clear()
    reconcile.reconcile(START + timedelta(minutes=30))
    assert path in mediamtx.paths
    assert ingest == []

    reconcile.reconcile(START + timedelta(hours=1, seconds=61))
    # Stopped, but kept: playback serves only configured paths.
    assert mediamtx.paths[path] == {"record": False}
    assert ingest == [f"http://ingest:8000/internal/recordings/{recording.pk}"]
    recording.refresh_from_db()
    assert (recording.state, recording.import_attempts) == (State.IMPORTING, 1)

    # MediaMTX restarted while ingest fetches: put back, stopped.
    mediamtx.paths.clear()
    reconcile.reconcile(START + timedelta(hours=1, minutes=2))
    assert mediamtx.paths[path] == {"record": False}

    # Archived: the path goes.
    recording.state = State.ARCHIVED
    recording.save()
    reconcile.reconcile(START + timedelta(hours=1, minutes=10))
    assert path not in mediamtx.paths


def test_asks_ingest_again_then_gives_up(recording, mediamtx, ingest) -> None:
    recording.state = State.IMPORTING
    recording.import_attempts = 1
    recording.import_requested_at = START + timedelta(hours=1)
    recording.save()
    later = START + timedelta(hours=1, minutes=21)
    reconcile.reconcile(later)
    reconcile.reconcile(later + timedelta(minutes=21))
    reconcile.reconcile(later + timedelta(minutes=42))
    recording.refresh_from_db()
    assert len(ingest) == 2
    assert recording.state == State.FAILED


def test_a_cancelled_request_keeps_no_path(recording, mediamtx, ingest) -> None:
    reconcile.reconcile(START)
    recording.state = State.CANCELLED
    recording.save()
    reconcile.reconcile(START + timedelta(minutes=5))
    assert mediamtx.paths == {}
    assert ingest == []


def test_ingest_reads_the_window_and_reports_back(recording) -> None:
    staff = User.objects.create(email="ingest@example.test", is_superuser=True)
    client = APIClient()
    client.force_authenticate(staff)
    data = client.get(reverse("api-recording-detail", args=[recording.pk])).json()
    assert data["video"] == recording.video_id
    url = urlsplit(data["playbackUrl"])
    assert (url.hostname, url.port, url.path) == ("mediamtx-playback", 9996, "/get")
    query = parse_qs(url.query)
    assert query["path"] == [recording.path]
    assert query["duration"] == ["3600"]
    assert datetime.datetime.fromisoformat(query["start"][0]) == START
    assert url.username == playback.USER
    assert playback.verify_token(unquote(url.password or ""), recording.path)

    response = client.post(
        reverse("api-recording-result", args=[recording.pk]), {"ok": True}, format="json"
    )
    assert response.status_code == 204
    recording.refresh_from_db()
    assert recording.state == State.ARCHIVED


def test_only_staff_read_recordings(recording) -> None:
    member = User.objects.create(email="member@example.test")
    client = APIClient()
    client.force_authenticate(member)
    response = client.get(reverse("api-recording-detail", args=[recording.pk]))
    assert response.status_code == 403


def playback_auth(path, password, user=playback.USER):
    return Client().post(
        reverse("api-mediamtx-auth"),
        {
            "user": user,
            "password": password,
            "token": "",
            "ip": "10.1.2.3",
            "action": "playback",
            "path": path,
            "protocol": "",
            "id": None,
            "query": "",
        },
        content_type="application/json",
    )


def test_playback_takes_the_signed_token_for_that_path_only(recording) -> None:
    token = playback.issue_token(recording.path)
    assert playback_auth(recording.path, token).status_code == 204
    assert playback_auth("contrib/record/999", token).status_code == 401
    assert playback_auth(recording.path, token, user="someone").status_code == 401
    assert playback_auth(recording.path, "forged").status_code == 401
    other = playback.issue_token(f"contrib/live/{recording.source.stream_id}")
    assert playback_auth(f"contrib/live/{recording.source.stream_id}", other).status_code == 401


def test_a_silent_source_fails_without_asking_ingest(recording, mediamtx, ingest) -> None:
    reconcile.reconcile(START)
    mediamtx.silent.add(recording.path)
    reconcile.reconcile(START + timedelta(hours=1, minutes=2))
    recording.refresh_from_db()
    assert recording.state == State.FAILED
    assert ingest == []
    assert mediamtx.paths == {}


def test_removes_paths_no_request_wants(recording, mediamtx, ingest) -> None:
    mediamtx.paths["contrib/record/999"] = {}
    mediamtx.paths["contrib/live/other"] = {}
    reconcile.reconcile(START)
    assert set(mediamtx.paths) == {recording.path, "contrib/live/other"}


def test_keeps_no_token_in_the_error(recording) -> None:
    staff = User.objects.create(email="ingest@example.test", is_superuser=True)
    client = APIClient()
    client.force_authenticate(staff)
    client.post(
        reverse("api-recording-result", args=[recording.pk]),
        {"ok": False, "error": "400 for http://recording:s3cr3t@mediamtx-playback:9996/get?x"},
        format="json",
    )
    recording.refresh_from_db()
    assert "s3cr3t" not in recording.error
    assert "mediamtx-playback:9996/get" in recording.error


def test_removing_a_path_uses_delete(monkeypatch, settings) -> None:
    import io
    import urllib.error

    from fk import receiver

    settings.FK_MEDIAMTX_API_URL = "http://mediamtx-api:9997"
    calls = []

    def urlopen(request, timeout):
        calls.append((request.get_method(), request.full_url))
        if request.get_method() != "DELETE":  # MediaMTX's router
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, io.BytesIO(b"404 page not found")
            )
        raise urllib.error.HTTPError(
            request.full_url, 404, "Not Found", {},
            io.BytesIO(b'{"status":"error","error":"path configuration not found"}'),
        )  # fmt: skip

    monkeypatch.setattr(receiver.urllib.request, "urlopen", urlopen)
    receiver.remove_path("contrib/record/3")  # already gone: fine
    assert calls == [("DELETE", "http://mediamtx-api:9997/v3/config/paths/delete/contrib/record/3")]
    with pytest.raises(receiver.ReceiverUnavailable):
        receiver._post("/v3/config/paths/delete/contrib/record/3")  # the wrong method


def test_a_window_just_past_is_stopped_not_swept(recording, mediamtx, ingest) -> None:
    reconcile.reconcile(START)
    reconcile.reconcile(START + timedelta(hours=1, minutes=1, seconds=5))
    assert mediamtx.paths[recording.path] == {"record": False}
    assert recording.path not in mediamtx.removed
    recording.refresh_from_db()
    assert recording.state == State.IMPORTING
