import datetime
from typing import List, Iterable, Protocol

from portion import closedopen, Interval, empty


from agenda.jukebox.utils import ceil_minute, floor_minute
from agenda.views import logger
from fk.models import Scheduleitem
from agenda.jukebox.schedule_repository import ScheduleRepository
from agenda.jukebox.gap_detector import GapDetector


class ScheduledItem(Protocol):
    """Protocol for objects representing scheduled items."""

    starttime: datetime.datetime

    def endtime(self) -> datetime.datetime:
        """Return the end time of this item."""
        ...


def _calculate_busy_intervals(items: Iterable[ScheduledItem], window: Interval) -> Interval:
    """
    Calculates the combined busy intervals for a collection of scheduled items
    within a given time window. The intervals are adjusted to align precisely
    to minute boundaries and are limited to intersect with the specified time
    window.

    Parameters:
        items (Iterable[ScheduledItem]): A collection of scheduled items,
            each item having start and end times.
        window (Interval[datetime.datetime]): The time interval within which
            the busy intervals are calculated.

    Returns:
        Interval[datetime.datetime]: Combined interval of all busy times within
            the given window, adjusted to minute boundaries.
    """
    busy_intervals: Interval = empty()
    for item in items:
        # Round to minute boundaries for consistency
        start = ceil_minute(item.starttime)
        end = floor_minute(item.endtime())
        item_interval = closedopen(start, end)
        # Only consider the part that overlaps with our window
        busy_intervals = busy_intervals | (item_interval & window)
    return busy_intervals


def _intervals_to_gaps(intervals: Interval, minimum_duration_seconds: float) -> List[Interval]:
    """Filter intervals by minimum duration, returning only large enough gaps.

    Args:
        intervals: Portion interval(s) to filter
        minimum_duration_seconds: Minimum gap duration in seconds

    Returns:
        List of intervals meeting the minimum duration requirement
    """
    gaps: List[Interval] = []
    for interval in intervals:
        if not interval.empty and interval.atomic:
            duration = (interval.upper - interval.lower).total_seconds()
            if duration >= minimum_duration_seconds:
                gaps.append(interval)
    return gaps


class GapFinder:
    MINIMUM_GAP_SECONDS = 300

    def __init__(self, lookback_hours: int = 24) -> None:
        self.lookback_hours = lookback_hours

    def _fetch_overlapping_items(
        self, start: datetime.datetime, end: datetime.datetime
    ) -> List[Scheduleitem]:
        # Delegate data access to repository
        return ScheduleRepository.fetch_overlaps(start, end, self.lookback_hours)

    def _find_gaps_in_items(
        self, start: datetime.datetime, end: datetime.datetime, items: Iterable[Scheduleitem]
    ) -> List[Interval]:
        # Delegate pure logic to GapDetector
        return GapDetector.detect(
            start=start,
            end=end,
            items=items,
            minimum_gap_seconds=self.MINIMUM_GAP_SECONDS,
            alignment="minute",
        )

    def find_gaps(self, start: datetime.datetime, end: datetime.datetime) -> List[Interval]:
        logger.info("Being asked to find gaps from %s to %s", start, end)
        scheduled = self._fetch_overlapping_items(start, end)
        return self._find_gaps_in_items(start, end, scheduled)
