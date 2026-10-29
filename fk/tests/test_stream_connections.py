from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest

from fk.models import LiveSource, StreamCredential
from fk.stream_connections import connection_examples


@pytest.fixture
def endpoints(settings):
    settings.FK_MEDIAMTX_SRT_URL = "srt://contribute.example.test:8890"
    settings.FK_MEDIAMTX_RTMP_URL = "rtmps://contribute.example.test:1936/"
    settings.FK_MEDIAMTX_RTSP_URL = "rtsps://contribute.example.test:8322/"
    settings.FK_MEDIAMTX_WEBRTC_URL = "https://contribute.example.test/rtc/"
    settings.FK_MEDIAMTX_HLS_URL = "https://contribute.example.test/hls/"


@pytest.mark.parametrize("action", ["publish", "read"])
def test_connection_urls_use_configured_listeners_and_credential_scope(endpoints, action):
    source = LiveSource(stream_id=UUID("11111111-1111-1111-1111-111111111111"))
    credential = StreamCredential(
        source=source,
        action=action,
        key_id=UUID("22222222-2222-2222-2222-222222222222"),
    )
    secret = "copy-this-password"
    examples = {item["label"]: item for item in connection_examples(credential, secret)}
    path = f"live/{source.stream_id}"
    srt = urlsplit(examples["SRT"]["url"])
    assert srt.netloc == "contribute.example.test:8890"
    assert parse_qs(srt.query) == {
        "streamid": [f"{action}:{path}:{credential.key_id}:{secret}"],
        "pkt_size": ["1316"],
    }
    rtmp = urlsplit(examples["RTMP"]["url"])
    assert rtmp.scheme == "rtmps"
    assert rtmp.path == f"/{path}"
    assert parse_qs(rtmp.query) == {"user": [str(credential.key_id)], "pass": [secret]}
    rtsp = urlsplit(examples["RTSP"]["url"])
    assert rtsp.scheme == "rtsps"
    assert rtsp.username == str(credential.key_id)
    assert rtsp.password == secret
    assert rtsp.path == f"/{path}"
    label, suffix = ("WebRTC / WHIP", "whip") if action == "publish" else ("WebRTC / WHEP", "whep")
    assert examples[label] == {
        "label": label,
        "url": f"https://contribute.example.test/rtc/{path}/{suffix}",
        "token": f"{credential.key_id}:{secret}",
    }
    assert ("HLS" in examples) == (action == "read")
    if action == "read":
        assert examples["HLS"]["url"] == f"https://contribute.example.test/hls/{path}/index.m3u8"


def test_unconfigured_listeners_are_not_advertised(settings):
    for protocol in ["SRT", "RTMP", "RTSP", "WEBRTC", "HLS"]:
        setattr(settings, f"FK_MEDIAMTX_{protocol}_URL", "")
    assert connection_examples(StreamCredential(source=LiveSource()), "secret") == []
