# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
"""Filling leftover airtime with fillers.

Runs after the weekly slots are placed (see :mod:`agenda.scheduling.weekly_slots`)
and fills whatever airtime is still empty, working minute-aligned around the
programming that is already scheduled.
"""

import datetime
import logging

from django.utils import timezone

from fk.models import Scheduleitem, Video

logger = logging.getLogger(__name__)


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

    jukebox_choices = items_for_gap(start, end, candidates)
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


def items_for_gap(start, end, candidates):
    """Plan (but do not save) filler placements between `start` and `end`.

    Returns a list of `{"id", "starttime", "video"}` dicts, skipping any
    stretch already occupied by an existing Scheduleitem.
    """
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

    def plist(video_list):
        return "[" + " ".join(str(v.id) for v in video_list) + "]"

    def next_vid(first=False):
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
