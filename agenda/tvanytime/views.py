"""HTTP surface for the NorDig TV-Anytime feed.

NorDig recommends distributing this metadata by pull, from "a public area
where the latest and most updated information is available"
(Metadata Exchange format specification 1.3, 2.11). So: anonymous GET, no
pagination, one document per window, and nothing a distributor has to
authenticate or negotiate to fetch on a timer.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import permissions, renderers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from fk.models import Scheduleitem

from . import document

OSLO = ZoneInfo("Europe/Oslo")

DEFAULT_DAYS = 7
# A window wide enough for any planning horizon a distributor has -- the
# schedule itself is only drafted three weeks out -- while keeping one
# request to one bounded scan.
MAX_DAYS = 31


class TVAnytimeRenderer(renderers.BaseRenderer):
    """Passes the already-serialized document straight through.

    The XML is built and encoded by `document`, so there is nothing for a
    renderer to do but declare the media type. `charset = None` keeps DRF
    from re-encoding bytes that already carry an XML declaration.
    """

    media_type = "application/xml"
    format = "xml"
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


def schedule_queryset():
    """Schedule items with everything the document builder reads.

    Every relation here is dereferenced once per item or per video while
    building; without them a week of programming is a few thousand
    queries. `organization__editor` is included because whether an
    organization has an active ansvarlig redaktør decides if its videos
    may be offered on demand.
    """
    return Scheduleitem.objects.select_related(
        "video__organization__editor",
    ).prefetch_related(
        "video__categories",
        "video__videofile_set",
    )


def _requested_days(request) -> int:
    raw = request.query_params.get("days")
    if raw is None:
        return DEFAULT_DAYS
    try:
        days = int(raw)
    except ValueError:
        raise ValidationError({"days": "Must be a whole number of days."}) from None
    if not 1 <= days <= MAX_DAYS:
        raise ValidationError({"days": f"Must be between 1 and {MAX_DAYS}."})
    return days


def _render(request, start_date: date, days: int) -> Response:
    # The window is computed here rather than read back out of by_day() so
    # that the bounds published on the Schedule element are the same ones
    # the query filtered on.
    window_start = datetime.combine(start_date, time.min, tzinfo=OSLO)
    window_end = window_start + timedelta(days=days)
    items = schedule_queryset().by_day(start_date, days=days)
    root = document.build(
        items,
        window_start=window_start,
        window_end=window_end,
        published_at=datetime.now(tz=OSLO),
    )
    return Response(document.to_bytes(root))


DESCRIPTION = (
    "The broadcast schedule as NorDig TV-Anytime metadata (ETSI TS "
    "102 822-3-1, profiled by the NorDig TVA Implementation Guidelines "
    "1.4).\n\n"
    "`ProgramInformationTable` describes each programme once and "
    "`ProgramLocationTable` says when it airs, joined by a `crid://` "
    "identifier that is stable across requests. Videos we hold online "
    "rights for additionally appear as `OnDemandProgram` entries.\n\n"
    "Times are Europe/Oslo. `PublishedStartTime` is what is scheduled; "
    "`ActualStartTime` appears only once the airtime has passed, marking "
    "the transmission as one we stand behind as having aired."
)

DAYS_PARAMETER = OpenApiParameter(
    name="days",
    type=int,
    description=f"Number of days to cover, 1-{MAX_DAYS}. Defaults to {DEFAULT_DAYS}.",
    required=False,
)

XML_RESPONSE = OpenApiResponse(
    description="A TVAMain document.",
    response={"type": "string", "format": "binary"},
)


def tvanytime_home(request):
    """The index a distributor lands on, at the root of the tvanytime path.

    Plain Django rather than DRF on purpose: it is a page for a person
    reading about the feed, so it has no place in the OpenAPI schema and
    nothing to content-negotiate. The feeds themselves are the API.

    Dates are resolved here rather than in the template because
    `fkweb.middleware.api_utc_middleware` overrides the active timezone to
    UTC for everything under /api/, which would print today's date as
    yesterday's for the first two hours of every Norwegian day.
    """
    today = datetime.now(tz=OSLO).date()
    return render(
        request,
        "agenda/tvanytime_home.html",
        {
            "title": _("Schedule as TV-Anytime"),
            "channel_name": settings.CHANNEL_DISPLAY_NAMES[0],
            "site_url": settings.SITE_URL,
            "authority": settings.TVA_AUTHORITY,
            "linear_service_id": settings.TVA_LINEAR_SERVICE_ID,
            "ondemand_service_id": settings.TVA_ONDEMAND_SERVICE_ID,
            "upcoming_url": reverse("api-tvanytime-upcoming"),
            "today_url": reverse(
                "api-tvanytime-date",
                args=(f"{today.year:04}", f"{today.month:02}", f"{today.day:02}"),
            ),
            "today": today,
            "default_days": DEFAULT_DAYS,
            "max_days": MAX_DAYS,
        },
    )


class TVAnytimeUpcomingView(APIView):
    """The schedule from today onwards, which is the URL to poll."""

    permission_classes = (permissions.AllowAny,)
    renderer_classes = (TVAnytimeRenderer,)

    @extend_schema(
        operation_id="tvanytime_upcoming_retrieve",
        summary="Upcoming schedule as TV-Anytime",
        description=DESCRIPTION,
        parameters=[DAYS_PARAMETER],
        responses={200: XML_RESPONSE},
    )
    def get(self, request):
        return _render(request, datetime.now(tz=OSLO).date(), _requested_days(request))


class TVAnytimeDateView(APIView):
    """One window, starting on a named date, for backfilling an archive."""

    permission_classes = (permissions.AllowAny,)
    renderer_classes = (TVAnytimeRenderer,)

    @extend_schema(
        operation_id="tvanytime_date_retrieve",
        summary="Schedule from a given date as TV-Anytime",
        description=DESCRIPTION,
        parameters=[DAYS_PARAMETER],
        responses={200: XML_RESPONSE},
    )
    def get(self, request, year, month, day):
        try:
            start_date = date(int(year), int(month), int(day))
        except ValueError:
            # The URL pattern admits 2025-02-31; the calendar does not.
            raise NotFound("No such date.") from None
        return _render(request, start_date, _requested_days(request))
