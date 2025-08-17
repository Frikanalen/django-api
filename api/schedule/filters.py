import datetime
from zoneinfo import ZoneInfo

from django_filters import rest_framework as filters
from django.utils.dateparse import parse_date

from fk.models import Scheduleitem


class ScheduleitemFilter(filters.FilterSet):
    date = filters.CharFilter(method="filter_by_day")
    days = filters.NumberFilter(method="filter_by_day")
    surrounding = filters.BooleanFilter(method="filter_by_day")

    class Meta:
        model = Scheduleitem
        fields = ["date", "days", "surrounding"]

    def filter_by_day(self, queryset, name, value):
        params = self.request.query_params
        date_str = params.get("date")
        days = params.get("days") or 1
        surrounding = params.get("surrounding", False)
        try:
            date_val = parse_date(date_str)
        except ValueError:
            date_val = None
        return queryset.by_day(
            start_date=date_val, days=int(days), include_surrounding=bool(surrounding)
        )

    @property
    def qs(self):
        qs = super().qs
        params = self.request.query_params
        # If none of the three are present, enforce the default window = today
        if not any(k in params for k in ("date", "days", "surrounding")):
            qs = qs.by_day(
                start_date=datetime.datetime.now(tz=ZoneInfo("Europe/Oslo")).date().isoformat(),
                days=1,
                include_surrounding=False,
            )
        return qs
