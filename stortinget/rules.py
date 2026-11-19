"""What the speaker list says about a sitting, without touching the schedule.

Learned from the sitting of 17 Nov 2026 (meeting 11685):

- 13:12:19 ``pause`` with no resume time: the debate was over and the hall
  stayed empty for 1 h 42 min, until the votes.
- 14:54 ``aktiv``, then ``votering``: the votes.
- 15:10:46 the meeting id jumped to the next sitting (``ikke_startet``):
  the sitting was over.

The feed also blips: one poll with meeting id 0 in the middle of the night,
for instance. So a phase only counts once it has held for :data:`HOLD`.
"""

from __future__ import annotations

import datetime
import enum
from dataclasses import dataclass, field

from .ebs import OnAir, briefing_day
from .feed import Snapshot

# How long a phase must hold before the keeper acts on it.
HOLD = datetime.timedelta(seconds=60)
# A pause that resumes sooner than this is not worth leaving the sitting for.
SHORT_PAUSE = datetime.timedelta(minutes=30)


class Phase(enum.Enum):
    NOT_STARTED = "not started"
    LIVE = "live"
    # The hall is empty for a while: a pause with no resume time, or one
    # that resumes at least SHORT_PAUSE away.
    BREAK = "break"
    ENDED = "ended"


def phase_of(snapshot: Snapshot, mote_id: int) -> Phase:
    """The phase of sitting `mote_id`, as `snapshot` shows it."""
    if snapshot.mote_id != mote_id:
        return Phase.ENDED
    if snapshot.status == "ikke_startet":
        return Phase.NOT_STARTED
    if snapshot.status == "pause":
        resumes = snapshot.resumes_at
        if resumes is None or resumes - snapshot.at >= SHORT_PAUSE:
            return Phase.BREAK
    # aktiv, votering, a short pause, or anything Stortinget adds later:
    # while the sitting is on, it stays on air.
    return Phase.LIVE


@dataclass
class Debouncer:
    """The phase that has held for HOLD; None until one has."""

    hold: datetime.timedelta = HOLD
    _candidate: Phase | None = None
    _since: datetime.datetime | None = None
    settled: Phase | None = field(default=None)

    def feed(self, at: datetime.datetime, phase: Phase) -> Phase | None:
        if phase != self._candidate:
            self._candidate, self._since = phase, at
        if self._since is not None and at - self._since >= self.hold:
            self.settled = phase
        return self.settled


# -- The EU midday briefing ---------------------------------------------------

# EbS's languages blip too: on 16 Nov 2026 a press point read INT only for
# 41 s in its middle. So the briefing's end must hold a little longer.
BRIEFING_HOLD = datetime.timedelta(seconds=90)


def briefing_phase(on_air: OnAir, day: datetime.date, *, seen_live: bool) -> Phase | None:
    """The phase of `day`'s midday briefing, as EbS's live status shows it;
    None when the status says nothing either way.

    Live while EbS shows the briefing with interpretation (INT plus EN, FR
    ...). Over once it shows the briefing with INT only (seen 16 Nov 2026,
    to the second), or another programme, but only after it has been live:
    before the briefing, EbS shows whatever comes first."""
    if on_air.title is None:
        return None
    if briefing_day(on_air.title) == day and on_air.languages - {"INT"}:
        return Phase.LIVE
    return Phase.ENDED if seen_live else Phase.NOT_STARTED
