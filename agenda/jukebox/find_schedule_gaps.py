from functools import reduce
from typing import Iterable, List

from portion import closedopen, Interval, empty
from fk.models import Scheduleitem


def interval_duration_sec(interval: Interval) -> float:
    """Convert an interval to a timedelta representing its duration."""
    return (interval.upper - interval.lower).total_seconds()


def find_schedule_gaps(
    window: Interval,
    schedule_items: Iterable[Scheduleitem],
    minimum_gap_seconds: int = 300,
) -> List[Interval]:
    """Find all intervals of at least `minimum_gap_seconds` duration
    between items within the given `window` interval."""

    def add_scheduled_time(busy: Interval, item: Scheduleitem) -> Interval:
        """Add this schedule item's time slot to the set of busy periods.
        Schedule items are cropped to the window interval."""
        return busy | (closedopen(item.starttime, item.endtime) & window)

    busy_intervals = reduce(add_scheduled_time, schedule_items, empty())
    free_intervals = window - busy_intervals

    return [
        schedule_gap
        for schedule_gap in free_intervals
        if interval_duration_sec(schedule_gap) >= minimum_gap_seconds
    ]
