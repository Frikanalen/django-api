"""Stortinget's live speaker list, as the keeper reads it.

https://data.stortinget.no/eksport/talerliste (no parameters) describes the
sitting in the chamber now, or the next one when none is on. Stortinget's
docs say it reads its database about once a second. What the keeper needs:

- ``mote_id``: which sitting. It jumps straight to the next sitting's id
  when one ends (seen 17 Nov 2026, 15:10:46), or reads 0 between sittings.
- ``mote_aktivitet_status``: ``ikke_startet``, ``aktiv`` or ``pause`` per
  the docs; ``votering`` during votes and ``ikke_spesifisert`` between
  sittings also occur, though undocumented.
- ``mote_start_dato_tid``: when the sitting starts or started.
- ``mote_neste_start_dato_tid``: when a long pause ends, if announced.

Times are Oslo wall-clock. Roughly every hour they come with a trailing
"Z" for a while, with the same digits, so the "Z" is ignored.
"""

from __future__ import annotations

import datetime
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from zoneinfo import ZoneInfo

URL = "https://data.stortinget.no/eksport/talerliste"
NS = "{http://data.stortinget.no}"
OSLO = ZoneInfo("Europe/Oslo")
TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class Snapshot:
    """One reading of the speaker list."""

    at: datetime.datetime
    mote_id: int
    status: str
    starts_at: datetime.datetime | None
    resumes_at: datetime.datetime | None


def _time(text: str | None) -> datetime.datetime | None:
    if not text or text.startswith("0001-01-01"):
        return None
    text = text.rstrip("Z").split(".")[0]
    return datetime.datetime.fromisoformat(text).replace(tzinfo=OSLO)


def parse(body: bytes, at: datetime.datetime) -> Snapshot:
    root = ET.fromstring(body)

    def text(name: str) -> str | None:
        element = root.find(NS + name)
        return element.text.strip() if element is not None and element.text else None

    return Snapshot(
        at=at,
        mote_id=int(text("mote_id") or 0),
        status=text("mote_aktivitet_status") or "",
        starts_at=_time(text("mote_start_dato_tid")),
        resumes_at=_time(text("mote_neste_start_dato_tid")),
    )


def fetch(at: datetime.datetime) -> Snapshot:
    request = urllib.request.Request(URL, headers={"User-Agent": "frikanalen-stortinget-keeper"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return parse(response.read(), at)
