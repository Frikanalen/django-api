"""Short-lived WebRTC preview tokens for an organization's own live sources.

MediaMTX never sees the site's session cookie, so a logged-in member's
browser instead presents one of these as a WHEP bearer token. It is signed
rather than stored: nothing to revoke or clean up, and the authentication
callback rechecks the member's access every time one is used.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import signing

from fk.models import LiveSource

SALT = "fk.preview-token"
# Long enough to cover the WHEP handshake and a few retries; the page asks
# for a new one whenever it reconnects. MediaMTX checks it only when a
# session starts, so expiry does not cut off a preview already playing.
TOKEN_TTL = timedelta(minutes=5)


def issue(source, user):
    return signing.dumps({"source": str(source.stream_id), "user": user.pk}, salt=SALT)


def verify(token):
    """Return the (source, user) a valid token names, or None."""
    try:
        claims = signing.loads(token, salt=SALT, max_age=TOKEN_TTL)
    except signing.BadSignature:
        return None
    if not isinstance(claims, dict):
        return None
    source = (
        LiveSource.objects.select_related("organization")
        .filter(stream_id=claims.get("source"), enabled=True)
        .first()
    )
    user = get_user_model().objects.filter(pk=claims.get("user"), is_active=True).first()
    if source is None or user is None:
        return None
    return source, user
