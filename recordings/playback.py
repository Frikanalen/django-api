"""Access to a recording on MediaMTX's playback server.

Ingest fetches a request's window from the playback server. Rather than a
standing credential, Django signs a short-lived token for the one path and
hands ingest a URL that carries it; MediaMTX asks Django's authentication
callback, which recognizes the token again (api.mediamtx).
"""

import datetime
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core import signing

SALT = "recordings.playback"
USER = "recording"
TOKEN_TTL = datetime.timedelta(hours=2)


def issue_token(path: str) -> str:
    return signing.dumps(path, salt=SALT)


def verify_token(token: str, path: str) -> bool:
    try:
        return signing.loads(token, salt=SALT, max_age=TOKEN_TTL) == path
    except signing.BadSignature:
        return False


def url(request) -> str:
    """Where ingest fetches exactly `request`'s window, as one MP4."""
    base = urlsplit(settings.FK_MEDIAMTX_PLAYBACK_URL.rstrip("/"))
    netloc = f"{USER}:{quote(issue_token(request.path), safe='')}@{base.netloc}"
    query = urlencode(
        {
            "path": request.path,
            "start": request.starttime.isoformat(),
            "duration": f"{(request.endtime - request.starttime).total_seconds():g}",
            "format": "mp4",
        }
    )
    return urlunsplit((base.scheme, netloc, f"{base.path}/get", query, ""))
