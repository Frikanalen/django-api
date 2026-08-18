from hmac import compare_digest

from django.db.models import Q
from django_filters import rest_framework as djfilters
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from api.auth.permissions import (
    IngestJobPermission,
    IsInOrganizationOrDisallow,
    IsInOrganizationOrReadOnly,
    RequireTargetOrganizationMembership,
)
from api.pagination import FkDefaultPagination
from api.video.serializers import (
    IngestJobSerializer,
    UploadTokenVerificationSerializer,
    VideoCreateSerializer,
    VideoSerializer,
    VideoUploadTokenSerializer,
)
from fk.models import Category, IngestJob, Video


class VideoDetail(generics.RetrieveUpdateDestroyAPIView):
    """
    Video details
    """

    queryset = Video.objects.all()
    serializer_class = VideoSerializer
    permission_classes = (IsInOrganizationOrReadOnly,)

    def get_queryset(self):
        # Videos of an organization without an ansvarlig redaktor are
        # staff-only until one is appointed; see OrganizationQuerySet.
        return Video.objects.visible_to(self.request.user)


class VideoUploadTokenDetail(generics.RetrieveAPIView):
    """
    Video details
    """

    queryset = Video.objects.all()
    serializer_class = VideoUploadTokenSerializer
    permission_classes = (IsInOrganizationOrDisallow,)


class VideoUploadTokenVerification(generics.GenericAPIView):
    """Verify an upload capability without disclosing the token itself."""

    queryset = Video.objects.all()
    serializer_class = UploadTokenVerificationSerializer

    @extend_schema(
        operation_id="videos_upload_token_verify",
        summary="Verify an upload token",
        description=(
            "Confirms that `uploadToken` authorizes an upload for this video. "
            "An invalid token deliberately produces the same response as an unknown video."
        ),
        request=UploadTokenVerificationSerializer,
        responses={
            204: OpenApiResponse(description="The upload token is valid."),
            404: OpenApiResponse(
                description="The video does not exist or the upload token is invalid."
            ),
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        video = self.get_object()

        # Treat a bad capability exactly like a missing video.  This avoids
        # exposing either fact to callers that do not already hold the token.
        if not compare_digest(video.upload_token, serializer.validated_data["upload_token"]):
            raise NotFound()

        return Response(status=status.HTTP_204_NO_CONTENT)


class VideoIngestJobDetail(generics.RetrieveUpdateAPIView):
    """Where an upload has got to, reported by ingest and read by its uploader.

    Reading and writing are two different audiences here: ingest declares
    the state it is in, and the organization behind the video finds out
    what became of the file it sent. Nobody else gets either.
    """

    queryset = Video.objects.all()
    serializer_class = IngestJobSerializer
    permission_classes = (IngestJobPermission,)
    # No PATCH: a partial report invites the half-updated row where the
    # state has moved on but the percentage has not. A whole-state PUT is
    # also what makes ingest's retries free of consequence.
    http_method_names = ["get", "put", "head", "options"]

    def get_object(self) -> IngestJob:
        video = generics.get_object_or_404(Video.objects.all(), pk=self.kwargs["pk"])
        # Unsaved when ingest has never reported; saving it is the PUT's
        # business, and a reader must not create rows by looking.
        job = IngestJob.for_video(video)
        self.check_object_permissions(self.request, job)
        return job

    @extend_schema(
        operation_id="videos_ingest_retrieve",
        summary="Read a video's ingest state",
        description=(
            "How far ingest has got with the video's uploaded file. Videos that were "
            "ingested before this endpoint existed report `done`; videos nothing has "
            "uploaded to report `pending`."
        ),
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        operation_id="videos_ingest_report",
        summary="Report a video's ingest state",
        description=(
            "Replaces the video's ingest state with the one given. Reserved for the "
            "ingest service; the whole state is sent every time, so a retried report "
            "is indistinguishable from the first."
        ),
    )
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)


class VideoFilter(djfilters.FilterSet):
    categories__name__icontains = djfilters.ModelMultipleChoiceFilter(
        field_name="categories__name",
        to_field_name="name",
        lookup_expr="icontains",
        queryset=Category.objects.all(),
    )
    created_time = djfilters.DateTimeFromToRangeFilter()
    updated_time = djfilters.DateTimeFromToRangeFilter()
    uploaded_time = djfilters.DateTimeFromToRangeFilter()
    q = djfilters.CharFilter(method="filter_search", label="Free-text search")

    class Meta:
        model = Video
        fields = {
            "duration": ["exact", "gt", "gte", "lt", "lte"],
            "creator__email": ["exact"],
            "framerate": ["exact"],
            "has_tono_records": ["exact"],
            "is_filler": ["exact"],
            "name": ["exact", "icontains"],
            "organization": ["exact"],
            "played_count_web": ["exact", "gt", "gte", "lt", "lte"],
            "publish_on_web": ["exact"],
            "ref_url": ["exact", "startswith", "icontains"],
        }

    def filter_search(self, queryset, name, value):
        terms = self.normalize_query(value)
        queries = [
            Q(name__icontains=term)
            | Q(description__icontains=term)
            | Q(organization__name__icontains=term)
            | Q(header__icontains=term)
            for term in terms
        ]
        query = queries.pop()
        for item in queries:
            query &= item
        return queryset.filter(query).order_by("-id")

    @staticmethod
    def normalize_query(query_string):
        """Split the query string into individual keywords, grouping quoted terms."""
        import shlex

        return shlex.split(query_string)


class VideoList(RequireTargetOrganizationMembership, generics.ListCreateAPIView):
    """
    List of videos

    Query parameters
    ----------------

    `q` - Free search query.

    `ordering` - Order results by specified field.  Prepend a minus for
                 descending order.  I.e. `?ordering=-id`.

    `creator__email` - the email of the video's creator

    `framerate` - the framerate in hz * 1000

    `has_tono_records` - if the tono flag is set (true/false)

    `is_filler` - if this is a filler video (true/false)

    `name` - the exact name/title of the video

    `name__icontains` - substring is part of name/title of the video

    `organization` - Frikanalen ID of organization behind video

    `played_count_web` - the number of times this video was played on the web

    `played_count_web__gt` - greater than

    `played_count_web__gte` - greater than or equal

    `played_count_web__lt`  - less than

    `played_count_web__lte` - less than or equal

    `publish_on_web` - if this video is published ont the web (true/false)

    `proper_import` - if the uploaded video was properly imported (true/false)

    `ref_url` - the exact reference url

    `ref_url__startswith` - the reference url start with this string

    `ref_url__icontains` - the reference url contain this string

    """

    queryset = Video.objects.filter(proper_import=True)
    pagination_class = FkDefaultPagination
    filterset_class = VideoFilter
    permission_classes = (IsInOrganizationOrReadOnly,)
    ordering_fields = [
        f.column for f in Video._meta.fields if f.column in VideoSerializer.Meta().fields
    ]

    def get_serializer_class(self):
        if hasattr(self.request, "method") and self.request.method in ["POST", "PUT", "PATCH"]:
            return VideoCreateSerializer
        return VideoSerializer

    def get_queryset(self):
        # Can filtering on proper_import be done using a different
        # queryset and VideoFilter?
        queryset = Video.objects.visible_to(self.request.user)
        proper_import = self.request.query_params.get("properImport")
        if proper_import and "false" == proper_import:
            return queryset
        return queryset.filter(proper_import=True)
