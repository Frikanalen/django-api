from functools import reduce
from typing import Iterable, List

from portion import Interval, empty

from agenda.jukebox.utils import interval_duration_sec


def find_schedule_gaps(
    window: Interval,
    reserved_areas: Iterable[Interval],
    minimum_gap_seconds: int = 300,
) -> List[Interval]:
    """Find all intervals of at least `minimum_gap_seconds` duration
    between items within the given `window` interval."""

    def reduce_to_busy_map(busy: Interval, item: Interval) -> Interval:
        """Add this schedule item's time slot to the set of busy periods.
        Schedule items are cropped to the window interval."""
        return busy | (item & window)

    busy_intervals = reduce(reduce_to_busy_map, reserved_areas, empty())
    free_intervals = window - busy_intervals

    return [
        schedule_gap & window
        for schedule_gap in free_intervals
        if interval_duration_sec(schedule_gap) >= minimum_gap_seconds
    ]
