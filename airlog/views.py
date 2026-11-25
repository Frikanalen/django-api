"""The air log for staff: what it covers, a playlist of any stretch of it
and its segments. Staff only, like the admin."""

import datetime

from django.http import FileResponse, Http404, HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import segments

# Longest stretch one request may cover. Coverage: a day and a bit, for a
# day's timeline in any time zone. Playlist: a few hours, so a playlist
# stays a few thousand lines.
MAX_COVERAGE = datetime.timedelta(hours=26)
MAX_PLAYLIST = datetime.timedelta(hours=6)

_RANGE_PARAMETERS = [
    OpenApiParameter("start", datetime.datetime, required=True),
    OpenApiParameter("end", datetime.datetime, required=True),
]


def _range(request, longest: datetime.timedelta) -> tuple[datetime.datetime, datetime.datetime]:
    values = []
    for name in ("start", "end"):
        raw = request.query_params.get(name, "")
        parsed = parse_datetime(raw) if raw else None
        if parsed is None:
            raise ValidationError({name: "Oppgi tidspunktet som ISO 8601, med tidssone."})
        if timezone.is_naive(parsed):
            raise ValidationError({name: "Tidspunktet må ha tidssone."})
        values.append(parsed.astimezone(datetime.UTC))
    start, end = values
    if end <= start:
        raise ValidationError({"end": "Slutten må komme etter starten."})
    if end - start > longest:
        raise ValidationError({"end": f"Høyst {longest} om gangen."})
    return start, end


def _require_air_log() -> None:
    if segments.root() is None:
        raise NotFound("Sendeloggen er ikke satt opp her.")


class AirlogCoverage(APIView):
    """The stretches of start..end that the air log has, gaps left out."""

    permission_classes = (IsAdminUser,)

    @extend_schema(
        parameters=_RANGE_PARAMETERS,
        responses=inline_serializer(
            "AirlogCoverage",
            {
                "segment_seconds": serializers.FloatField(),
                "spans": inline_serializer(
                    "AirlogSpan",
                    {"start": serializers.DateTimeField(), "end": serializers.DateTimeField()},
                    many=True,
                ),
            },
        ),
    )
    def get(self, request):
        _require_air_log()
        start, end = _range(request, MAX_COVERAGE)
        return Response(
            {
                "segment_seconds": segments.segment_seconds(),
                "spans": [{"start": a, "end": b} for a, b in segments.spans(start, end)],
            }
        )


class AirlogPlaylist(APIView):
    """An HLS playlist of start..end; left open while end is still to come."""

    permission_classes = (IsAdminUser,)

    @extend_schema(
        parameters=_RANGE_PARAMETERS, responses={(200, "application/vnd.apple.mpegurl"): str}
    )
    def get(self, request):
        _require_air_log()
        start, end = _range(request, MAX_PLAYLIST)
        response = HttpResponse(
            segments.playlist(start, end, timezone.now()),
            content_type="application/vnd.apple.mpegurl",
        )
        response["Cache-Control"] = "no-store"
        return response


class AirlogSegment(APIView):
    """One segment of the air log (MPEG-TS)."""

    permission_classes = (IsAdminUser,)

    @extend_schema(responses={(200, "video/mp2t"): bytes})
    def get(self, request, path: str):
        found = segments.file(path)
        if found is None:
            raise Http404
        response = FileResponse(found.open("rb"), content_type="video/mp2t")
        # A segment never changes once it has its name; private, it's staff's.
        response["Cache-Control"] = "private, max-age=86400, immutable"
        return response
