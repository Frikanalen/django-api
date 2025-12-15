import datetime
from dataclasses import dataclass
from typing import List, Iterable, Protocol

from agenda.views import logger
from fk.models import Scheduleitem


@dataclass(frozen=True)
class Gap:
    """A contiguous free time interval [start, end] suitable for jukebox filling."""

    start: datetime.datetime
    end: datetime.datetime


class ScheduledItem(Protocol):
    """Protocol for objects representing scheduled items."""
    starttime: datetime.datetime

    def endtime(self) -> datetime.datetime:
        """Return the end time of this item."""
        ...


class GapFinder:
    MINIMUM_GAP_SECONDS = 300

    def _is_large_enough(self, gap_start: datetime.datetime, gap_end: datetime.datetime) -> bool:
        """Check if a gap meets the minimum duration requirement."""
        return (
            gap_end > gap_start
            and (gap_end - gap_start).total_seconds() >= self.MINIMUM_GAP_SECONDS
        )

    def _find_gaps_in_items(
        self, start: datetime.datetime, end: datetime.datetime, items: Iterable[ScheduledItem]
    ) -> List[Gap]:
        """
        Core algorithm: find gaps between scheduled items.

        Args:
            start: Window start time
            end: Window end time
            items: Iterable of scheduled items (must be sorted by starttime)

        Returns:
            List of gaps found within the window
        """
        pointer = start
        gaps: List[Gap] = []

        for item in items:
            # Skip items that have already ended
            if item.endtime() <= pointer:
                continue
            # Check if there's a large enough gap before this item
            if self._is_large_enough(pointer, item.starttime):
                gaps.append(Gap(pointer, item.starttime))
            # Move pointer past this item
            pointer = item.endtime()

        # Add final gap if any remains in the window
        if self._is_large_enough(pointer, end):
            gaps.append(Gap(pointer, end))

        return gaps

    def find_gaps(self, start: datetime.datetime, end: datetime.datetime) -> List[Gap]:
        """Return gaps within [start, end) based on existing Scheduleitems."""
        logger.info("Being asked to find gaps from %s to %s", start, end)

        # Fetch all items that could overlap with the window
        scheduled = list(
            Scheduleitem.objects.filter(starttime__lt=end).order_by("starttime")
        )

        return self._find_gaps_in_items(start, end, scheduled)

