from urllib.parse import parse_qs, urlsplit
from uuid import UUID
from xml.etree import ElementTree

import pytest

from fk.models import LiveSource, StreamCredential
from fk.stream_connections import blackmagic_web_presenter_config, connection_examples


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
    path = f"contrib/live/{source.stream_id}"
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


def test_converter_examples_are_only_the_allowed_rtsp_paths(endpoints):
    source = LiveSource()
    credential = StreamCredential(source=source, action=StreamCredential.Action.CONVERT)
    examples = connection_examples(credential, "secret")
    assert len(examples) == 2
    assert all(item["url"].startswith("rtsps://") for item in examples)
    assert urlsplit(examples[0]["url"]).path == f"/contrib/live/{source.stream_id}"
    assert urlsplit(examples[1]["url"]).path == f"/contrib/preview/{source.stream_id}"


def test_read_credentials_include_derived_opus_whep_path(endpoints):
    source = LiveSource()
    credential = StreamCredential(source=source, action=StreamCredential.Action.READ)
    examples = connection_examples(credential, "secret")
    preview = next(item for item in examples if item["label"] == "WebRTC / Opus preview")
    assert (
        preview["url"]
        == f"https://contribute.example.test/rtc/contrib/preview/{source.stream_id}/whep"
    )
    assert preview["token"] == f"{credential.key_id}:secret"


def test_blackmagic_config_contains_publish_urls_and_debug_profiles(endpoints):
    source = LiveSource(
        name="Main & backup",
        stream_id=UUID("11111111-1111-1111-1111-111111111111"),
    )
    credential = StreamCredential(
        source=source,
        action=StreamCredential.Action.PUBLISH,
        key_id=UUID("22222222-2222-2222-2222-222222222222"),
    )
    document = ElementTree.fromstring(blackmagic_web_presenter_config(credential, "a&b"))
    servers = {
        server.findtext("name"): urlsplit(server.findtext("url"))
        for server in document.findall("./service/servers/server")
    }
    assert set(servers) == {"Main & backup (SRT)", "Main & backup (RTMP)"}
    assert servers["Main & backup (SRT)"].scheme == "srt"
    assert parse_qs(servers["Main & backup (SRT)"].query)["streamid"] == [
        f"publish:{source.contribution_path}:{credential.key_id}:a&b"
    ]
    rtmp = servers["Main & backup (RTMP)"]
    assert rtmp.scheme == "rtmps"
    assert rtmp.path == f"/{source.contribution_path}"
    assert parse_qs(rtmp.query) == {"user": [str(credential.key_id)], "pass": ["a&b"]}
    profiles = {
        profile.findtext("name"): {
            config.attrib["resolution"]: (config.attrib["fps"], config.findtext("bitrate"))
            for config in profile.findall("config")
        }
        for profile in document.findall("./service/profiles/profile")
    }
    assert profiles == {
        "Streaming Low": {"720p": ("50", "3000000"), "1080p": ("50", "4500000")},
        "Streaming High": {"720p": ("50", "6000000"), "1080p": ("50", "8000000")},
    }


def test_blackmagic_config_is_only_offered_for_publish_credentials(endpoints):
    credential = StreamCredential(source=LiveSource(), action=StreamCredential.Action.READ)
    assert blackmagic_web_presenter_config(credential, "secret") is None
