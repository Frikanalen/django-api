from django.db.models import Prefetch
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied

from agenda.scheduling import policy
from api.auth.permissions import IsInOrganizationOrReadOnly, RequireTargetOrganizationMembership
from api.pagination import FkSchedulePagination
from api.schedule.filters import ScheduleitemFilter
from api.schedule.serializers import ScheduleitemModifySerializer, ScheduleitemReadSerializer
from fk.models import Scheduleitem, VideoFile


class ScheduleitemViewSet(RequireTargetOrganizationMembership, viewsets.ModelViewSet):
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

    # Eagerly load the nested relations exposed by ScheduleitemReadSerializer.
    queryset = Scheduleitem.objects.select_related("video__organization").prefetch_related(
        "video__categories",
        Prefetch(
            "video__videofile_set",
            queryset=VideoFile.objects.select_related("format"),
        ),
    )
    pagination_class = FkSchedulePagination
    permission_classes = (IsInOrganizationOrReadOnly,)
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

    def perform_destroy(self, instance):
        # Create and update enforce the freeze in the serializer; delete
        # never reaches one, so the check lives here.
        if not self.request.user.is_staff and policy.is_frozen(instance.starttime):
            raise PermissionDenied(policy.freeze_message())
        instance.delete()
