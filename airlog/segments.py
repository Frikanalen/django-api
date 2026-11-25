"""The air log's segments, read straight from the recorder's folder.

live-stream-packager's airlog recorder writes self-contained MPEG-TS
segments named by their UTC start time,

    <FK_AIRLOG_DIR>/YYYY-MM-DD/HH/YYYYMMDDTHHMMSSZ.ts

and its janitor deletes them after a week. A segment only gets that name once
it is complete. The folder is the whole record: there is no index to keep in
step with it, so whatever is on disk is what can be played.
"""

import datetime
import math
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

UTC = datetime.UTC
_NAME = re.compile(r"^(\d{8}T\d{6})Z\.ts$")
# A segment path as the API names it, relative to FK_AIRLOG_DIR.
SEGMENT_PATH = re.compile(r"^(\d{4}-\d{2}-\d{2})/(\d{2})/(\d{8}T\d{6}Z\.ts)$")


@dataclass(frozen=True)
class Segment:
    start: datetime.datetime
    path: str  # relative to FK_AIRLOG_DIR
    # Up to the next segment's start, or the nominal length when the next one
    # is missing (a gap, or the newest segment).
    duration: float
    # A gap (the recorder was down) or a recorder restart comes before it.
    discontinuity: bool

    @property
    def end(self) -> datetime.datetime:
        return self.start + datetime.timedelta(seconds=self.duration)


def root() -> Path | None:
    return Path(settings.FK_AIRLOG_DIR) if settings.FK_AIRLOG_DIR else None


def segment_seconds() -> float:
    return float(settings.FK_AIRLOG_SEGMENT_SECONDS)


def _hours(start: datetime.datetime, end: datetime.datetime) -> Iterator[datetime.datetime]:
    hour = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    while hour < end:
        yield hour
        hour += datetime.timedelta(hours=1)


def _starts(base: Path, start: datetime.datetime, end: datetime.datetime):
    """(start, relative path) of every segment file in the hours start..end."""
    found = []
    for hour in _hours(start, end):
        folder = f"{hour:%Y-%m-%d}/{hour:%H}"
        try:
            entries = os.scandir(base / folder)
        except FileNotFoundError:
            continue
        with entries:
            for entry in entries:
                match = _NAME.match(entry.name)
                if not match:
                    continue
                begins = datetime.datetime.strptime(match[1], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
                found.append((begins, f"{folder}/{entry.name}"))
    found.sort()
    return found


def segments(start: datetime.datetime, end: datetime.datetime) -> list[Segment]:
    """The segments that overlap start..end, oldest first."""
    base = root()
    if base is None:
        return []
    nominal = segment_seconds()
    slack = datetime.timedelta(seconds=nominal * 2)
    # One segment either side, to see whether the neighbours are continuous.
    found = _starts(base, start - slack, end + slack)
    limit = nominal * 1.5
    result = []
    for i, (begins, path) in enumerate(found):
        following = found[i + 1][0] if i + 1 < len(found) else None
        to_next = (following - begins).total_seconds() if following else None
        duration = to_next if to_next is not None and to_next <= limit else nominal
        from_previous = (begins - found[i - 1][0]).total_seconds() if i else None
        discontinuity = from_previous is None or from_previous > limit
        segment = Segment(begins, path, duration, discontinuity)
        if segment.end > start and begins < end:
            result.append(segment)
    return result


def spans(
    start: datetime.datetime, end: datetime.datetime
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """The stretches of start..end that the air log covers, without gaps."""
    covered: list[tuple[datetime.datetime, datetime.datetime]] = []
    for segment in segments(start, end):
        if covered and not segment.discontinuity:
            covered[-1] = (covered[-1][0], segment.end)
        else:
            covered.append((segment.start, segment.end))
    return [(max(a, start), min(b, end)) for a, b in covered]


def playlist(start: datetime.datetime, end: datetime.datetime, now: datetime.datetime) -> str:
    """An HLS playlist of start..end. While end is still to come it is left
    open, so a player reloads it and follows the recording as it grows."""
    found = segments(start, end)
    target = max([segment_seconds(), *(s.duration for s in found)])
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{math.ceil(target)}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
    ]
    for i, segment in enumerate(found):
        if segment.discontinuity and i > 0:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(
            "#EXT-X-PROGRAM-DATE-TIME:"
            + segment.start.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        lines.append(f"#EXTINF:{segment.duration:.3f},")
        lines.append(f"segments/{segment.path}")
    if end <= now:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def file(path: str) -> Path | None:
    """The file behind a segment path from the API, if it names one."""
    base = root()
    if base is None or not SEGMENT_PATH.fullmatch(path):
        return None
    # The pattern already rules out "..", but check the normalised path stays
    # under the air log folder all the same.
    folder = os.path.realpath(base)
    candidate = os.path.realpath(os.path.join(folder, path))
    if not candidate.startswith(folder + os.sep):
        return None
    return Path(candidate) if os.path.isfile(candidate) else None
