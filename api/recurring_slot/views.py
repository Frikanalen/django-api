from django.db.models import Q
from rest_framework import generics, permissions
from rest_framework.response import Response

from api.pagination import FkDefaultPagination
from api.recurring_slot.serializers import (
    RecurringSlotCreationRequestSerializer,
    RecurringSlotOwnershipRequestSerializer,
    RecurringSlotRequestSerializer,
    RecurringSlotSerializer,
)
from fk.models import RecurringSlotCreationRequest, RecurringSlotOwnershipRequest
from scheduling.models import RecurringSlot


def administered_organizations(user):
    return Q(organization__editor=user) | Q(organization__members=user)


class RecurringSlotList(generics.ListAPIView):
    """Every recurring slot. Public, like the schedule it produces."""

    queryset = RecurringSlot.objects.select_related("default_fill_rule")
    serializer_class = RecurringSlotSerializer
    pagination_class = FkDefaultPagination
    permission_classes = (permissions.AllowAny,)


class RecurringSlotDetail(generics.RetrieveAPIView):
    queryset = RecurringSlot.objects.select_related("default_fill_rule")
    serializer_class = RecurringSlotSerializer
    permission_classes = (permissions.AllowAny,)


class RecurringSlotRequestList(generics.ListAPIView):
    """One chronological read view over both request models."""

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
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(administered_organizations(self.request.user)).distinct()

    def _ownership_requests(self):
        queryset = RecurringSlotOwnershipRequest.objects.select_related(
            "organization",
            "requested_by",
            "slot",
            "previous_organization",
            "reviewed_by",
        )
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(administered_organizations(self.request.user)).distinct()

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
