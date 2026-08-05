# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
"""Schedule filling.

Both entry points here are invoked from management commands (and thus from
nightly CronJobs), not from the web request path:

* :func:`fill_next_weeks_agenda` places one video per :class:`WeeklySlot`.
* :func:`fill_agenda_with_jukebox` fills whatever is left over with fillers.
"""

import datetime
import logging

from django.utils import timezone

from fk.models import Scheduleitem, Video, WeeklySlot

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

        if Scheduleitem.objects.filter(
            starttime__gte=next_datetime, starttime__lt=end_next_datetime
        ).exists():
            # Ouch we have already scheduled something in the slot
            logger.debug("Already something scheduled in this slot")
            continue
        item = Scheduleitem(
            video=video,
            schedulereason=Scheduleitem.REASON_AUTO,
            starttime=next_datetime,
            duration=video.duration,
        )
        item.save()


def fill_agenda_with_jukebox(start=None, days=1):
    start = start or timezone.now()
    end = start + datetime.timedelta(days=days)

    # A filler must have a length that actually advances the schedule
    # clock; `_fill_time_with_jukebox` would otherwise place a zero-length
    # video once a minute for the whole window.  Video.duration defaults
    # to zero, so this is ordinary unimported data, not corruption --
    # negative lengths are barred by a check constraint on the model.
    # Kept out of Video.objects.fillers() on purpose: that queryset also
    # feeds the jukebox CSV, whose output is a frozen contract.
    candidates = Video.objects.fillers().exclude(duration__lte=datetime.timedelta(0)).order_by("?")

    jukebox_choices = _items_for_gap(start, end, candidates)
    for schedobj in jukebox_choices:
        video = schedobj["video"]
        item = Scheduleitem(
            video=video,
            schedulereason=Scheduleitem.REASON_JUKEBOX,
            starttime=schedobj["starttime"],
            duration=video.duration,
        )
        item.save()

    return jukebox_choices


def ceil_minute(dt):
    return floor_minute(dt) + datetime.timedelta(minutes=1)


def floor_minute(dt):
    """Returns the datetime with seconds and microseconds cleared"""
    return dt.replace(second=0, microsecond=0)


def _items_for_gap(start, end, candidates):
    logger.info("Being asked to fill gap from %s to %s", start, end)
    # The smallest gap this function will try to fill
    MINIMUM_GAP_SECONDS = 300

    # Get a list of previously scheduled videos
    startdt, enddt = Scheduleitem.objects.expand_to_surrounding(start, end)
    already_scheduled = list(
        Scheduleitem.objects.filter(starttime__gte=startdt, starttime__lte=enddt).order_by(
            "starttime"
        )
    )

    start_of_gap = ceil_minute(start)
    end = floor_minute(end)

    pool = None
    full_items = []
    while True:
        end_of_gap = end

        # Get the first video already existing in schedule
        # that falls within the current time range
        if len(already_scheduled):
            extant_video = already_scheduled.pop(0)

            # Keep trying until we find one that ends
            # inside the range we are working with
            if extant_video.endtime() < start_of_gap:
                continue

            # If it doesn't begin until after the
            # end of our window, the window is
            # empty; otherwise this video is now
            # the end of our gap
            if extant_video.starttime > end:
                extant_video = None
            else:
                end_of_gap = floor_minute(extant_video.starttime)

        gap = (end_of_gap - start_of_gap).total_seconds()

        if gap > MINIMUM_GAP_SECONDS:
            (items, pool) = _fill_time_with_jukebox(
                start_of_gap, end_of_gap, candidates, current_pool=pool
            )
            full_items.extend(items)
        else:
            logger.info("Not filling %d second gap", gap)

        if end_of_gap >= end:
            break

        start_of_gap = ceil_minute(extant_video.endtime())
    return full_items


def _fill_time_with_jukebox(start, end, videos, current_pool=None):
    current_time = start
    video_pool = current_pool or list(videos)
    logger.info("Filling jukebox from %s to %s - %d in pool", start, end, len(video_pool))
    rejected_videos = []
    new_items = []

    def plist(l):
        return "[" + " ".join(str(v.id) for v in l) + "]"

    def next_vid(first=False):
        logger.debug(Video.objects.all())
        logger.debug("next vid %s rej %s pool %s", first, plist(rejected_videos), plist(video_pool))
        if len(video_pool) < len(videos) and first:
            video_pool.extend(list(videos))
        if len(rejected_videos):
            return rejected_videos.pop(0)
        if not len(video_pool):
            return None
        return video_pool.pop(0)

    while current_time < end:
        video = next_vid(True)
        if not video:
            # Nothing eligible to draw from; leave the rest of the gap empty
            # rather than dereferencing None below.
            logger.info("No videos available to fill from %s", current_time)
            break
        new_rejects = []

        while current_time + video.duration > end:
            logger.debug("end overshoots time %s", current_time + video.duration)
            if video not in rejected_videos and video not in new_rejects:
                new_rejects.append(video)
            video = next_vid()
            logger.debug(
                "next vid is %s rejected %s new_rej %s",
                video,
                plist(rejected_videos),
                plist(new_rejects),
            )
            if not video:
                return new_items, rejected_videos + video_pool
        rejected_videos.extend(new_rejects)
        new_items.append({"id": video.id, "starttime": current_time, "video": video})
        logger.info("Added video %s at curr time %s", video.id, current_time.strftime("%H:%M:%S"))
        current_time = ceil_minute(current_time + video.duration)

    return new_items, rejected_videos + video_pool
