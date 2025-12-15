import datetime
from typing import List, Iterable, Protocol

import portion as P

from agenda.views import logger
from fk.models import Scheduleitem


class ScheduledItem(Protocol):
    """Protocol for objects representing scheduled items."""

    starttime: datetime.datetime

    def endtime(self) -> datetime.datetime:
        """Return the end time of this item."""
        ...


def _calculate_busy_intervals(items: Iterable[ScheduledItem], window: P.Interval) -> P.Interval:
    """Calculate the union of all scheduled item intervals within a window.

    Args:
        items: Scheduled items to process
        window: Time window to constrain intervals to

    Returns:
        Union of all item intervals intersected with the window
    """
    busy_intervals = P.empty()
    for item in items:
        item_interval = P.closedopen(item.starttime, item.endtime())
        # Only consider the part that overlaps with our window
        busy_intervals = busy_intervals | (item_interval & window)
    return busy_intervals


def _intervals_to_gaps(intervals: P.Interval, minimum_duration_seconds: float) -> List[P.Interval]:
    """Filter intervals by minimum duration, returning only large enough gaps.

    Args:
        intervals: Portion interval(s) to filter
        minimum_duration_seconds: Minimum gap duration in seconds

    Returns:
        List of intervals meeting the minimum duration requirement
    """
    gaps: List[P.Interval] = []
    for interval in intervals:
        if not interval.empty and interval.atomic:
            duration = (interval.upper - interval.lower).total_seconds()
            if duration >= minimum_duration_seconds:
                gaps.append(interval)
    return gaps


class GapFinder:
    MINIMUM_GAP_SECONDS = 300
    LOOKBACK_HOURS = 24  # How far back to look for long-running items

    def _fetch_overlapping_items(
        self, start: datetime.datetime, end: datetime.datetime
    ) -> List[Scheduleitem]:
        """Fetch scheduled items that could overlap with the given time window.

        An item overlaps if it starts before the window ends AND ends after the window starts.
        Since we can't easily filter on endtime (computed field), we look back LOOKBACK_HOURS
        from start to catch any long-running items.

        Args:
            start: Window start time
            end: Window end time

        Returns:
            List of Scheduleitem objects that might overlap the window
        """
        lookback = start - datetime.timedelta(hours=self.LOOKBACK_HOURS)
        return list(
            Scheduleitem.objects.filter(starttime__gte=lookback, starttime__lt=end).order_by(
                "starttime"
            )
        )

    def _find_gaps_in_items(
        self, start: datetime.datetime, end: datetime.datetime, items: Iterable[ScheduledItem]
    ) -> List[P.Interval]:
        """
        Core algorithm: find gaps between scheduled items using interval arithmetic.

        Args:
            start: Window start time
            end: Window end time
            items: Iterable of scheduled items

        Returns:
            List of interval gaps found within the window
        """
        window = P.closedopen(start, end)
        busy_intervals = _calculate_busy_intervals(items, window)
        free_intervals = window - busy_intervals
        return _intervals_to_gaps(free_intervals, self.MINIMUM_GAP_SECONDS)

    def find_gaps(self, start: datetime.datetime, end: datetime.datetime) -> List[P.Interval]:
        """Return gaps within [start, end) based on existing Scheduleitems."""
        logger.info("Being asked to find gaps from %s to %s", start, end)
        scheduled = self._fetch_overlapping_items(start, end)
        return self._find_gaps_in_items(start, end, scheduled)
