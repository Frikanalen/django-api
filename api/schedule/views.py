from rest_framework import viewsets

from api.auth.permissions import IsInOrganizationOrReadOnly
from api.schedule.filters import ScheduleitemFilter
from api.schedule.serializers import ScheduleitemModifySerializer, ScheduleitemReadSerializer
from api.pagination import FkSchedulePagination
from fk.models import Scheduleitem


class ScheduleitemViewSet(viewsets.ModelViewSet):
    """
    Video events schedule

    list:
    Query parameters
    ----------------
    `date`: YYYY-MM-DD or 'today' (Europe/Oslo). Defaults to today.

    `days`: Number of days. Defaults to 1.

    `surrounding`: Include event before and after the window.

    `ordering`: Field to order by. Prefix '-' for desc. Defaults to 'starttime'.
    """

    # Eagerly load related video → organization and categories
    queryset = Scheduleitem.objects.select_related("video__organization").prefetch_related(
        "video__categories"
    )
    pagination_class = FkSchedulePagination
    permission_classes = (IsInOrganizationOrReadOnly,)
    filterset_class = ScheduleitemFilter
    ordering_fields = ["starttime"]
    ordering = ["starttime"]

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return ScheduleitemModifySerializer
        return ScheduleitemReadSerializer
