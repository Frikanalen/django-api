import datetime
from typing import List

from fk.models import Scheduleitem


class ScheduleRepository:
    """Data access layer for schedule items.

    Encapsulates queries to fetch items overlapping a window, using a lookback
    to include long-running items whose computed endtime may overlap the window.
    """

    @staticmethod
    def fetch_overlaps(
        start: datetime.datetime, end: datetime.datetime, lookback_hours: int
    ) -> List[Scheduleitem]:
        lookback = start - datetime.timedelta(hours=lookback_hours)
        return list(
            Scheduleitem.objects.filter(
                starttime__gte=lookback,
                starttime__lt=end,
            ).order_by("starttime")
        )
