"""Choosing which filler to play: scoring rules and the weighted draw.

The jukebox planner (see :mod:`agenda.scheduling.jukebox`) asks a
selector one question, repeatedly: given the time remaining in this
gap, which video next?  This module answers with weighted randomness:
every rule multiplies a candidate's weight, and the draw is
proportional to the product -- preference, not decree, so material a
rule frowns on still eventually airs.

A rule is anything with ``weight(video, context) -> float`` returning a
non-negative multiplier, where 1.0 is indifference and 0.0 a veto.
"""

import datetime
import logging
import random
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Protocol

from fk.models import Scheduleitem, Video

logger = logging.getLogger(__name__)


class Rule(Protocol):
    """A scoring rule: a non-negative multiplier on a candidate's weight."""

    def weight(self, video: Video, context: "ScheduleContext") -> float: ...


class Selector(Protocol):
    """What the jukebox planner asks for the next filler.

    Both selectors here satisfy it: the weighted draw below and
    :class:`agenda.scheduling.jukebox.RoundRobinSelector`.
    """

    def pick(self, remaining: datetime.timedelta) -> Video | None: ...


class ScheduleContext:
    """What is already on the air in the window, kept current as picks land.

    Built from every Scheduleitem overlapping the window -- weekly-slot
    programming included, which is what lets OrganizationDiversity
    downplay an organization the slots already favor.
    """

    def __init__(self) -> None:
        self.org_airtime: defaultdict[int, datetime.timedelta] = defaultdict(datetime.timedelta)
        self.total_airtime = datetime.timedelta(0)
        self.times_played: Counter[int] = Counter()
        self.last_video_id: int | None = None

    @classmethod
    def from_schedule(cls, start: datetime.datetime, end: datetime.datetime) -> "ScheduleContext":
        context = cls()
        for item in Scheduleitem.objects.overlapping(start, end).select_related("video"):
            context._count(item.video, item.duration)
        return context

    def record(self, video: Video) -> None:
        """Note a pick the selector just made, so later weights see it."""
        self._count(video, video.duration)
        self.last_video_id = video.id

    def _count(self, video: Video | None, duration: datetime.timedelta) -> None:
        # An edge item's airtime counts in full even where it leaves the
        # window; shares are a steering signal, not bookkeeping.
        self.total_airtime += duration
        if video is None:
            return
        self.times_played[video.id] += 1
        if video.organization_id is not None:
            self.org_airtime[video.organization_id] += duration

    def organization_share(self, organization_id: int | None) -> float:
        """The organization's fraction of all airtime counted so far."""
        if organization_id is None or not self.total_airtime:
            return 0.0
        return self.org_airtime[organization_id] / self.total_airtime


class Freshness:
    """Newer uploads weigh more, to keep the airwaves fresh.

    Half the weight per `half_life` of age, levelling off at `floor` so
    ancient material still airs. A video with no upload time recorded is
    treated as ancient.
    """

    def __init__(
        self,
        now: datetime.datetime,
        half_life: datetime.timedelta = datetime.timedelta(days=365),
        floor: float = 0.2,
    ) -> None:
        self.now = now
        self.half_life = half_life
        self.floor = floor

    def weight(self, video: Video, context: ScheduleContext) -> float:
        if video.uploaded_time is None:
            return self.floor
        age = self.now - video.uploaded_time
        if age <= datetime.timedelta(0):
            return 1.0
        return self.floor + (1.0 - self.floor) * 0.5 ** (age / self.half_life)


class OrganizationDiversity:
    """No one organization should dominate a day's schedule.

    Weight is (1 - share) ** strength, so the more of the window's
    airtime an organization already holds -- weekly slots included --
    the less its videos weigh. The floor keeps this a preference, never
    a veto: when one organization owns the whole window (a share of
    1.0), zeroing it would zero every candidate alike and knock out
    RepeatAvoidance via the selector's uniform fallback.
    """

    def __init__(self, strength: float = 1.0, floor: float = 0.05) -> None:
        self.strength = strength
        self.floor = floor

    def weight(self, video: Video, context: ScheduleContext) -> float:
        share = context.organization_share(video.organization_id)
        return max((1.0 - share) ** self.strength, self.floor)


class RepeatAvoidance:
    """Never the same video twice in a row; beyond that, each earlier
    play in the window halves the weight."""

    def __init__(self, penalty: float = 0.5) -> None:
        self.penalty = penalty

    def weight(self, video: Video, context: ScheduleContext) -> float:
        if video.id == context.last_video_id:
            return 0.0
        return self.penalty ** context.times_played[video.id]


def default_rules(now: datetime.datetime) -> list[Rule]:
    return [Freshness(now=now), OrganizationDiversity(), RepeatAvoidance()]


class WeightedSelector:
    """Draws the next filler at random, in proportion to the product of
    every rule's weight.

    If every fitting candidate weighs zero -- say the only video short
    enough just played -- the draw falls back to uniform: dead air is
    worse than repetition.
    """

    def __init__(
        self,
        candidates: Sequence[Video],
        context: ScheduleContext,
        rules: Sequence[Rule],
        rng: random.Random | None = None,
    ) -> None:
        # A non-positive duration cannot advance the schedule clock, so
        # it would be drawn once a minute for the whole window.
        self._candidates = [v for v in candidates if v.duration > datetime.timedelta(0)]
        self._context = context
        self._rules = list(rules)
        self._rng = rng or random

    def pick(self, remaining: datetime.timedelta) -> Video | None:
        """The next video no longer than `remaining`, or None if none fit."""
        fitting = [v for v in self._candidates if v.duration <= remaining]
        if not fitting:
            return None
        scores = [self._weight(video) for video in fitting]
        weights = scores if any(scores) else None  # None draws uniformly
        video = self._rng.choices(fitting, weights=weights, k=1)[0]
        self._context.record(video)
        return video

    def _weight(self, video: Video) -> float:
        weight = 1.0
        for rule in self._rules:
            weight *= rule.weight(video, self._context)
        return weight
