"""What ingest needs to archive a recording, and where it reports back."""

import re

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import playback
from .models import RecordingRequest

State = RecordingRequest.State


class RecordingSerializer(serializers.ModelSerializer):
    playback_url = serializers.SerializerMethodField()
    # Set when ingest is to fetch the source's own file instead.
    master_url = serializers.SerializerMethodField()

    class Meta:
        model = RecordingRequest
        fields = (
            "id", "video", "source", "starttime", "endtime", "state", "playback_url", "master_url",
        )  # fmt: skip

    def get_playback_url(self, request) -> str:
        return playback.url(request)

    def get_master_url(self, request) -> str | None:
        return request.master_url if request.use_master else None


class ResultSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    error = serializers.CharField(required=False, allow_blank=True, default="")


class RecordingDetail(APIView):
    """A recording request, with a URL to fetch its window from (ingest only)."""

    permission_classes = (IsAdminUser,)

    def get(self, request, pk):
        recording = get_object_or_404(RecordingRequest, pk=pk)
        return Response(RecordingSerializer(recording).data)


_CREDENTIALS = re.compile(r"//[^/\s@]+@")


class RecordingResult(APIView):
    """Ingest reports that it archived the recording, or why it couldn't."""

    permission_classes = (IsAdminUser,)

    def post(self, request, pk):
        recording = get_object_or_404(RecordingRequest, pk=pk)
        result = ResultSerializer(data=request.data)
        result.is_valid(raise_exception=True)
        # Ingest's error may quote the playback URL: never keep its token.
        error = _CREDENTIALS.sub("//", result.validated_data["error"])
        if result.validated_data["ok"]:
            recording.state, recording.error = State.ARCHIVED, ""
        elif recording.use_master:
            # The master didn't work out: the next pass hands over the
            # recording instead (recordings.reconcile), if there is one.
            recording.use_master = False
            recording.master_deadline = timezone.now()
            recording.import_attempts = 0
            recording.import_requested_at = None
            recording.error = f"Ferdig fil feilet: {error}"
        else:
            recording.state, recording.error = State.FAILED, error
        recording.save(
            update_fields=[
                "state", "error", "use_master", "master_deadline", "import_attempts",
                "import_requested_at",
            ]
        )  # fmt: skip
        return Response(status=204)
