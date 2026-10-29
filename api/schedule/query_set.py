from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import models
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.db.models import Q, Subquery


class ScheduleitemQuerySet(models.QuerySet):
    """
    QuerySet for ScheduleItem model with convenience methods for date-range filtering.
    """

    TZ = ZoneInfo("Europe/Oslo")

    def normalize_date(self, value: str | date | datetime | None) -> date | None:
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
                # Date-only input, and only the date survives the call, so an
                # absent zone cannot affect the result.
                return datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007
            except ValueError:
                return None
        return None

    def by_day(
        self,
        start_date: str | date | datetime | None = None,
        days: int = 7,
        include_surrounding: bool = False,
    ) -> models.QuerySet:
        """
        Return items from `start_date` (defaults to today) for `days` days.
        If `include_surrounding` is True, also include the immediately
        preceding and following items using scalar subqueries.
        """
        # Normalize and compute datetime bounds
        date_obj = self.normalize_date(start_date) or datetime.now(self.TZ).date()
        start_dt = datetime.combine(date_obj, time.min, tzinfo=self.TZ)
        end_dt = start_dt + timedelta(days=days)

        # Base filter for main range
        main_filter = Q(starttime__gte=start_dt, starttime__lt=end_dt)

        if not include_surrounding:
            return self.filter(main_filter).order_by("starttime")

        previous_pk = self.filter(starttime__lt=start_dt).order_by("-starttime").values("pk")[:1]
        next_pk = self.filter(starttime__gte=end_dt).order_by("starttime").values("pk")[:1]

        return self.filter(
            main_filter | Q(pk__in=Subquery(previous_pk)) | Q(pk__in=Subquery(next_pk))
        ).order_by("starttime")

    def overlapping(self, start_dt: datetime, end_dt: datetime) -> models.QuerySet:
        """
        Items whose airtime intersects [start_dt, end_dt).

        This is the one definition of "that airtime is already taken". The
        bounds are half-open, so an item ending exactly at `start_dt` does not
        count -- back-to-back programming is not a conflict.

        Asked of `airtime`, the generated range column, so the question is
        one indexed `&&` against its GiST index. The predicate this replaces
        compared `starttime + duration` row by row, which no index could
        answer -- the starttime half is unbounded to the left, so settling
        "is this slot free" meant reading every item ever broadcast.
        """
        return self.filter(airtime__overlap=DateTimeTZRange(start_dt, end_dt, "[)"))
