"""Where each entry in a grant's list plays.

Programmes (videos) start on the five-minute grid, with at least a short
breather after the programme before them, so a viewer gets a moment of
graphics between programmes and a time worth printing in the schedule.
Posters are not programmes: one plays right after whatever is before it,
so a member can put it in the gap a programme leaves, and the next
programme still starts on the grid after it.

The first entry starts with the grant. Jukebox grants are not padded:
they are a stream of short fillers, not a schedule of programmes.

The frontend lays a list out the same way (src/app/grants/grants.ts);
keep the two in step.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# The grid programmes start on.
GRID = timedelta(minutes=5)
# The least time between the end of a programme and the start of the next.
BREATHER = timedelta(seconds=30)

_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Placed:
    start: datetime
    end: datetime


def _up_to_grid(instant: datetime) -> datetime:
    """The first grid point at or after `instant`. Oslo is a whole number
    of hours off UTC, so the UTC grid is Oslo's grid too."""
    offset = (instant - _EPOCH) % GRID
    return instant if not offset else instant + (GRID - offset)


def is_programme(entry) -> bool:
    from scheduling.models import Poster

    return not isinstance(entry, Poster)


def next_start(cursor: datetime, previous, entry, *, padded: bool) -> datetime:
    """Where `entry` starts, when what came before it (`previous`, or
    None at the head of the list) ended at `cursor`."""
    if not padded or previous is None or not is_programme(entry):
        return cursor
    earliest = cursor + BREATHER if is_programme(previous) else cursor
    return _up_to_grid(earliest)


def lay_out(start: datetime, entries: Sequence, *, padded: bool) -> list[Placed]:
    """When each of `entries` (videos and posters, anything with a
    `duration`) plays, from `start`."""
    placed = []
    cursor = start
    previous = None
    for entry in entries:
        begin = next_start(cursor, previous, entry, padded=padded)
        cursor = begin + entry.duration
        placed.append(Placed(begin, cursor))
        previous = entry
    return placed


def end_of(start: datetime, entries: Sequence, *, padded: bool) -> datetime:
    """When the last of `entries` ends; `start` for an empty list."""
    placed = lay_out(start, entries, padded=padded)
    return placed[-1].end if placed else start
