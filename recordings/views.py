"""What ingest needs to archive a recording, and where it reports back."""

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import playback
from .models import RecordingRequest

State = RecordingRequest.State


class RecordingSerializer(serializers.ModelSerializer):
    playback_url = serializers.SerializerMethodField()

    class Meta:
        model = RecordingRequest
        fields = ("id", "video", "source", "starttime", "endtime", "state", "playback_url")

    def get_playback_url(self, request) -> str:
        return playback.url(request)


class ResultSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    error = serializers.CharField(required=False, allow_blank=True, default="")


class RecordingDetail(APIView):
    """A recording request, with a URL to fetch its window from (ingest only)."""

    permission_classes = (IsAdminUser,)

    def get(self, request, pk):
        recording = get_object_or_404(RecordingRequest, pk=pk)
        return Response(RecordingSerializer(recording).data)


class RecordingResult(APIView):
    """Ingest reports that it archived the recording, or why it couldn't."""

    permission_classes = (IsAdminUser,)

    def post(self, request, pk):
        recording = get_object_or_404(RecordingRequest, pk=pk)
        result = ResultSerializer(data=request.data)
        result.is_valid(raise_exception=True)
        recording.state = State.ARCHIVED if result.validated_data["ok"] else State.FAILED
        recording.error = result.validated_data["error"]
        recording.save(update_fields=["state", "error"])
        return Response(status=204)
