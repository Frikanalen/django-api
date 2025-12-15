"""Jukebox scheduling components.

This package separates data access (repositories/loaders) from pure logic (detectors/planners).
"""

import datetime
from typing import List

from django.db.models import QuerySet
from portion import Interval

from agenda.jukebox.find_schedule_gaps import find_schedule_gaps
from agenda.jukebox.planner import ScheduleGapPlanner

from agenda.jukebox.utils import week_as_interval, scheduleitem_as_interval
from agenda.views import logger
from fk.models import Scheduleitem

LOOKBACK = datetime.timedelta(hours=24)


def _get_schedule_items(window: Interval) -> QuerySet[Scheduleitem]:
    """Return a queryset of scheduled items within and adjacent to the window.
    Note that this will include items quite a bit in the past, to ensure that an
    item which starts in the past but ends within our window still remains."""
    lookback = window.lower - LOOKBACK

    scheduled_qs = Scheduleitem.with_video.filter(
        starttime__gte=lookback, starttime__lt=window.upper
    ).order_by("starttime")

    logger.debug("%d items already scheduled in window %s", scheduled_qs.count(), lookback)
    return scheduled_qs


def _find_schedule_gaps(window: Interval) -> List[Interval]:
    logger.info("Being asked to find gaps from %s to %s", window.lower, window.upper)
    lookback_window = window.lower - LOOKBACK
    reserved_areas = [scheduleitem_as_interval(x) for x in _get_schedule_items(lookback_window)]
    return find_schedule_gaps(window=window, reserved_areas=reserved_areas)


def plan_iso_week(iso_year: int, iso_week: int, now) -> List[Scheduleitem]:
    """
    Fill gaps in a given ISO week with schedule items.
    Args:
        iso_year: The ISO year.
        iso_week: The ISO week number.
        now: The current datetime context for scoring.
    Returns:
        List of unsaved Scheduleitem objects to fill the gaps.
    """
    logger.info("Planning ISO week %d-W%02d", iso_year, iso_week)

    interval = week_as_interval(iso_year, iso_week)
    free_slots = _find_schedule_gaps(interval)
    scheduled_items = list(_get_schedule_items(interval))
    logger.info("Found %d gaps to fill within window %s", len(free_slots), interval)

    return [
        item
        for gap in free_slots
        for item in ScheduleGapPlanner().layout(gap, scheduled_items, now)
    ]
