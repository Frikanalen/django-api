# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
"""Filling leftover airtime with fillers.

Runs after the weekly slots are placed (see :mod:`agenda.scheduling.weekly_slots`)
and fills whatever airtime is still empty, working minute-aligned around the
programming that is already scheduled.
"""

import datetime
import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from django.utils import timezone

from agenda.scheduling.policy import scheduling_horizon
from agenda.scheduling.selection import ScheduleContext, WeightedSelector, default_rules
from fk.models import Scheduleitem, Video

logger = logging.getLogger(__name__)

# The smallest gap the jukebox will try to fill. The boundary is
# exclusive: a gap of exactly this length is left empty.
MINIMUM_GAP = datetime.timedelta(seconds=300)


def fill_agenda_with_jukebox(start=None, days=None, rng=None):
    start = start or timezone.now()
    if days is None:
        # The production default: draft through the end of the open
        # broadcast week, so every week is complete before it opens for
        # member picks (see agenda.scheduling.policy).
        end = scheduling_horizon(now=start)
    else:
        end = start + datetime.timedelta(days=days)

    # A filler must have a length that actually advances the schedule
    # clock; the planner would otherwise place a zero-length video once
    # a minute for the whole window.  Video.duration defaults to zero,
    # so this is ordinary unimported data, not corruption -- negative
    # lengths are barred by a check constraint on the model.  Kept out
    # of Video.objects.fillers() on purpose: that queryset also feeds
    # the jukebox CSV, whose output is a frozen contract.
    candidates = list(Video.objects.fillers().exclude(duration__lte=datetime.timedelta(0)))

    # The context seeds from everything already on the air in the
    # window -- weekly-slot programming included -- so an organization
    # the slots favor starts the day with its filler weight down.
    context = ScheduleContext.from_schedule(start, end)
    selector = WeightedSelector(candidates, context, default_rules(now=start), rng=rng)

    placements = items_for_gap(start, end, candidates, selector=selector)
    for placement in placements:
        item = Scheduleitem(
            video=placement.video,
            schedulereason=Scheduleitem.REASON_JUKEBOX,
            starttime=placement.starttime,
            duration=placement.video.duration,
        )
        item.save()

    return placements


def next_whole_minute(dt):
    """The whole minute strictly after `dt` -- even if `dt` is one already.

    Deliberately not a true ceiling: filling starts on the minute *after*
    the window opens, and the tests pin that a 12:00:00 start places its
    first item at 12:01.
    """
    return floor_minute(dt) + datetime.timedelta(minutes=1)


def floor_minute(dt):
    """Returns the datetime with seconds and microseconds cleared"""
    return dt.replace(second=0, microsecond=0)


@dataclass(frozen=True)
class Placement:
    """One planned (not yet saved) filler: this video at this time."""

    video: Video
    starttime: datetime.datetime


@dataclass(frozen=True)
class Gap:
    """A free, minute-aligned stretch of airtime."""

    start: datetime.datetime
    end: datetime.datetime

    @property
    def duration(self) -> datetime.timedelta:
        return self.end - self.start


def free_gaps(
    start: datetime.datetime,
    end: datetime.datetime,
    occupied: Iterable[tuple[datetime.datetime, datetime.datetime]],
) -> Iterator[Gap]:
    """Yield the usable free intervals inside [start, end].

    `occupied` is (starttime, endtime) pairs of airtime already spoken
    for, sorted by starttime. Every boundary is a whole-minute rule:
    filling starts on the whole minute after `start`, a gap ends on the
    last whole minute before an occupied stretch and resumes on the whole
    minute after it, and a gap of exactly MINIMUM_GAP is too short to use.
    """
    start_of_gap = next_whole_minute(start)
    end = floor_minute(end)
    pending = list(occupied)

    while True:
        end_of_gap = end
        resume_at = None
        while pending:
            occupied_start, occupied_end = pending.pop(0)
            # Already behind us; an item ending exactly at start_of_gap
            # still bounds the (then empty) gap, so the comparison is strict.
            if occupied_end < start_of_gap:
                continue
            if occupied_start > end:
                # Beyond the window, as is everything after it.
                break
            end_of_gap = floor_minute(occupied_start)
            resume_at = next_whole_minute(occupied_end)
            break

        gap = Gap(start_of_gap, end_of_gap)
        if gap.duration > MINIMUM_GAP:
            yield gap
        else:
            logger.info("Not filling %d second gap", gap.duration.total_seconds())

        if resume_at is None or end_of_gap >= end:
            return
        start_of_gap = resume_at


def items_for_gap(start, end, candidates, selector=None):
    """Plan (but do not save) filler placements between `start` and `end`.

    Returns a list of `Placement`s, skipping any stretch already occupied
    by an existing Scheduleitem. Videos are chosen by `selector`;
    without one, a RoundRobinSelector cycles `candidates` in the order
    given.
    """
    logger.info("Being asked to fill gap from %s to %s", start, end)

    # overlapping() is the one definition of occupied airtime, and it
    # includes programming that starts before the window but overruns
    # into it.
    occupied = [
        (item.starttime, item.endtime())
        for item in Scheduleitem.objects.overlapping(start, end).order_by("starttime")
    ]

    if selector is None:
        selector = RoundRobinSelector(candidates)
    placements = []
    for gap in free_gaps(start, end, occupied):
        placements.extend(_fill_gap(gap, selector))
    return placements


def _fill_gap(gap: Gap, selector) -> list[Placement]:
    """Pack fillers into one gap, advancing minute-aligned after each."""
    logger.info("Filling jukebox from %s to %s", gap.start, gap.end)
    placements = []
    current_time = gap.start
    while current_time < gap.end:
        video = selector.pick(gap.end - current_time)
        if video is None:
            # Nothing eligible fits; leave the rest of the gap empty.
            logger.info("No videos available to fill from %s", current_time)
            break
        placements.append(Placement(video=video, starttime=current_time))
        logger.info("Added video %s at curr time %s", video.id, current_time.strftime("%H:%M:%S"))
        current_time = next_whole_minute(current_time + video.duration)
    return placements


class RoundRobinSelector:
    """Draws videos in the order given, cycling through the whole list.

    No video is drawn again until the rest of the list has had its turn.
    A video too long for the remaining time keeps its place in line and
    is retried first at the next pick; the caller supplies the order
    (shuffled in production, fixed in tests).
    """

    def __init__(self, candidates: Sequence[Video]):
        self._candidates = list(candidates)
        self._pool = list(self._candidates)
        self._deferred = []

    def pick(self, remaining: datetime.timedelta) -> Video | None:
        """The next video no longer than `remaining`, or None if none fit."""
        # Top up before the first draw, so that the pool running dry
        # mid-pick (everything left too long) ends this pick instead of
        # retrying the same videos forever.
        if len(self._pool) < len(self._candidates):
            self._pool.extend(self._candidates)
        skipped = []
        while True:
            video = self._draw()
            if video is None:
                # The too-long videos stay skipped rather than rejoining
                # the deferred queue: they must not jump the line at a
                # pick they already failed.
                return None
            if video.duration <= remaining:
                self._deferred.extend(skipped)
                return video
            if video not in self._deferred and video not in skipped:
                skipped.append(video)

    def _draw(self) -> Video | None:
        if self._deferred:
            return self._deferred.pop(0)
        if self._pool:
            return self._pool.pop(0)
        return None
