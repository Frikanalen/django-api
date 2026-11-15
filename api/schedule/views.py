from rest_framework import viewsets

from api.auth.permissions import IsStaffOrReadOnly
from api.pagination import FkSchedulePagination
from api.schedule.filters import ScheduleitemFilter
from api.schedule.serializers import (
    ScheduleitemModifySerializer,
    ScheduleitemReadSerializer,
)
from fk.models import Scheduleitem


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

    `include_posters`: Include members' text posters (for playout).

    `ordering`: Field to order by. Prefix '-' for desc. Defaults to 'starttime'.
    """

    # Eagerly load the nested relations exposed by ScheduleitemReadSerializer.
    # The files themselves still need prefetching; their format does not,
    # now that it is a column on the row rather than a table to join.
    queryset = Scheduleitem.objects.select_related(
        "video__organization", "video__live_transmission__source", "poster__organization"
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
