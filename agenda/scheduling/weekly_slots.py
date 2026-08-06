# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
"""Placing one video per WeeklySlot occurrence.

Runs before the jukebox (see :mod:`agenda.scheduling.jukebox`) and, like
it, drafts through the end of the open broadcast week (see
:mod:`agenda.scheduling.policy`). Airtime taken by anything but jukebox
fillers is respected; jukebox fillers are displaced outside the freeze
boundary, so a newly defined slot does not wait two weeks for airtime
the jukebox got to first.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from agenda.scheduling.policy import freeze_boundary, scheduling_horizon
from fk.models import Scheduleitem, WeeklySlot

logger = logging.getLogger(__name__)


def fill_next_weeks_agenda(now=None):
    now = now or timezone.now()
    horizon = scheduling_horizon(now)
    frozen_until = freeze_boundary(now)

    slots = WeeklySlot.objects.all()
    if len(slots) == 0:
        logger.warning("No WeeklySlots defined; exiting")
        return

    for slot in slots:
        if not slot.purpose:
            logger.info("No purpose connected, so nothing to fill")
            continue
        for starttime in _occurrences(slot, now, horizon):
            _fill_occurrence(slot, starttime, frozen_until)


def _occurrences(slot, now, horizon):
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


def _fill_occurrence(slot, starttime, frozen_until):
    # Chosen per occurrence, so the least_scheduled strategy sees each
    # placement it just made.
    video = slot.purpose.single_video(slot.duration)
    if not video:
        logger.info("Couldn't get a video to use in slot!")
        return
    end = starttime + slot.duration
    conflicts = list(Scheduleitem.objects.overlapping(starttime, end))

    if any(item.schedulereason != Scheduleitem.REASON_JUKEBOX for item in conflicts):
        # Something deliberate is on the air across this slot. Note this
        # includes an item that started *before* the slot and runs into it.
        logger.info("Already something scheduled across %s; skipping slot", starttime)
        return
    if conflicts:
        if starttime < frozen_until:
            # Frozen weeks change as little as possible: only genuinely
            # empty airtime may still be filled.
            logger.info("Not displacing jukebox fillers at %s inside the frozen weeks", starttime)
            return
        Scheduleitem.objects.filter(pk__in=[item.pk for item in conflicts]).delete()

    Scheduleitem.objects.create(
        video=video,
        schedulereason=Scheduleitem.REASON_AUTO,
        starttime=starttime,
        duration=video.duration,
    )
