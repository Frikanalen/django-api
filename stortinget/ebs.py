"""EbS, the EU's TV service, as the keeper reads it for the European
Commission's midday press briefing.

Two sources, both public and without keys:

- The programme grid (REST): which programmes EbS plans when. The midday
  briefing is listed as "EC Midday press briefing of DD/MM/YYYY" on the
  days it is held (not every weekday), with the planned start; after the
  event the grid is rewritten with the real start and duration.
- The live status (WebSocket): what is on each channel now, with its
  title and the languages it carries. Learned from the briefing of 16 Nov
  2026: the title appeared 2 min 20 s after the start, with languages
  INT/EN/FR; when the briefing ended (11:44:42Z) the languages dropped to
  INT only, under the same title. The reference field flaps between ids
  every half minute and goes empty mid-programme, so it is not used.

Rights: © European Union, CC BY 4.0.
"""

from __future__ import annotations

import datetime
import html
import json
import re
import urllib.request
from dataclasses import dataclass

from websockets.sync.client import connect

GRID_URL = "https://8hwk2cyeyb.execute-api.eu-west-1.amazonaws.com/parrotfish-prod/ebs/grid"
LIVE_URL = "wss://08njmv7gbf.execute-api.eu-west-1.amazonaws.com/production"
# EbS (not EbS+, 1136) in the live status.
CHANNEL = 1134
TIMEOUT_SECONDS = 15
USER_AGENT = "frikanalen-stortinget-keeper"

BRIEFING = re.compile(r"^EC Midday press briefing of (\d\d)/(\d\d)/(\d{4})$")
_TAG = re.compile(r"<[^>]*>")


def plain(text: str | None) -> str:
    """A grid or live title as plain text (they come as HTML, "<p>…</p>")."""
    return " ".join(_TAG.sub(" ", html.unescape(text or "")).split())


def briefing_day(title: str | None) -> datetime.date | None:
    """The date of the midday briefing `title` names, if it names one."""
    match = BRIEFING.match(plain(title))
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def _instant(text: str | None) -> datetime.datetime | None:
    if not text:
        return None
    return datetime.datetime.fromisoformat(text)


@dataclass(frozen=True)
class Programme:
    """One programme in the grid."""

    title: str
    start: datetime.datetime
    # Zero while it is only planned; the real length once it has been.
    duration: datetime.timedelta


@dataclass(frozen=True)
class OnAir:
    """What EbS shows now, per the live status."""

    at: datetime.datetime
    # None when the status names nothing (between programmes, and in
    # short gaps in the middle of one).
    title: str | None
    languages: frozenset[str]


def parse_grid(body: bytes) -> list[Programme]:
    programmes = []
    for day in json.loads(body):
        for item in day.get("programs") or []:
            start = _instant(item.get("startDatetime"))
            titles = [plain(t.get("content")) for t in item.get("titles") or []]
            if start is None or not titles:
                continue
            title = next((t for t in titles if BRIEFING.match(t)), titles[0])
            duration = datetime.timedelta(seconds=float(item.get("duration") or 0))
            programmes.append(Programme(title, start, duration))
    return programmes


def find_briefing(programmes: list[Programme], day: datetime.date) -> Programme | None:
    """The midday briefing of `day`, if the grid lists one."""
    return next((p for p in programmes if briefing_day(p.title) == day), None)


def parse_live(body: str | bytes, at: datetime.datetime) -> OnAir | None:
    """EbS's entry in one live status reply; None if the reply has none."""
    entries = json.loads(body)
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("channelId") == CHANNEL:
            title = plain(entry.get("title")) or None
            return OnAir(at, title, frozenset(entry.get("languages") or []))
    return None


def fetch_grid(day: datetime.date) -> list[Programme]:
    stamp = day.strftime("%Y%m%d")
    url = f"{GRID_URL}?channelName=EBS&dateFrom={stamp}&dateTo={stamp}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return parse_grid(response.read())


def fetch_live(at: datetime.datetime) -> OnAir | None:
    """Ask the live status once. The service answers a poll with one reply
    covering all channels."""
    with connect(
        LIVE_URL,
        open_timeout=TIMEOUT_SECONDS,
        close_timeout=2,
        user_agent_header=USER_AGENT,
    ) as socket:
        socket.send(json.dumps({"action": "polling", "data": ""}))
        for _ in range(3):
            on_air = parse_live(socket.recv(timeout=TIMEOUT_SECONDS), at)
            if on_air is not None:
                return on_air
    return None
