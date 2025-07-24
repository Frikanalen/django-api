from django_filters import rest_framework as djfilters

from api.auth.permissions import IsInOrganizationOrReadOnly
from api.videofile.serializers import VideoFileSerializer
from api.views import Pagination
from fk.models import VideoFile


from rest_framework import viewsets


class VideoFileFilter(djfilters.FilterSet):
    created_time = djfilters.DateTimeFromToRangeFilter()

    class Meta:
        model = VideoFile
        fields = {
            "video__id": ["exact"],
            "format": ["exact"],
            "integrated_lufs": ["exact", "gt", "gte", "lt", "lte", "isnull"],
            "truepeak_lufs": ["exact", "gt", "gte", "lt", "lte", "isnull"],
        }


class VideoFileViewSet(viewsets.ModelViewSet):
    """
    Video file list and detail endpoint.
    """

    queryset = VideoFile.objects.all()
    serializer_class = VideoFileSerializer
    pagination_class = Pagination
    filterset_class = VideoFileFilter
    permission_classes = (IsInOrganizationOrReadOnly,)
