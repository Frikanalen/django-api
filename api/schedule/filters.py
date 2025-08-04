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
