"""Jukebox scheduling components.

This package separates data access (repositories/loaders) from pure logic (detectors/planners).
"""

from typing import List

from agenda.jukebox.gapfinder import ScheduleGapFinder
from agenda.jukebox.layout import ScheduleLayout

from agenda.jukebox.utils import week_as_interval
from agenda.views import logger
from fk.models import Scheduleitem


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
    free_slots = ScheduleGapFinder().find_schedule_gaps(interval)
    logger.info("Found %d gaps to fill within window %s", len(free_slots), interval)

    return [item for gap in free_slots for item in ScheduleLayout().layout(gap, now)]
