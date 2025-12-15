import datetime
from typing import Iterable, List, Protocol

from portion import closedopen, Interval, empty

from agenda.jukebox.utils import ceil_minute, floor_minute


class ScheduledItem(Protocol):
    starttime: datetime.datetime

    def endtime(self) -> datetime.datetime: ...


class GapDetector:
    """Pure gap detection logic based on interval arithmetic.

    Detects free intervals within a window from a set of scheduled items.
    """

    @staticmethod
    def detect(
        start: datetime.datetime,
        end: datetime.datetime,
        items: Iterable[ScheduledItem],
        minimum_gap_seconds: int = 300,
        alignment: str = "minute",
    ) -> List[Interval]:
        if start >= end:
            return []
        # Align window to minute boundaries for consistent detection
        window = closedopen(ceil_minute(start), floor_minute(end))
        busy_intervals = empty()
        for item in items:
            istart = ceil_minute(item.starttime)
            iend = floor_minute(item.endtime())
            busy_intervals = busy_intervals | (closedopen(istart, iend) & window)
        free_intervals = window - busy_intervals
        gaps: List[Interval] = []
        for interval in free_intervals:
            if not interval.empty and interval.atomic:
                duration = (interval.upper - interval.lower).total_seconds()
                if duration >= minimum_gap_seconds:
                    gaps.append(interval)
        return gaps
