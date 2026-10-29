"""Connection examples for the one-time credential response, never persisted."""

from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from django.conf import settings

from fk.models import StreamCredential


def connection_examples(credential, secret):
    path = f"live/{credential.source.stream_id}"
    username = str(credential.key_id)
    examples = []

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

    if credential.action == StreamCredential.Action.READ and (base := settings.FK_MEDIAMTX_HLS_URL):
        examples.append(
            {
                "label": "HLS",
                "url": f"{base.rstrip('/')}/{path}/index.m3u8",
                "note": "Use the username and password above when prompted by your player.",
            }
        )

    return examples
