from django.shortcuts import get_object_or_404
from django_filters import rest_framework as djfilters
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied

from api.auth.permissions import IsInOrganizationOrReadOnly
from api.pagination import FkDefaultPagination
from api.program_image.serializers import (
    ProgramImageRegistrationSerializer,
    ProgramImageSerializer,
)
from fk.models import ProgramImage, Video


class ProgramImageFilter(djfilters.FilterSet):
    class Meta:
        model = ProgramImage
        fields = {"role": ["exact"]}


class ProgramImageViewSet(viewsets.ModelViewSet):
    """Editorial image metadata; image bytes are uploaded through tusd."""

    queryset = ProgramImage.objects.none()
    pagination_class = FkDefaultPagination
    filterset_class = ProgramImageFilter
    permission_classes = (IsInOrganizationOrReadOnly,)

    def get_serializer_class(self):
        if self.action == "create":
            return ProgramImageRegistrationSerializer
        return ProgramImageSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        visible_videos = Video.objects.visible_to(self.request.user)
        return ProgramImage.objects.filter(
            video_id=self.kwargs["video_id"], video__in=visible_videos
        ).select_related("video__organization")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["video_id"] = self.kwargs["video_id"]
        return context

    def perform_create(self, serializer):
        # The browser uploads to tusd. Only ingest may register a file, after
        # its privileged archive writer has published the complete image.
        if not self.request.user.is_staff:
            raise PermissionDenied("Only ingest may register archived images.")
        video = get_object_or_404(
            Video.objects.visible_to(self.request.user), pk=self.kwargs["video_id"]
        )
        data = serializer.validated_data
        image, _created = ProgramImage.objects.update_or_create(
            filename=data["filename"], defaults={**data, "video": video}
        )
        serializer.instance = image
