"""Jukebox scheduling components.

This package separates data access (repositories/loaders) from pure logic (detectors/planners).
"""

from datetime import timedelta
from typing import List

from portion import Interval, closedopen

from agenda.jukebox.find_schedule_gaps import find_schedule_gaps
from agenda.jukebox.gap_filler import GapFiller
from agenda.jukebox.schedule_repository import ScheduleRepository

from agenda.jukebox.utils import week_as_interval
from agenda.jukebox.week_context_builder import WeekContextBuilder
from agenda.views import logger
from fk.models import Scheduleitem, Video

LOOKBACK_HOURS = 24


def _find_gaps(window: Interval) -> List[Interval]:
    logger.info("Being asked to find gaps from %s to %s", window.lower, window.upper)
    lookback_window = closedopen(window.lower - timedelta(hours=LOOKBACK_HOURS), window.upper)
    schedule_items = ScheduleRepository.fetch_schedule_items_by_interval(lookback_window)
    return find_schedule_gaps(window=window, schedule_items=schedule_items)


def plan_iso_week(iso_year: int, iso_week: int) -> List[Scheduleitem]:
    """Fill gaps in a given ISO week with schedule items."""
    logger.info("Planning ISO week %d-W%02d", iso_year, iso_week)

    interval = week_as_interval(iso_year, iso_week)
    gap_filler = GapFiller(Video.objects.fillers())
    free_slots = _find_gaps(interval)
    logger.info("Found %d gaps to fill in window %s", len(free_slots), interval)

    week_ctx = WeekContextBuilder.build_for_window(interval)

    return [item for gap in free_slots for item in gap_filler.get_items_for_gap(gap, week_ctx)]
