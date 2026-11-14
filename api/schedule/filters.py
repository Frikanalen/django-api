from django import forms
from django_filters import rest_framework as filters

from fk.models import Scheduleitem


class DateOrTodayField(forms.DateField):
    def to_python(self, value):
        if value == "today":
            return None
        return super().to_python(value)


class DateOrTodayFilter(filters.DateFilter):
    field_class = DateOrTodayField


class ScheduleitemFilter(filters.FilterSet):
    date = DateOrTodayFilter()
    days = filters.NumberFilter(min_value=1)
    surrounding = filters.BooleanFilter()
    include_posters = filters.BooleanFilter(
        method="keep",
        help_text="Include members' text posters, which playout airs in the gaps "
        "between programmes. Left out by default: they are not programmes.",
    )

    class Meta:
        model = Scheduleitem
        fields = ["date", "days", "surrounding", "include_posters"]

    def keep(self, queryset, name, value):
        return queryset

    def filter_queryset(self, queryset):
        params = self.form.cleaned_data
        if not params.get("include_posters"):
            queryset = queryset.programmes()
        return queryset.by_day(
            start_date=params.get("date"),
            days=int(params.get("days") or 1),
            include_surrounding=bool(params.get("surrounding")),
        )
