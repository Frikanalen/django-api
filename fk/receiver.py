"""What MediaMTX, the contribution receiver, knows about a live source's signal.

Read from MediaMTX's control API, which describes the signal as the encoder
sends it. The preview a member watches is a small re-encoded copy, so the
browser's own numbers describe that copy and not their signal.

MediaMTX asks Django to authenticate every request, its API included.
Django is the API's only client, so it signs a short-lived token for itself
and recognizes it again in the authentication callback: no shared secret
to distribute.
"""

import json
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.core import signing

SALT = "fk.receiver-api"
TOKEN_TTL = timedelta(seconds=30)
TIMEOUT_SECONDS = 3

# How MediaMTX names where a path's signal comes from, as the protocol a
# member chose. Pulled sources arrive from MediaMTX's own relay over RTSP,
# which says nothing a member can use.
PROTOCOLS = {
    "srtConn": "SRT",
    "rtmpConn": "RTMP",
    "rtmpsConn": "RTMP",
    "rtspSession": "RTSP",
    "rtspsSession": "RTSP",
    "webRTCSession": "WebRTC",
}
VIDEO_CODECS = {"H264", "H265", "AV1", "VP8", "VP9", "MPEG4Video", "MPEG1Video", "MJPEG"}


class ReceiverUnavailable(Exception):
    """The receiver's API is not configured here, or did not answer."""


def issue_api_token():
    return signing.dumps("api", salt=SALT)


def verify_api_token(token):
    try:
        return signing.loads(token, salt=SALT, max_age=TOKEN_TTL) == "api"
    except signing.BadSignature:
        return False


def _get(path):
    """A GET on the receiver's API: the decoded body, or None for a 404."""
    base = settings.FK_MEDIAMTX_API_URL
    if not base:
        raise ReceiverUnavailable("FK_MEDIAMTX_API_URL is not set")
    request = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {issue_api_token()}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise ReceiverUnavailable(f"receiver answered {error.code}") from error
    except (OSError, ValueError) as error:
        raise ReceiverUnavailable(str(error)) from error


def _track(track):
    props = track.get("codecProps") or {}
    return track.get("codec") or "", props


def signal(source):
    """The signal now arriving on a source, as a dict for SignalSerializer."""
    path = _get(f"/v3/paths/get/{quote(source.contribution_path)}")
    # "online" replaced "ready" in MediaMTX's API; either says a signal is in.
    if not path or not (path.get("online") or path.get("ready")):
        return {"receiving": False}

    video = audio = None
    for codec, props in map(_track, path.get("tracks2") or []):
        if codec in VIDEO_CODECS and video is None:
            video = {
                "codec": codec,
                "width": props.get("width"),
                "height": props.get("height"),
                "profile": props.get("profile") or "",
            }
        elif codec not in VIDEO_CODECS and audio is None:
            audio = {
                "codec": codec,
                "sample_rate": props.get("sampleRate"),
                "channel_count": props.get("channelCount"),
            }

    origin = path.get("source") or {}
    result = {
        "receiving": True,
        "since": path.get("onlineTime") or path.get("readyTime"),
        "protocol": "" if source.is_pulled else PROTOCOLS.get(origin.get("type"), ""),
        "video": video,
        "audio": audio,
        "bytes_received": path.get("inboundBytes", path.get("bytesReceived", 0)),
        "frames_in_error": path.get("inboundFramesInError", 0),
        "srt": None,
    }
    if origin.get("type") == "srtConn" and origin.get("id"):
        try:
            connection = _get(f"/v3/srt/conns/get/{quote(origin['id'])}")
        except ReceiverUnavailable:
            connection = None
        if connection:
            result["srt"] = {
                "packets_received": connection.get("packetsReceived", 0),
                "packets_lost": connection.get("packetsReceivedLoss", 0),
                "packets_retransmitted": connection.get("packetsReceivedRetrans", 0),
                "packets_dropped": connection.get("packetsReceivedDrop", 0),
                "round_trip_ms": connection.get("msRTT"),
            }
    return result
