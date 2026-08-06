# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
"""Placing one video per WeeklySlot.

Runs before the jukebox (see :mod:`agenda.scheduling.jukebox`), which then
fills whatever airtime these slots leave empty.
"""

import logging

from fk.models import Scheduleitem, WeeklySlot

logger = logging.getLogger(__name__)


def fill_next_weeks_agenda():
    slots = WeeklySlot.objects.all()

    if len(slots) == 0:
        logger.warning("No WeeklySlots defined; exiting")
        return

    for slot in slots:
        if not slot.purpose:
            logger.info("No purpose connected, so nothing to fill")
            continue
        video = slot.purpose.single_video(slot.duration)
        if not video:
            logger.info("Couldn't get a video to use in slot!")
            continue
        next_datetime = slot.next_datetime()
        end_next_datetime = next_datetime + slot.duration

        if Scheduleitem.objects.overlapping(next_datetime, end_next_datetime).exists():
            # Something is already on the air across this slot. Note this
            # includes an item that started *before* the slot and runs into
            # it, which the old starttime-only check could not see.
            logger.info("Already something scheduled across %s; skipping slot", next_datetime)
            continue
        item = Scheduleitem(
            video=video,
            schedulereason=Scheduleitem.REASON_AUTO,
            starttime=next_datetime,
            duration=video.duration,
        )
        item.save()
