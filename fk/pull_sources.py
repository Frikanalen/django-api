"""Pull URLs: where MediaMTX fetches a pulled live source from.

The whole URL is a secret: SRT passphrases, RTMP stream keys and HLS tokens
all ride inside it. It is stored encrypted with a key per environment (from
OpenBao), so a database copied from production to staging, or a backup, is
of no use without production's key. Only MediaMTX's relay ever gets it back
in the clear; members see the redacted target.
"""

import socket
from ipaddress import ip_address
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError

# What the relay's FFmpeg may open. HLS only over HTTPS; no plain HTTP, file
# or UDP, which would reach into the cluster or the host.
SCHEMES = {
    "srt": 9000,
    "rtmp": 1935,
    "rtmps": 443,
    "rtsp": 554,
    "rtsps": 322,
    "https": 443,
}

MAX_LENGTH = 2048


def _fernet() -> MultiFernet:
    keys = [k for k in settings.FK_PULL_URL_KEYS if k]
    if not keys:
        raise ImproperlyConfigured("FK_PULL_URL_KEYS is not set")
    # The first key encrypts; the others still decrypt, for rotation.
    return MultiFernet([Fernet(k) for k in keys])


def is_configured() -> bool:
    return any(settings.FK_PULL_URL_KEYS)


def encrypt(url: str) -> str:
    return _fernet().encrypt(url.encode()).decode()


def decrypt(token: str) -> str | None:
    """The URL, or None when this environment's key cannot open it."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ImproperlyConfigured):
        return None


def redact(url: str) -> str:
    """Scheme, host and port only: what a member may be shown again."""
    parts = urlsplit(url)
    port = parts.port or SCHEMES.get(parts.scheme)
    return (
        f"{parts.scheme}://{parts.hostname}:{port}"
        if port
        else f"{parts.scheme}://{parts.hostname}"
    )


def _resolve(host: str, port: int) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]


def validate(url: str) -> None:
    """Refuse a URL the relay must not open: wrong scheme, or not on the internet.

    The cluster's NetworkPolicy is the real guard (it also catches a name that
    resolves differently later); this gives the member a clear error at once.
    """
    if len(url) > MAX_LENGTH:
        raise ValidationError(f"The address is longer than {MAX_LENGTH} characters.")
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise ValidationError("The address must not contain spaces or control characters.")
    try:
        parts = urlsplit(url)
        port = parts.port or SCHEMES.get(parts.scheme)
    except ValueError as error:
        raise ValidationError("That is not a valid address.") from error
    if parts.scheme not in SCHEMES:
        raise ValidationError(
            "The address must start with " + ", ".join(f"{s}://" for s in SCHEMES) + "."
        )
    if not parts.hostname:
        raise ValidationError("The address has no host name.")
    try:
        addresses = _resolve(parts.hostname, port or 0)
    except (OSError, UnicodeError) as error:
        raise ValidationError(f"Cannot find the host {parts.hostname}.") from error
    if not addresses or not all(ip_address(a.split("%")[0]).is_global for a in addresses):
        raise ValidationError(
            f"{parts.hostname} is not a public internet address; Frikanalen pulls only from those."
        )
