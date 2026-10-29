from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.schedule.serializers import OSLO
from fk.models import LiveSource, Scheduleitem


class UpcomingTransmissionSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    starttime = serializers.DateTimeField(default_timezone=OSLO)
    endtime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)

    class Meta:
        model = Scheduleitem
        fields = ("id", "name", "starttime", "endtime")
        read_only_fields = fields

    def get_name(self, obj: Scheduleitem) -> str:
        return obj.video.name if obj.video else obj.default_name


class LiveSourceSerializer(serializers.ModelSerializer):
    contribution_path = serializers.CharField(read_only=True)
    preview_path = serializers.CharField(read_only=True)
    upcoming_transmissions = serializers.SerializerMethodField()

    class Meta:
        model = LiveSource
        fields = (
            "id",
            "name",
            "enabled",
            "stream_id",
            "contribution_path",
            "preview_path",
            "upcoming_transmissions",
        )
        read_only_fields = fields

    @extend_schema_field(UpcomingTransmissionSerializer(many=True))
    def get_upcoming_transmissions(self, obj: LiveSource):
        # Prefetched by the view: only bookings that have not yet ended.
        items = [t.schedule_item for t in obj.upcoming]  # type: ignore[attr-defined]
        return UpcomingTransmissionSerializer(items, many=True).data


class PreviewTokenSerializer(serializers.Serializer):
    token = serializers.CharField(
        help_text="Send as `Authorization: Bearer <token>` with the WHEP request."
    )
    expires_at = serializers.DateTimeField(
        help_text="The token must be presented before this; a session it started keeps playing."
    )
    contribution_path = serializers.CharField(
        help_text="The original feed. WebRTC carries its video but not AAC audio."
    )
    preview_path = serializers.CharField(
        help_text="The Opus-audio preview, if a conversion worker runs for this source."
    )
