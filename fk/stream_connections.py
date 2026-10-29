"""Connection examples for the one-time credential response, never persisted."""

from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from django.conf import settings

from fk.models import StreamCredential


def connection_examples(credential, secret):
    path = credential.source.contribution_path
    username = str(credential.key_id)
    examples = []

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
