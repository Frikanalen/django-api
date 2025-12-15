from datetime import date, datetime, time, timedelta
from typing import Union
from zoneinfo import ZoneInfo

from django.db import models
from django.db.models import Q


class ScheduleitemQuerySet(models.QuerySet):
    """
    QuerySet for ScheduleItem model with convenience methods for date-range filtering.
    """

    TZ = ZoneInfo("Europe/Oslo")

    def with_video(self):
        """Return only schedule items that have a video attached."""
        return self.filter(video__isnull=False)

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
            return self.filter(main_filter).order_by("-starttime")

        # Get main range items
        main_items = list(self.filter(main_filter))

        # Get the item immediately before start_dt
        prev_item = self.filter(starttime__lt=start_dt).order_by("-starttime").first()

        # Get the item immediately after or at end_dt
        next_item = self.filter(starttime__gte=end_dt).order_by("starttime").first()

        # Combine all items
        all_items = []
        if prev_item:
            all_items.append(prev_item)
        all_items.extend(main_items)
        if next_item:
            all_items.append(next_item)

        # Sort by starttime descending and return as queryset-like behavior
        # We need to return actual queryset, so let's use PKs
        if not all_items:
            return self.none()

        pks = [item.pk for item in all_items]
        return self.filter(pk__in=pks).order_by("-starttime")

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


class ScheduleitemWithVideoManager(models.Manager):
    """
    Manager that only returns Scheduleitem instances where video is not null.

    This allows for better type safety - when using this manager, you can be
    confident that item.video will never be None.

    Usage:
        from fk.models import Scheduleitem

        # All items with videos
        items = Scheduleitem.with_video.all()

        # Type checkers still see video as Optional, but you can assert:
        for item in Scheduleitem.with_video.all():
            assert item.video is not None
            # Now type checkers know video is not None
    """

    def get_queryset(self) -> "ScheduleitemQuerySet":
        return ScheduleitemQuerySet(self.model, using=self._db).filter(video__isnull=False)

