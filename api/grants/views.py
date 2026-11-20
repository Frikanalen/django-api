from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Prefetch, Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from api.auth.permissions import IsStaffOrReadOnly
from api.grants.filters import AirtimeGrantFilter
from api.grants.serializers import (
    AirtimeGrantCreateSerializer,
    AirtimeGrantSerializer,
    AirtimeGrantUpdateSerializer,
    GrantItemsWriteSerializer,
    PublicGrantSerializer,
    entries,
)
from api.pagination import FkDefaultPagination
from api.programming.serializers import build_recipe
from fk.models import Scheduleitem
from scheduling import broadcast_time, services
from scheduling.models import AirtimeGrant, GrantItem


def _drf(error: DjangoValidationError) -> ValidationError:
    if hasattr(error, "message_dict"):
        return ValidationError(error.message_dict)
    return ValidationError(error.messages)


class AirtimeGrantViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Airtime grants: a window of airtime given to one organization, and the
    list of what plays in it.

    Members see their organizations' grants and replace a grant's list
    while it is open (or until air, if it is exempt from locking). Staff
    see every grant, create grants, and cancel or exempt them.
    """

    queryset = AirtimeGrant.objects.none()
    serializer_class = AirtimeGrantSerializer
    pagination_class = FkDefaultPagination
    filterset_class = AirtimeGrantFilter
    permission_classes = (permissions.IsAuthenticated, IsStaffOrReadOnly)
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_permissions(self):
        if self.action == "public":
            return [permissions.AllowAny()]
        if self.action in ("items", "automatic"):
            # Per-grant rules, checked by the service layer.
            return [permissions.IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        # Everything the serializer reads, in a fixed number of queries
        # however many grants the page holds.
        queryset = AirtimeGrant.objects.select_related(
            "organization",
            "recipe__series_source__series",
            "recipe__jukebox_settings",
            "recipe__playlist_source",
        ).prefetch_related(
            Prefetch(
                "items",
                queryset=GrantItem.objects.select_related(
                    "video__organization",
                    "video__live_transmission__source",
                    "poster",
                    "graphic",
                ),
            ),
            "recipe__jukebox_settings__categories",
            # What `started_items` and `aired_items` count: the same rows
            # services.started_rows reads, one query for the whole page.
            Prefetch(
                "schedule_items",
                queryset=Scheduleitem.objects.filter(
                    starttime__lte=timezone.now() + services.ON_AIR_MARGIN
                ).order_by("starttime"),
                to_attr="prefetched_started_rows",
            ),
        )
        user = self.request.user
        if user.is_staff:
            return queryset
        return queryset.filter(
            Q(organization__editor=user) | Q(organization__members=user)
        ).distinct()

    def get_serializer_class(self):
        if self.action == "create":
            return AirtimeGrantCreateSerializer
        if self.action in ("update", "partial_update"):
            return AirtimeGrantUpdateSerializer
        return AirtimeGrantSerializer

    def _respond(self, grant, status=200):
        grant = self.get_queryset().get(pk=grant.pk)
        return Response(
            AirtimeGrantSerializer(grant, context=self.get_serializer_context()).data,
            status=status,
        )

    @extend_schema(request=AirtimeGrantCreateSerializer, responses={201: AirtimeGrantSerializer})
    def create(self, request, *args, **kwargs):
        serializer = AirtimeGrantCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data["videos"] = entries(data.pop("items", []))
        if data.get("recipe"):
            data["recipe"] = build_recipe(
                data["recipe"], organization=data["organization"], user=request.user
            )
        now = timezone.now()
        if data["endtime"] <= now:
            raise ValidationError({"endtime": "This time has already passed."})
        if data["starttime"] < now:
            # Staff fitting something in under the playhead: it starts now.
            data["starttime"] = now.replace(microsecond=0) + timedelta(seconds=1)
        # Given after its usual deadline, the airtime stays open until air,
        # so the organization can still set it up.
        if "locks_at" not in data and "lock_exempt" not in data:
            data["lock_exempt"] = broadcast_time.default_locks_at(data["starttime"]) <= now
        try:
            grant = services.create_grant(**data)
        except DjangoValidationError as error:
            raise _drf(error) from error
        return self._respond(grant, status=201)

    @extend_schema(request=AirtimeGrantUpdateSerializer, responses=AirtimeGrantSerializer)
    def update(self, request, *args, **kwargs):
        grant = self.get_object()
        serializer = AirtimeGrantUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changes = dict(serializer.validated_data)
        try:
            if changes.pop("state", None) == AirtimeGrant.State.CANCELLED:
                grant = services.cancel_grant(grant)
            if changes:
                grant = services.update_grant(grant, **changes)
        except DjangoValidationError as error:
            raise _drf(error) from error
        return self._respond(grant)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "date", OpenApiTypes.DATE, description="Europe/Oslo. Today if left out."
            ),
            OpenApiParameter(
                "days", OpenApiTypes.INT, description="Number of days. Defaults to 1."
            ),
        ],
        responses=PublicGrantSerializer(many=True),
        filters=False,
    )
    @action(detail=False, methods=["get"], pagination_class=None, filterset_class=None)
    def public(self, request):
        """
        Whose airtime it is, for everyone: the grants that touch the given
        days, with only their organization and window. The public schedule
        shows them around the programmes of /api/scheduleitems, which name
        their grant.
        """
        try:
            day = datetime.strptime(request.query_params["date"], "%Y-%m-%d").date()
        except KeyError:
            day = datetime.now(broadcast_time.TZ).date()
        except ValueError as error:
            raise ValidationError({"date": "Use YYYY-MM-DD."}) from error
        try:
            days = min(max(int(request.query_params.get("days", 1)), 1), 31)
        except ValueError as error:
            raise ValidationError({"days": "A whole number of days."}) from error
        start = datetime.combine(day, time.min, tzinfo=broadcast_time.TZ)
        grants = (
            AirtimeGrant.objects.select_related("organization")
            .exclude(state=AirtimeGrant.State.CANCELLED)
            # A suspended member's airtime compiles to nothing; it is not shown either.
            .exclude(organization__is_rogue=True)
            .filter(starttime__lt=start + timedelta(days=days), endtime__gt=start)
            .order_by("starttime")
        )
        return Response(PublicGrantSerializer(grants, many=True).data)

    @extend_schema(request=GrantItemsWriteSerializer, responses=AirtimeGrantSerializer)
    @action(detail=True, methods=["put"])
    def items(self, request, pk=None):
        """Save the chosen videos, pinned, in the order given. With
        `automatic`, the grant's recipe fills the time they leave."""
        grant = self.get_object()
        serializer = GrantItemsWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        videos = entries(serializer.validated_data["items"])
        choice = serializer.validated_data.get("recipe")
        try:
            grant = services.set_items(
                grant,
                videos,
                user=request.user,
                automatic=serializer.validated_data["automatic"],
                recipe=build_recipe(choice, organization=grant.organization, user=request.user)
                if choice
                else services.KEEP,
                whole_slot=serializer.validated_data["whole_slot"],
            )
        except DjangoValidationError as error:
            raise _drf(error) from error
        return self._respond(grant)

    @extend_schema(request=None, responses=AirtimeGrantSerializer)
    @action(detail=True, methods=["post"])
    def automatic(self, request, pk=None):
        """Unpin everything and let the grant's recipe program the whole window."""
        grant = self.get_object()
        try:
            grant = services.make_automatic(grant, user=request.user)
        except DjangoValidationError as error:
            raise _drf(error) from error
        return self._respond(grant)
