from django.contrib.postgres.search import SearchQuery, SearchRank
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import F, Prefetch
from django.db.models.functions import Greatest
from django.http import Http404
from django_filters import rest_framework as djfilters
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_field
from rest_framework import generics, status
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from api.auth.permissions import (
    IngestJobPermission,
    IsInOrganizationOrDisallow,
    IsInOrganizationOrReadOnly,
    RequireTargetOrganizationMembership,
    can_administer_organization,
)
from api.grants.views import _drf
from api.pagination import FkDefaultPagination
from api.video.serializers import (
    IngestClaimSerializer,
    IngestJobSerializer,
    VideoCreateSerializer,
    VideoMediaMetadataSerializer,
    VideoSerializer,
    VideoUploadSerializer,
    coming_live_airings,
)
from fk.models import Category, IngestJob, Organization, Video
from scheduling import services as scheduling


def _with_live_airings(queryset):
    """Fetch every live video's coming airings in one query for the lot."""
    return queryset.prefetch_related(
        Prefetch("scheduleitem_set", queryset=coming_live_airings(), to_attr="coming_live_airings")
    )


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
        return _with_live_airings(Video.objects.visible_to(self.request.user))

    @extend_schema(
        responses={
            200: VideoSerializer,
            403: OpenApiResponse(
                description=(
                    "The video is withheld. Either staff have removed it pending review: "
                    '`{"detail", "code": "video_removed"}`, deliberately with no reason, plus '
                    '`"organization": {"id", "name"}` when the caller is in its organization; '
                    "or it belongs to a suspended organization, which the body names: "
                    '`{"detail", "code": "organization_suspended", "organization": {"id", "name"}}`.'
                )
            ),
        }
    )
    def retrieve(self, request, *args, **kwargs):
        try:
            return super().retrieve(request, *args, **kwargs)
        except Http404:
            # A removed video is withheld, not missing. To the public, say
            # only that it is unavailable; the reason stays with staff, and
            # so does whose video it was. Its own organization is told a
            # little more, and which organization, so its page can say so.
            removed = (
                Video.objects.filter(pk=self.kwargs.get("pk"), is_banned=True)
                .select_related("organization")
                .first()
            )
            if removed is not None:
                if can_administer_organization(request.user, removed.organization):
                    body = {
                        "detail": "Videoen er administrativt fjernet i påvente av gjennomgang.",
                        "code": "video_removed",
                        "organization": {
                            "id": removed.organization.pk,
                            "name": removed.organization.name,
                        },
                    }
                else:
                    body = {"detail": "Denne videoen er utilgjengelig.", "code": "video_removed"}
                return Response(body, status=status.HTTP_403_FORBIDDEN)
            # A suspended organization's video is withheld, not missing:
            # say whose it was, so the page can show the suspension notice.
            organization = (
                Organization.objects.filter(is_rogue=True, video__pk=self.kwargs.get("pk"))
                .values("id", "name")
                .first()
            )
            if organization is None:
                raise
            return Response(
                {
                    "detail": "Organisasjonen er suspendert.",
                    "code": "organization_suspended",
                    "organization": organization,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

    def perform_destroy(self, instance):
        # Takes the video out of the grants that list it, and off the
        # schedule, refilling what it leaves; see scheduling.services.
        try:
            scheduling.delete_video(instance)
        except DjangoValidationError as error:
            raise _drf(error) from error


class VideoUpload(generics.RetrieveAPIView):
    """Where to upload this video's file, for those allowed to.

    Answering at all is the authorization: ingest asks this endpoint on
    the uploader's behalf, with the uploader's own session cookie or API
    token, before it accepts an upload. Allowed are staff and the
    members of the video's organization, for a video they can see; a
    removed video or a suspended organization's takes no uploads.
    """

    serializer_class = VideoUploadSerializer
    permission_classes = (IsInOrganizationOrDisallow,)

    def get_queryset(self):
        return Video.objects.visible_to(self.request.user)

    @extend_schema(
        operation_id="videos_upload_retrieve",
        summary="Get a video's upload endpoint",
        responses={
            200: VideoUploadSerializer,
            401: OpenApiResponse(description="Not signed in."),
            403: OpenApiResponse(description="Not allowed to upload to this video."),
            404: OpenApiResponse(description="No such video, or not one the caller can see."),
        },
    )
    def get(self, request, *args, **kwargs):
        return Response(self.get_serializer(self.get_object()).data)


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


class VideoMediaMetadataDetail(generics.GenericAPIView):
    """The ffprobe report on a video's original, written by ingest.

    Same audiences as the ingest job beside it: ingest writes, the
    organization behind the video reads. The probe names archive paths
    and encoder tags that are no business of anyone else's.

    Plain JSON in both directions, deliberately bypassing the camelCase
    renderer and parser the rest of the API uses. They rewrite every key
    they meet, recursively, so `codec_name` would be stored as
    `codecName` -- no longer what ffprobe says, and no longer comparable
    with a fresh probe of the same file.
    """

    queryset = Video.objects.all()
    serializer_class = VideoMediaMetadataSerializer
    permission_classes = (IngestJobPermission,)
    renderer_classes = (JSONRenderer,)
    parser_classes = (JSONParser,)

    def get_object(self) -> Video:
        video = generics.get_object_or_404(Video.objects.all(), pk=self.kwargs["pk"])
        self.check_object_permissions(self.request, video)
        return video

    @extend_schema(
        operation_id="videos_media_metadata_retrieve",
        summary="Read a video's media metadata",
        description=(
            "The ffprobe report on the video's original file, keys exactly as ffprobe "
            "prints them. `version` 0 with empty `data` means nothing has probed it yet."
        ),
    )
    def get(self, request, *args, **kwargs):
        return Response(self.get_serializer(self.get_object()).data)

    @extend_schema(
        operation_id="videos_media_metadata_update",
        summary="Record a video's media metadata",
        description=(
            "Replaces the stored ffprobe report with the one given. Reserved for the "
            "ingest service. Keys inside `data` are stored exactly as sent."
        ),
    )
    def put(self, request, *args, **kwargs):
        video = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        video.media_metadata = dict(serializer.validated_data)
        # A queryset update rather than save(): only this column is written,
        # so a concurrent edit to the rest of the row survives, and
        # `updated_time` is left alone. A probe describes the file, not the
        # programme, and a catalogue-wide refill must not make every video
        # look freshly edited to whatever watches that timestamp.
        Video.objects.filter(pk=video.pk).update(media_metadata=video.media_metadata)
        return Response(self.get_serializer(video).data)


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
    categories__name__icontains = extend_schema_field(OpenApiTypes.STR)(
        djfilters.ModelMultipleChoiceFilter(
            field_name="categories__name",
            to_field_name="name",
            lookup_expr="icontains",
            queryset=Category.objects.all(),
        )
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
        #
        # Everything VideoSerializer reads off a video beyond its own columns
        # is fetched up front, so a page costs the same handful of queries
        # whatever its length instead of seven more per video.
        queryset = (
            Video.objects.visible_to(self.request.user)
            .select_related("creator", "organization__editor", "series", "live_transmission")
            .prefetch_related("categories", "videofile_set")
        )
        return _with_live_airings(queryset)
