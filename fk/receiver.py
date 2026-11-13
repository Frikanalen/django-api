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
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode

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


# --- History, from Prometheus -------------------------------------------
#
# MediaMTX's API knows only now. Prometheus scrapes MediaMTX's metrics, so
# it remembers what a source's signal did before anyone opened its page.

HISTORY_STEP_SECONDS = 30
HISTORY_MAX_MINUTES = 180

_BYTES = "paths_inbound_bytes"
_LOST = "srt_conns_packets_received_loss"
_DROPPED = "srt_conns_packets_received_drop"


def _prometheus_range(query, start, end, step):
    base = settings.FK_PROMETHEUS_URL
    if not base:
        raise ReceiverUnavailable("FK_PROMETHEUS_URL is not set")
    params = urlencode({"query": query, "start": start, "end": end, "step": step})
    url = f"{base.rstrip('/')}/api/v1/query_range?{params}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            body = json.load(response)
    except (OSError, ValueError) as error:
        raise ReceiverUnavailable(str(error)) from error
    if body.get("status") != "success":
        raise ReceiverUnavailable(str(body.get("error", "query failed")))
    return body["data"]["result"]


def history(source, minutes, now):
    """Samples of a source's signal over the last `minutes`, oldest first.

    One per scrape step from the first step with a signal in; a later step
    without one says so. Each holds what happened since the step before, in the shape the
    live page builds from the signal report: bitrate, packets lost and
    dropped (SRT), and whether the encoder had connected anew.
    """
    path = source.contribution_path
    step = HISTORY_STEP_SECONDS
    end = int(now.timestamp()) // step * step
    start = end - minutes * 60
    # One query for all four series. The path is ours (a UUID), not input.
    query = (
        f'{{__name__=~"paths|{_BYTES}",name="{path}"}} '
        f'or {{__name__=~"{_LOST}|{_DROPPED}",path="{path}"}}'
    )
    # Several series can carry one metric (a pod restart, a new SRT
    # connection): summed per instant.
    totals: dict[str, dict[int, float]] = {"ready": {}, _BYTES: {}, _LOST: {}, _DROPPED: {}}
    for series in _prometheus_range(query, start, end, step):
        labels = series.get("metric", {})
        name = labels.get("__name__")
        if name == "paths":
            if labels.get("state") != "ready":
                continue
            name = "ready"
        if name not in totals:
            continue
        for instant, value in series.get("values", []):
            at = int(float(instant))
            totals[name][at] = totals[name].get(at, 0.0) + float(value)

    samples = []
    previous = None
    for at in range(start, end + 1, step):
        if not totals["ready"].get(at):
            previous = None
            # Before the first signal there is nothing to tell; after it,
            # a step without one is a hole in the broadcast.
            if samples:
                samples.append(
                    {
                        "at": datetime.fromtimestamp(at, tz=UTC),
                        "receiving": False,
                        "bitrate": None,
                        "lost": 0,
                        "dropped": 0,
                        "reconnected": False,
                    }
                )
            continue
        received = totals[_BYTES].get(at)
        lost = totals[_LOST].get(at)
        dropped = totals[_DROPPED].get(at)
        sample = {
            "at": datetime.fromtimestamp(at, tz=UTC),
            "receiving": True,
            "bitrate": None,
            "lost": 0,
            "dropped": 0,
            "reconnected": False,
        }
        if previous is not None and previous["at"] == at - step:
            # Counters start over with a new connection.
            if received is not None and previous["received"] is not None:
                if received >= previous["received"]:
                    sample["bitrate"] = round((received - previous["received"]) * 8 / step)
                else:
                    sample["reconnected"] = True
            if not sample["reconnected"]:
                if lost is not None and previous["lost"] is not None:
                    sample["lost"] = max(0, round(lost - previous["lost"]))
                if dropped is not None and previous["dropped"] is not None:
                    sample["dropped"] = max(0, round(dropped - previous["dropped"]))
        samples.append(sample)
        previous = {"at": at, "received": received, "lost": lost, "dropped": dropped}
    return samples
