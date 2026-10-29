# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
"""Placing one video per WeeklySlot occurrence.

Runs before the jukebox (see :mod:`agenda.scheduling.jukebox`) and, like
it, drafts through the end of the open broadcast week (see
:mod:`agenda.scheduling.policy`). Airtime taken by anything but jukebox
fillers is respected; jukebox fillers are displaced outside the freeze
boundary, so a newly defined slot does not wait two weeks for airtime
the jukebox got to first.

Placements carry provenance (Scheduleitem.weekly_slot), and the open
week is a draft: each nightly run re-picks its own unfrozen placements
when the source's answer has changed, so a `latest` slot drafted
nearly three weeks out does not go stale. Frozen weeks are never
touched.
"""

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from agenda.scheduling.policy import (
    airtime_conflicts,
    displace,
    freeze_boundary,
    scheduling_horizon,
)
from fk.models import Scheduleitem, Video, WeeklySlot, WeeklySlotSource

logger = logging.getLogger(__name__)


def fill_next_weeks_agenda(now: datetime | None = None) -> None:
    now = now or timezone.now()
    horizon = scheduling_horizon(now)
    frozen_until = freeze_boundary(now)

    slots = WeeklySlot.objects.all()
    if len(slots) == 0:
        logger.warning("No WeeklySlots defined; exiting")
        return

    for slot in slots:
        source = slot.source
        if source is None:
            logger.info("No source connected, so nothing to fill")
            continue
        for starttime in _occurrences(slot, now, horizon):
            _fill_occurrence(slot, source, starttime, frozen_until)


def _occurrences(slot: WeeklySlot, now: datetime, horizon: datetime) -> Iterator[datetime]:
    """Every time `slot` comes up between `now` and `horizon`."""
    day = slot.next_date(timezone.localtime(now).date())
    if slot.next_datetime(from_date=day) <= now:
        # Today is the slot's weekday, but its start time has passed.
        day += timedelta(days=7)
    while True:
        occurrence = slot.next_datetime(from_date=day)
        if occurrence >= horizon:
            return
        yield occurrence
        day += timedelta(days=7)


def _fill_occurrence(
    slot: WeeklySlot,
    source: WeeklySlotSource,
    starttime: datetime,
    frozen_until: datetime,
) -> None:
    # Chosen per occurrence, so the least_scheduled strategy sees each
    # placement it just made.
    video = source.single_video(slot.duration)
    if not video:
        logger.info("Couldn't get a video to use in slot!")
        return
    end = starttime + slot.duration

    with transaction.atomic():
        blocking, displaceable = airtime_conflicts(starttime, end, for_update=True)
        # The slot's own earlier placement is not a conflict but a draft
        # this run may refresh. Anything else deliberate -- member
        # picks, admin entries, other slots, pre-provenance rows --
        # keeps the airtime.
        own = [item for item in blocking if item.weekly_slot_id == slot.pk]
        foreign = [item for item in blocking if item.weekly_slot_id != slot.pk]
        if foreign:
            # Note this includes an item that started *before* the slot
            # and runs into it.
            logger.info("Already something scheduled across %s; skipping slot", starttime)
            return

        if starttime < frozen_until:
            # Frozen weeks change as little as possible: only genuinely
            # empty airtime may still be filled.
            if own or displaceable:
                logger.info("Not touching the frozen weeks at %s", starttime)
                return
        elif own:
            _refresh_own_placement(slot, source, own[0], video, displaceable)
            return

        displace(displaceable)
        Scheduleitem.objects.create(
            video=video,
            schedulereason=Scheduleitem.REASON_AUTO,
            starttime=starttime,
            duration=video.duration,
            weekly_slot=slot,
        )


def _refresh_own_placement(
    slot: WeeklySlot,
    source: WeeklySlotSource,
    placement: Scheduleitem,
    video: Video,
    displaceable: list[Scheduleitem],
) -> None:
    """Re-pick an unfrozen draft placement its source no longer stands
    by: a newer upload under `latest`, or a drafted video that has
    become ineligible. The open week is a draft, but a standing pick is
    left alone -- no churn night to night."""
    if source.still_current(placement.video, slot.duration):
        return
    logger.info(
        "Re-picking slot placement at %s: %s replaces %s",
        placement.starttime,
        video.id,
        placement.video_id,
    )
    # A longer replacement may reach jukebox fillers that packed in
    # behind a shorter draft; they give way like anywhere else.
    displace(displaceable)
    placement.video = video
    placement.duration = video.duration
    placement.save()
