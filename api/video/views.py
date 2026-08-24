from hmac import compare_digest

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F
from django.db.models.functions import Greatest
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
    IngestClaimSerializer,
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


class IngestClaim(generics.GenericAPIView):
    """Hand one waiting job to one worker.

    The counterpart to the reporting endpoint above: that one is how a
    worker says what it is doing, this one is how it finds out what to
    do. Workers poll it, so an empty queue is the ordinary case rather
    than an error -- it answers 204, and the worker sleeps and asks
    again. A 404 would say the endpoint was wrong, and an empty list
    would make every caller unwrap a collection that can only ever hold
    one thing.

    Claiming also covers recovery. A job whose worker was killed
    mid-transcode stops reporting but keeps its state, and nothing else
    in the system would ever look at it again; here it becomes claimable
    once its lease expires. See IngestJob.claim().
    """

    serializer_class = IngestClaimSerializer
    permission_classes = (IngestJobPermission,)

    @extend_schema(
        operation_id="ingest_claim",
        summary="Claim an ingest job",
        description=(
            "Atomically hands the caller the highest-priority claimable job and moves it "
            "to `probing`. Claimable means waiting, or claimed by a worker that has not "
            "reported for longer than the ingest lease. Concurrent callers are never given "
            "the same job. An idle queue answers 204."
        ),
        request=IngestClaimSerializer,
        responses={
            200: IngestJobSerializer,
            204: OpenApiResponse(description="Nothing is claimable right now."),
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        job = IngestJob.claim(
            kind=serializer.validated_data.get("kind"),
            worker=serializer.validated_data.get("worker", ""),
        )
        if job is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(IngestJobSerializer(job).data)


class VideoFilter(djfilters.FilterSet):
    categories__name__icontains = djfilters.ModelMultipleChoiceFilter(
        field_name="categories__name",
        to_field_name="name",
        lookup_expr="icontains",
        queryset=Category.objects.all(),
    )
    created_time = djfilters.DateTimeFromToRangeFilter()
    # Declared rather than left to Meta so the default can be stated: a
    # caller who says nothing gets the public catalogue, which is what the
    # list has always meant. Saying `false` now means what it reads as --
    # only the videos ingest never finished -- rather than "everything".
    proper_import = djfilters.BooleanFilter(
        label="Whether ingest finished. Omitted means true.",
    )
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
            "series": ["exact"],
        }

    def filter_search(self, queryset, name, value):
        if not value.strip():
            return queryset

        # `websearch` accepts ordinary web-style input, including quoted
        # phrases, and deliberately never treats malformed input as SQL
        # syntax. The vectors are generated columns backed by GIN indexes.
        query = SearchQuery(value, config="norwegian", search_type="websearch")
        video_rank = SearchRank(F("search_document"), query, cover_density=True)

        # A caller who has named the organization already knows whose
        # videos these are, so matching its name here only dilutes the
        # result: a query that happened to match it would hand back the
        # organization's whole catalogue rather than the videos the words
        # actually appear in. `organization`'s own filter is what narrows
        # the rows; dropping the name vector leaves no cross-table OR to
        # defeat the index either, so this is a single GIN scan instead of
        # the UNION below.
        if self.form.cleaned_data.get("organization"):
            return (
                queryset.filter(search_document=query)
                .annotate(search_rank=video_rank)
                .order_by("-search_rank", "-id")
            )

        # Matching ids are collected via UNION rather than a single
        # `Q(search_document=query) | Q(organization__search_document=query)`.
        # That single-query form (including the `organization__in=` subquery
        # variant) puts both tsvector checks behind one join, and Postgres
        # can't decompose an OR spanning two tables back into per-table
        # index scans -- it falls back to scanning every video row
        # regardless of table size. Each UNION branch is planned
        # independently, so each runs against its own GIN index.
        #
        # `.order_by()` clears the `id` ordering each branch would
        # otherwise inherit from Video's Meta -- sorting by id here is
        # thrown away the moment the ids reach `pk__in` below, but Postgres
        # doesn't know that and sorts each branch anyway. `union(all=True)`
        # skips deduplication for the same reason: `pk__in` doesn't care
        # whether its right-hand side has duplicates, so paying to dedupe
        # here buys nothing.
        own_match = Video.objects.filter(search_document=query).order_by().values("pk")
        organization_match = (
            Video.objects.filter(organization__search_document=query).order_by().values("pk")
        )
        matching_ids = own_match.union(organization_match, all=True)

        organization_rank = SearchRank(
            F("organization__search_document"), query, cover_density=True
        )
        return (
            queryset.filter(pk__in=matching_ids)
            .annotate(search_rank=Greatest(video_rank, organization_rank))
            .order_by("-search_rank", "-id")
        )

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        # An absent `proper_import` is not "no opinion": the catalogue is
        # the finished videos, and only an explicit value opens it up.
        if self.form.cleaned_data.get("proper_import") is None:
            queryset = queryset.filter(proper_import=True)
        return queryset


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

    `organization` - Frikanalen ID of organization behind video.  Given
                     alongside `q`, it also narrows the search to that
                     organization's own videos: the organization's name
                     stops counting as a match, since it would otherwise
                     return the whole catalogue.

    `played_count_web` - the number of times this video was played on the web

    `played_count_web__gt` - greater than

    `played_count_web__gte` - greater than or equal

    `played_count_web__lt`  - less than

    `played_count_web__lte` - less than or equal

    `publish_on_web` - if this video is published ont the web (true/false)

    `proper_import` - whether ingest finished (true/false).  Omitted, the
                      list shows only finished videos, which is the public
                      catalogue.

    `ref_url` - the exact reference url

    `ref_url__startswith` - the reference url start with this string

    `ref_url__icontains` - the reference url contain this string

    """

    queryset = Video.objects.all()
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
        # `proper_import` is VideoFilter's business now, so that it reaches
        # the OpenAPI schema like every other filter instead of being read
        # off the query string here where no generated client can find it.
        return Video.objects.visible_to(self.request.user)
