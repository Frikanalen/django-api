import datetime
from functools import reduce
from typing import List

from django.db.models import QuerySet
from portion import Interval, empty


from agenda.jukebox.utils import interval_duration_sec, scheduleitem_as_interval
from agenda.views import logger
from fk.models import Scheduleitem

LOOKBACK = datetime.timedelta(hours=24)


class ScheduleGapFinder:
    def find_schedule_gaps(
        self,
        window: Interval,
        minimum_gap_seconds: int = 300,
    ) -> List[Interval]:
        """Find all intervals of at least `minimum_gap_seconds` duration
        between existing schedule items within the given `window` interval."""

        def reduce_to_busy_map(busy: Interval, item: Interval) -> Interval:
            """Add this schedule item's time slot to the set of busy periods.
            Schedule items are cropped to the window interval."""
            return busy | (item & window)

        reserved_areas = [scheduleitem_as_interval(x) for x in self._get_schedule_items(window)]
        busy_intervals = reduce(reduce_to_busy_map, reserved_areas, empty())
        free_intervals = window - busy_intervals

        return [
            schedule_gap & window
            for schedule_gap in free_intervals
            if interval_duration_sec(schedule_gap) >= minimum_gap_seconds
        ]

    @staticmethod
    def _get_schedule_items(window: Interval) -> QuerySet[Scheduleitem]:
        """Return a queryset of scheduled items within and previous to the window.
        We include items quite a bit in the past, to ensure that an item which starts
        in the past but ends within our window still remains."""
        lookback = window.lower - LOOKBACK

        scheduled_qs = Scheduleitem.with_video.filter(
            starttime__gte=lookback, starttime__lt=window.upper
        ).order_by("starttime")

        logger.debug("%d items already scheduled in window %s", scheduled_qs.count(), lookback)
        return scheduled_qs
