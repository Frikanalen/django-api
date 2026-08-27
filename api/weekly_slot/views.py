from django.db.models import Q
from rest_framework import generics, permissions
from rest_framework.response import Response

from api.pagination import FkDefaultPagination
from api.weekly_slot.permissions import CanModifyWeeklySlot
from api.weekly_slot.serializers import (
    WeeklySlotCreationRequestSerializer,
    WeeklySlotOwnershipRequestSerializer,
    WeeklySlotRequestSerializer,
    WeeklySlotSerializer,
    WeeklySlotSourceSerializer,
)
from fk.models import (
    WeeklySlot,
    WeeklySlotCreationRequest,
    WeeklySlotOwnershipRequest,
    WeeklySlotSource,
)


def administered_organizations(user):
    return Q(organization__editor=user) | Q(organization__members=user)


class WeeklySlotSourceList(generics.ListCreateAPIView):
    serializer_class = WeeklySlotSourceSerializer
    pagination_class = FkDefaultPagination
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        queryset = WeeklySlotSource.objects.select_related("organization").prefetch_related(
            "direct_videos"
        )
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(administered_organizations(self.request.user)).distinct()


class WeeklySlotSourceDetail(generics.RetrieveUpdateAPIView):
    serializer_class = WeeklySlotSourceSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        queryset = WeeklySlotSource.objects.select_related("organization").prefetch_related(
            "direct_videos"
        )
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(administered_organizations(self.request.user)).distinct()


class WeeklySlotDetail(generics.RetrieveUpdateAPIView):
    serializer_class = WeeklySlotSerializer
    permission_classes = (CanModifyWeeklySlot,)

    def get_queryset(self):
        return WeeklySlot.objects.select_related("organization", "source")


class WeeklySlotRequestList(generics.ListAPIView):
    """One chronological read view over both request models."""

    serializer_class = WeeklySlotRequestSerializer
    pagination_class = FkDefaultPagination
    permission_classes = (permissions.IsAuthenticated,)

    def _creation_requests(self):
        queryset = WeeklySlotCreationRequest.objects.select_related(
            "organization", "requested_by", "reviewed_by", "weekly_slot"
        )
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(administered_organizations(self.request.user)).distinct()

    def _ownership_requests(self):
        queryset = WeeklySlotOwnershipRequest.objects.select_related(
            "organization",
            "requested_by",
            "weekly_slot",
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


class WeeklySlotCreationRequestCreate(generics.CreateAPIView):
    serializer_class = WeeklySlotCreationRequestSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)


class WeeklySlotOwnershipRequestCreate(generics.CreateAPIView):
    serializer_class = WeeklySlotOwnershipRequestSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)
