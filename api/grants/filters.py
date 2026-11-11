from django_filters import rest_framework as filters

from scheduling.models import AirtimeGrant


class AirtimeGrantFilter(filters.FilterSet):
    organization = filters.NumberFilter(field_name="organization_id")
    starts_after = filters.IsoDateTimeFilter(field_name="starttime", lookup_expr="gte")
    starts_before = filters.IsoDateTimeFilter(field_name="starttime", lookup_expr="lt")
    state = filters.ChoiceFilter(choices=AirtimeGrant.State.choices)

    class Meta:
        model = AirtimeGrant
        fields = ["organization", "starts_after", "starts_before", "state"]
