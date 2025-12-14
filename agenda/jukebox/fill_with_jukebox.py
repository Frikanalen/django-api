import datetime
import logging

from django.utils import timezone

from agenda.views import logger
from fk.models import Video, Scheduleitem


def fill_with_jukebox(start=None, days=1):
    start = start or timezone.now()
    end = start + datetime.timedelta(days=days)

    candidates = Video.objects.fillers().order_by("?")

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
    logger.info("Being asked to fill gap from {} to {}".format(start, end))
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
            logging.info("Not filling %d second gap" % gap)

        if end_of_gap >= end:
            break

        start_of_gap = ceil_minute(extant_video.endtime())
    return full_items


def _fill_time_with_jukebox(start, end, videos, current_pool=None):
    current_time = start
    video_pool = current_pool or list(videos)
    logger.info("Filling jukebox from %s to %s - %d in pool" % (start, end, len(video_pool)))
    rejected_videos = []
    new_items = []

    def plist(l):
        return "[" + " ".join(str(v.id) for v in l) + "]"

    def next_vid(first=False):
        logger.debug(Video.objects.all())
        logger.debug(
            "next vid %s rej %s pool %s" % (first, plist(rejected_videos), plist(video_pool))
        )
        if len(video_pool) < len(videos) and first:
            video_pool.extend(list(videos))
        if len(rejected_videos):
            return rejected_videos.pop(0)
        if not len(video_pool):
            return None
        return video_pool.pop(0)

    while current_time < end:
        video = next_vid(True)
        new_rejects = []

        while current_time + video.duration > end:
            logger.debug("end overshoots time", current_time + video.duration)
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
