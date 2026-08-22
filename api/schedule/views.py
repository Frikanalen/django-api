from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from agenda.scheduling import policy
from api.auth.permissions import (
    CanScheduleForOrganizationOrReadOnly,
    RequireSchedulingEligibility,
)
from api.pagination import FkSchedulePagination
from api.schedule.filters import ScheduleitemFilter
from api.schedule.serializers import (
    ScheduleitemModifySerializer,
    ScheduleitemReadSerializer,
    SchedulingPolicySerializer,
)
from fk.models import Scheduleitem, WeeklySlot


class ScheduleitemViewSet(RequireSchedulingEligibility, viewsets.ModelViewSet):
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
    # The files themselves still need prefetching; their format does not,
    # now that it is a column on the row rather than a table to join.
    queryset = Scheduleitem.objects.select_related("video__organization").prefetch_related(
        "video__categories",
        "video__videofile_set",
    )
    pagination_class = FkSchedulePagination
    permission_classes = (CanScheduleForOrganizationOrReadOnly,)
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
        # Create and update enforce the window in the serializer; delete
        # never reaches one, so the check lives here.
        if not self.request.user.is_staff and not policy.is_open_airtime(
            instance.starttime, instance.endtime
        ):
            raise PermissionDenied(policy.scheduling_window_message())
        instance.delete()


class SchedulingPolicyView(APIView):
    """Broadcast-week boundaries and recurring weekly reservations."""

    permission_classes = (permissions.AllowAny,)

    @extend_schema(
        operation_id="scheduling_policy_retrieve",
        summary="Broadcast-week scheduling policy",
        description=(
            "The instants that divide the broadcast schedule into its three "
            "states. Every broadcast week runs Monday 00:00 to Monday 00:00, "
            "Europe/Oslo, and is drafted two Mondays before it airs, open for "
            "member changes for one week, then frozen from the Monday before "
            "airing.\n\n"
            "Derive per-item state by comparing `starttime` against these "
            "boundaries: before `freezeBoundary` the item is frozen; from "
            "`freezeBoundary` up to `schedulingHorizon` it is in the open week, "
            "where an item marked `displaceable` can be replaced by a member "
            "organization's own pick; beyond `schedulingHorizon` nothing is "
            "drafted yet. The values only change on Mondays at midnight "
            "Europe/Oslo. Staff accounts are exempt from the freeze. "
            "`weeklySlots` describes recurring reserved airtime whether or "
            "not its concrete programme has been drafted yet."
        ),
        responses=SchedulingPolicySerializer,
    )
    def get(self, request):
        now = timezone.now()
        serializer = SchedulingPolicySerializer(
            {
                "freeze_boundary": policy.freeze_boundary(now),
                "scheduling_horizon": policy.scheduling_horizon(now),
                "server_time": now,
                "weekly_slots": WeeklySlot.objects.select_related("source").all(),
            }
        )
        return Response(serializer.data)
