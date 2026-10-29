from django_filters import rest_framework as djfilters
from rest_framework import viewsets

from api.auth.permissions import IsInOrganizationOrReadOnly, RequireTargetOrganizationMembership
from api.pagination import FkDefaultPagination
from api.videofile.serializers import VideoFileSerializer
from fk.models import VideoFile


class VideoFileFilter(djfilters.FilterSet):
    created_time = djfilters.DateTimeFromToRangeFilter()

    class Meta:
        model = VideoFile
        fields = {
            "video_id": ["exact"],
            "variant": ["exact"],
            "integrated_lufs": ["isnull"],
            # `lt` is ingest's planning query: everything produced by a
            # profile older than the current revision, which is stale and
            # wants rebuilding. It needs no special handling because the
            # column is NOT NULL with 0 for "predates tracking" -- so the
            # pre-tracking rows sort below every real revision and fall
            # inside the comparison rather than out of it.
            "profile_revision": ["exact", "lt"],
        }


class VideoFileViewSet(RequireTargetOrganizationMembership, viewsets.ModelViewSet):
    """
    Video file list and detail endpoint.
    """

    queryset = VideoFile.objects.all()
    serializer_class = VideoFileSerializer
    pagination_class = FkDefaultPagination
    filterset_class = VideoFileFilter
    permission_classes = (IsInOrganizationOrReadOnly,)
