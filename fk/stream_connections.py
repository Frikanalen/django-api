"""Connection details for the one-time credential response, never persisted."""

from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

from django.conf import settings

from fk.models import LiveSource, StreamCredential

# Shown where the playout reader's path depends on the booked source.
PLACEHOLDER_STREAM_ID = "<source stream UUID>"


def blackmagic_web_presenter_config(credential, secret):
    """Return an importable Blackmagic Streaming XML document when applicable."""
    if credential.action != StreamCredential.Action.PUBLISH:
        return None

    urls = {
        example["label"]: example["url"]
        for example in connection_examples(credential, secret)
        if example["label"] in {"SRT", "RTMP"}
    }
    if not urls:
        return None

    streaming = ElementTree.Element("streaming")
    service = ElementTree.SubElement(streaming, "service")
    ElementTree.SubElement(service, "name").text = "Frikanalen"
    servers = ElementTree.SubElement(service, "servers")
    for protocol in ("SRT", "RTMP"):
        if protocol not in urls:
            continue
        server = ElementTree.SubElement(servers, "server")
        ElementTree.SubElement(server, "name").text = f"{credential.source.name} ({protocol})"
        ElementTree.SubElement(server, "url").text = urls[protocol]

    profiles = ElementTree.SubElement(service, "profiles", {"default": "Streaming High"})
    for profile_name, bitrates in (
        ("Streaming Low", {"720p": "3000000", "1080p": "4500000"}),
        ("Streaming High", {"720p": "6000000", "1080p": "8000000"}),
    ):
        profile = ElementTree.SubElement(profiles, "profile")
        ElementTree.SubElement(profile, "name").text = profile_name
        for resolution, bitrate in bitrates.items():
            config = ElementTree.SubElement(
                profile,
                "config",
                {"resolution": resolution, "fps": "50", "codec": "H264"},
            )
            ElementTree.SubElement(config, "bitrate").text = bitrate
            ElementTree.SubElement(config, "audio-bitrate").text = "128000"

    return ElementTree.tostring(streaming, encoding="unicode", xml_declaration=True)


def connection_examples(credential, secret):
    username = str(credential.key_id)
    examples = []

    if credential.action == StreamCredential.Action.PLAYOUT:
        # Playout takes the stream UUID from the schedule; it reads any
        # enabled source's contribution over RTSP and nothing else.
        if base := settings.FK_MEDIAMTX_RTSP_URL:
            path = LiveSource(stream_id=PLACEHOLDER_STREAM_ID).contribution_path
            parts = urlsplit(f"{base.rstrip('/')}/{path}")
            netloc = f"{quote(username, safe='')}:{quote(secret, safe='')}@{parts.netloc}"
            examples.append(
                {
                    "label": "RTSP (read any enabled source)",
                    "url": urlunsplit(parts._replace(netloc=netloc)),
                    "note": "Give playout LIVE_READER_CREDENTIAL=<username>:<password>.",
                }
            )
        return examples

    path = credential.source.contribution_path

    if credential.action == StreamCredential.Action.CONVERT:
        # Worker credentials are not encoder credentials: show only their
        # two permitted RTSP operations, never a bogus SRT "convert" URL.
        if base := settings.FK_MEDIAMTX_RTSP_URL:
            for label, stream_path in (
                ("Contribution input (read)", path),
                ("Preview output (publish)", credential.source.preview_path),
            ):
                parts = urlsplit(f"{base.rstrip('/')}/{stream_path}")
                netloc = f"{quote(username, safe='')}:{quote(secret, safe='')}@{parts.netloc}"
                examples.append({"label": label, "url": urlunsplit(parts._replace(netloc=netloc))})
        return examples

    if base := settings.FK_MEDIAMTX_SRT_URL:
        query = urlencode(
            {"streamid": f"{credential.action}:{path}:{username}:{secret}", "pkt_size": 1316},
            safe="/:",
        )
        examples.append({"label": "SRT", "url": f"{base.rstrip('/')}?{query}"})

    if base := settings.FK_MEDIAMTX_RTMP_URL:
        query = urlencode({"user": username, "pass": secret})
        examples.append({"label": "RTMP", "url": f"{base.rstrip('/')}/{path}?{query}"})

    if base := settings.FK_MEDIAMTX_RTSP_URL:
        parts = urlsplit(base.rstrip("/") + "/" + path)
        netloc = f"{quote(username, safe='')}:{quote(secret, safe='')}@{parts.netloc}"
        examples.append({"label": "RTSP", "url": urlunsplit(parts._replace(netloc=netloc))})

    # HTTP-based protocols use an Authorization header, not credentials in
    # the URL. WHIP/WHEP clients such as OBS accept user:password as a token.
    if base := settings.FK_MEDIAMTX_WEBRTC_URL:
        publishing = credential.action == StreamCredential.Action.PUBLISH
        examples.append(
            {
                "label": "WebRTC / WHIP" if publishing else "WebRTC / WHEP",
                "url": f"{base.rstrip('/')}/{path}/{'whip' if publishing else 'whep'}",
                "token": f"{username}:{secret}",
            }
        )
        if not publishing:
            examples.append(
                {
                    "label": "WebRTC / Opus preview",
                    "url": f"{base.rstrip('/')}/{credential.source.preview_path}/whep",
                    "token": f"{username}:{secret}",
                    "note": "Requires a configured preview conversion worker for this source.",
                }
            )

    if credential.action == StreamCredential.Action.READ and (base := settings.FK_MEDIAMTX_HLS_URL):
        examples.append(
            {
                "label": "HLS",
                "url": f"{base.rstrip('/')}/{path}/index.m3u8",
                "note": "Use the username and password above when prompted by your player.",
            }
        )

    return examples
