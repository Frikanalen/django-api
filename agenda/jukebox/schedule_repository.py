from django.db.models import QuerySet
from portion import Interval

from fk.models import Scheduleitem


class ScheduleRepository:
    """Data access layer for schedule items.

    Encapsulates queries to fetch items overlapping a window, using a lookback
    to include long-running items whose computed endtime may overlap the window.
    """

    @staticmethod
    def fetch_schedule_items_by_interval(window: Interval) -> QuerySet[Scheduleitem]:
        return Scheduleitem.objects.filter(
            starttime__gte=window.lower,
            starttime__lt=window.upper,
        ).order_by("starttime")
