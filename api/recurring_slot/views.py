from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions
from rest_framework.response import Response

from api.auth.permissions import IsStaffOrReadOnly
from api.grant_request.views import REQUEST_FILTERS, filter_requests
from api.pagination import FkDefaultPagination
from api.recurring_slot.serializers import (
    RecurringSlotCreationRequestSerializer,
    RecurringSlotOwnershipRequestSerializer,
    RecurringSlotRequestSerializer,
    RecurringSlotSerializer,
)
from scheduling.models import (
    RecurringSlot,
    RecurringSlotCreationRequest,
    RecurringSlotOwnershipRequest,
)


def administered_organizations(user):
    return Q(organization__editor=user) | Q(organization__members=user)


class RecurringSlotList(generics.ListCreateAPIView):
    """Every recurring slot. Public, like the schedule it produces;
    staff create them."""

    queryset = RecurringSlot.objects.select_related(
        "organization",
        "recipe__series_source__series",
        "recipe__jukebox_settings",
        "recipe__playlist_source",
    )
    serializer_class = RecurringSlotSerializer
    pagination_class = FkDefaultPagination
    permission_classes = (IsStaffOrReadOnly,)


class RecurringSlotDetail(generics.RetrieveUpdateDestroyAPIView):
    """Staff change or delete a slot. Grants it already issued stay as
    they are, without their origin once the slot is deleted."""

    queryset = RecurringSlot.objects.select_related(
        "organization",
        "recipe__series_source__series",
        "recipe__jukebox_settings",
        "recipe__playlist_source",
    )
    serializer_class = RecurringSlotSerializer
    permission_classes = (IsStaffOrReadOnly,)


class RecurringSlotRequestList(generics.ListAPIView):
    """One chronological read view over both request models."""

    filter_backends = ()
    # list() deliberately combines two model types instead of using DRF's
    # queryset machinery. This inert queryset only gives schema introspection
    # a model without changing the endpoint's runtime behavior.
    queryset = RecurringSlotCreationRequest.objects.none()
    serializer_class = RecurringSlotRequestSerializer
    pagination_class = FkDefaultPagination
    permission_classes = (permissions.IsAuthenticated,)

    def _creation_requests(self):
        queryset = RecurringSlotCreationRequest.objects.select_related(
            "organization", "requested_by", "reviewed_by", "slot"
        )
        return filter_requests(queryset, self.request)

    def _ownership_requests(self):
        queryset = RecurringSlotOwnershipRequest.objects.select_related(
            "organization",
            "requested_by",
            "slot",
            "previous_organization",
            "reviewed_by",
        )
        return filter_requests(queryset, self.request)

    @extend_schema(parameters=REQUEST_FILTERS)
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        requests = sorted(
            [*self._creation_requests(), *self._ownership_requests()],
            key=lambda slot_request: (slot_request.created_at, slot_request.pk),
            reverse=True,
        )
        page = self.paginate_queryset(requests)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(self.get_serializer(requests, many=True).data)


class RecurringSlotCreationRequestCreate(generics.CreateAPIView):
    serializer_class = RecurringSlotCreationRequestSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)


class RecurringSlotOwnershipRequestCreate(generics.CreateAPIView):
    serializer_class = RecurringSlotOwnershipRequestSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)
