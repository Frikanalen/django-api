from datetime import date, datetime, time, timedelta
from typing import Union
from zoneinfo import ZoneInfo

from django.db import models
from django.db.models import Q, Window, F
from django.db.models.functions import Lag, Lead


class ScheduleitemQuerySet(models.QuerySet):
    """
    QuerySet for ScheduleItem model with convenience methods for date-range filtering.
    """

    TZ = ZoneInfo("Europe/Oslo")

    def normalize_date(self, value: Union[str, date, datetime, None]) -> date | None:
        """
        Normalize various date representations to a date object.
        Accepts strings in 'YYYY-MM-DD', date, datetime, or None.
        """
        if not value:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.astimezone(self.TZ).date()
        if isinstance(value, str):
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                return None
        return None

    def by_day(
        self,
        start_date: Union[str, date, datetime, None] = None,
        days: int = 7,
        include_surrounding: bool = False,
    ) -> models.QuerySet:
        """
        Return items from `start_date` (defaults to today) for `days` days.
        If `include_surrounding` is True, also include the immediately
        preceding and following items via window functions in one pass.
        """
        # Normalize and compute datetime bounds
        date_obj = self.normalize_date(start_date) or datetime.now(self.TZ).date()
        start_dt = datetime.combine(date_obj, time.min, tzinfo=self.TZ)
        end_dt = start_dt + timedelta(days=days)

        # Base filter for main range
        main_filter = Q(starttime__gte=start_dt, starttime__lt=end_dt)

        if not include_surrounding:
            return self.filter(main_filter).order_by("starttime")

        # Annotate each row with previous and next PKs
        annotated = self.annotate(
            prev_pk=Window(expression=Lag("pk"), order_by=[F("starttime")]),
            next_pk=Window(expression=Lead("pk"), order_by=[F("starttime")]),
        )

        # Filter for main range or matching prev/next PK
        range_q = main_filter
        prev_q = Q(pk=F("prev_pk"))
        next_q = Q(pk=F("next_pk"))

        return annotated.filter(range_q | prev_q | next_q).order_by("starttime")

    def expand_to_surrounding(
        self, start_dt: datetime, end_dt: datetime
    ) -> tuple[datetime, datetime]:
        """
        Expand [start_dt, end_dt] to include the immediate event before start_dt
        and the immediate event after end_dt, if they exist.

        Performs at most two extra queries:
          - one ORDER BY -starttime to find the latest <= start_dt
          - one ORDER BY starttime to find the earliest >= end_dt
        """
        # previous event at or before start_dt
        prev_start = (
            self.filter(starttime__lte=start_dt)
            .order_by("-starttime")
            .values_list("starttime", flat=True)
            .first()
        )
        if prev_start:
            start_dt = prev_start

        # next event at or after end_dt
        next_start = (
            self.filter(starttime__gte=end_dt)
            .order_by("starttime")
            .values_list("starttime", flat=True)
            .first()
        )
        if next_start:
            end_dt = next_start

        return start_dt, end_dt
