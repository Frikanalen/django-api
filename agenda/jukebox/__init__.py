"""Jukebox scheduling components.

This package separates data access (repositories/loaders) from pure logic (detectors/planners).
"""

from typing import List

from agenda.jukebox.gap_finder import GapFinder
from agenda.jukebox.gap_filler import GapFiller
from agenda.jukebox.jukebox_planner import JukeboxPlanner

__all__ = ["JukeboxPlanner"]

from agenda.jukebox.utils import get_week_boundaries
from agenda.jukebox.week_context_builder import WeekContextBuilder
from agenda.views import logger
from fk.models import Scheduleitem, Video


def plan_iso_week(iso_year: int, iso_week: int) -> List[Scheduleitem]:
    """Plan and persist jukebox placements for a whole ISO week.

    Analyzes the schedule for an ISO week to find free slots, then fills them
    with candidate videos.

    Args:
        iso_year: ISO year
        iso_week: ISO week number (1-53)

    Returns:
        List of Scheduleitem objects for the week (persisted to database)
    """
    logger.info("Planning ISO week %d-W%02d", iso_year, iso_week)

    interval = get_week_boundaries(iso_year, iso_week)
    gap_filler = GapFiller(Video.objects.fillers())
    free_slots = GapFinder().find_gaps(*interval)
    logger.info("Found %d gaps to fill in window %s -> %s", len(free_slots), *interval)
    for slot in free_slots:
        logger.info(f"Gap: {slot.lower}-{slot.upper}")

    # Build initial context from existing schedule. This mutable context is shared
    # across all gaps and accumulates state (recent videos, org airtime) as items
    # are placed, ensuring diversity and balance across the entire week.
    week_ctx = WeekContextBuilder.build_for_window(*interval)

    return [item for gap in free_slots for item in gap_filler.get_items_for_gap(gap, week_ctx)]
