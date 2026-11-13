from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from api.auth.permissions import IsStaffOrReadOnly
from api.pagination import FkSchedulePagination
from api.schedule.filters import ScheduleitemFilter
from api.schedule.serializers import (
    ScheduleitemModifySerializer,
    ScheduleitemReadSerializer,
)
from fk.models import Scheduleitem
from fk.signals import schedule_version


class ScheduleitemViewSet(viewsets.ModelViewSet):
    """
    The compiled broadcast schedule. Read by everyone; written only by
    staff, as corrections. Organizations program through /api/grants.

    list:
    Query parameters
    ----------------
    `date`: YYYY-MM-DD or 'today' (Europe/Oslo). Defaults to today.

    `days`: Number of days. Defaults to 1.

    `surrounding`: Include event before and after the window.

    `ordering`: Field to order by. Prefix '-' for desc. Defaults to 'starttime'.
    """

    # Eagerly load the nested relations exposed by ScheduleitemReadSerializer.
    # The files themselves still need prefetching; their format does not,
    # now that it is a column on the row rather than a table to join.
    queryset = Scheduleitem.objects.select_related(
        "video__organization", "video__live_transmission__source"
    ).prefetch_related(
        "video__categories",
        "video__videofile_set",
    )
    pagination_class = FkSchedulePagination
    permission_classes = (IsStaffOrReadOnly,)
    filterset_class = ScheduleitemFilter
    ordering_fields = ["starttime"]
    ordering = ["starttime"]

    def filter_queryset(self, queryset):
        if self.action == "list":
            return super().filter_queryset(queryset)
        return queryset

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return ScheduleitemModifySerializer
        return ScheduleitemReadSerializer

    @extend_schema(
        responses=inline_serializer("ScheduleVersion", fields={"version": serializers.CharField()}),
    )
    @action(detail=False, methods=["get"], pagination_class=None, filterset_class=None)
    def version(self, request):
        """An opaque token that changes whenever the schedule or a grant does.

        For callers that cache the schedule (the frontend): poll this, and
        refetch only when it differs from the last one seen.
        """
        return Response({"version": schedule_version()})
