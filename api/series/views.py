from django.db.models import Count
from django.db.models.deletion import ProtectedError
from django_filters import rest_framework as filters
from rest_framework import generics
from rest_framework.exceptions import ValidationError

from api.auth.permissions import (
    IsInOrganizationOrReadOnly,
    RequireTargetOrganizationMembership,
)
from api.pagination import FkDefaultPagination
from api.series.serializers import SeriesSerializer, SeriesWriteSerializer
from fk.models import Series


class SeriesFilter(filters.FilterSet):
    class Meta:
        model = Series
        fields = {
            "organization": ["exact"],
            "name": ["exact", "icontains"],
        }


class SeriesList(RequireTargetOrganizationMembership, generics.ListCreateAPIView):
    """List public series or create one for an organization you administer."""

    serializer_class = SeriesSerializer
    pagination_class = FkDefaultPagination
    filterset_class = SeriesFilter
    permission_classes = (IsInOrganizationOrReadOnly,)

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SeriesWriteSerializer
        return SeriesSerializer

    def get_queryset(self):
        return (
            Series.objects.visible_to(self.request.user)
            .select_related("organization", "organization__editor")
            .annotate(episode_count=Count("videos"))
        )


class SeriesDetail(RequireTargetOrganizationMembership, generics.RetrieveUpdateDestroyAPIView):
    """Read a series, edit its metadata, or delete it while it has no episodes."""

    serializer_class = SeriesSerializer
    permission_classes = (IsInOrganizationOrReadOnly,)

    def get_serializer_class(self):
        if self.request.method in ("PUT", "PATCH"):
            return SeriesWriteSerializer
        return SeriesSerializer

    def get_queryset(self):
        return (
            Series.objects.visible_to(self.request.user)
            .select_related("organization", "organization__editor")
            .annotate(episode_count=Count("videos"))
        )

    def perform_destroy(self, instance):
        try:
            super().perform_destroy(instance)
        except ProtectedError as error:
            raise ValidationError(
                {"series": "Remove every episode from this series before deleting it."}
            ) from error
