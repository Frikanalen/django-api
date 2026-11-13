from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.schedule.serializers import OSLO
from fk import pull_sources
from fk.models import LiveSource, Scheduleitem, StreamCredential


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
    pull_url = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=pull_sources.MAX_LENGTH,
        help_text=(
            "An address Frikanalen pulls this source from (srt://, rtmp(s)://, rtsp(s)://, "
            "or https:// for HLS), instead of an encoder pushing to it. Empty makes it a "
            "pushed source again. Never returned; see pull_target."
        ),
    )
    is_pulled = serializers.BooleanField(read_only=True)
    pull_target = serializers.CharField(
        read_only=True, help_text="Scheme, host and port of the pull address."
    )

    class Meta:
        model = LiveSource
        fields = (
            "id",
            "name",
            "enabled",
            "pull_url",
            "is_pulled",
            "pull_target",
            "stream_id",
            "contribution_path",
            "preview_path",
            "upcoming_transmissions",
        )
        # A member names a source and switches it off and on; the stream ID
        # is generated, and the rest is derived.
        read_only_fields = (
            "id",
            "stream_id",
            "contribution_path",
            "preview_path",
            "upcoming_transmissions",
        )

    def validate_pull_url(self, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        if not pull_sources.is_configured():
            raise serializers.ValidationError("Pulling live sources is not set up here.")
        try:
            pull_sources.validate(value)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        return value

    def create(self, validated_data):
        pull_url = validated_data.pop("pull_url", "")
        source = LiveSource(**validated_data)
        source.set_pull_url(pull_url)
        source.save()
        return source

    def update(self, instance, validated_data):
        pull_url = validated_data.pop("pull_url", None)
        with transaction.atomic():
            if pull_url is not None:
                instance.set_pull_url(pull_url)
                if pull_url:
                    # Nothing publishes to a pulled source; retire its encoder keys.
                    instance.credentials.filter(
                        action=StreamCredential.Action.PUBLISH, revoked_at__isnull=True
                    ).update(revoked_at=timezone.now())
            return super().update(instance, validated_data)

    @extend_schema_field(UpcomingTransmissionSerializer(many=True))
    def get_upcoming_transmissions(self, obj: LiveSource):
        # Attached by the view: live airings that have not yet ended.
        return UpcomingTransmissionSerializer(getattr(obj, "upcoming", []), many=True).data


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
    program_path = serializers.CharField(
        allow_blank=True,
        help_text=(
            "A low-latency copy of what the channel is airing, readable with the "
            "same token. Empty where the receiver has no programme return."
        ),
    )


class SignalVideoSerializer(serializers.Serializer):
    codec = serializers.CharField(help_text="As MediaMTX names it, e.g. `H264`.")
    width = serializers.IntegerField(allow_null=True)
    height = serializers.IntegerField(allow_null=True)
    profile = serializers.CharField(allow_blank=True)


class SignalAudioSerializer(serializers.Serializer):
    codec = serializers.CharField(help_text="As MediaMTX names it, e.g. `MPEG-4 Audio`.")
    sample_rate = serializers.IntegerField(allow_null=True)
    channel_count = serializers.IntegerField(allow_null=True)


class SignalSrtSerializer(serializers.Serializer):
    """Counters since the SRT connection opened."""

    packets_received = serializers.IntegerField()
    packets_lost = serializers.IntegerField(help_text="Lost on the way; SRT asks for these again.")
    packets_retransmitted = serializers.IntegerField()
    packets_dropped = serializers.IntegerField(
        help_text="Never recovered in time: these are what a viewer sees as glitches."
    )
    round_trip_ms = serializers.FloatField(allow_null=True)


class SignalSerializer(serializers.Serializer):
    """The signal arriving on a source, as the encoder sends it."""

    available = serializers.BooleanField(
        help_text="False where the receiver cannot be asked; nothing else is known then."
    )
    receiving = serializers.BooleanField()
    since = serializers.DateTimeField(required=False, allow_null=True)
    protocol = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="`SRT`, `RTMP`, `RTSP` or `WebRTC`; empty for a pulled source.",
    )
    video = SignalVideoSerializer(required=False, allow_null=True)
    audio = SignalAudioSerializer(required=False, allow_null=True)
    bytes_received = serializers.IntegerField(
        required=False,
        help_text="Since the signal came in. The difference between two reads gives the bitrate.",
    )
    frames_in_error = serializers.IntegerField(required=False)
    srt = SignalSrtSerializer(required=False, allow_null=True)
    measured_at = serializers.DateTimeField()


class SignalSampleSerializer(serializers.Serializer):
    at = serializers.DateTimeField(help_text="The end of the step this sample covers.")
    receiving = serializers.BooleanField()
    bitrate = serializers.IntegerField(allow_null=True, help_text="Bits per second.")
    lost = serializers.IntegerField(help_text="SRT packets lost on the way and asked for again.")
    dropped = serializers.IntegerField(help_text="SRT packets that never arrived in time.")
    reconnected = serializers.BooleanField(
        help_text="The encoder had connected anew since the step before."
    )


class SignalHistorySerializer(serializers.Serializer):
    """How a source's signal has held up, from before the page was opened."""

    available = serializers.BooleanField(
        help_text="False where there is no record to ask; `samples` is empty then."
    )
    step_seconds = serializers.IntegerField()
    samples = SignalSampleSerializer(
        many=True, help_text="Oldest first, from the first step with a signal in."
    )


class EncoderKeySerializer(serializers.ModelSerializer):
    """An encoder's publish credential, as listed: never its password."""

    class Meta:
        model = StreamCredential
        fields: tuple[str, ...] = ("id", "label", "key_id", "created_at", "revoked_at")
        read_only_fields = ("id", "key_id", "created_at", "revoked_at")


class ConnectionExampleSerializer(serializers.Serializer):
    # Shadows Field.label in mypy's eyes only: the metaclass takes declared
    # fields out of the class namespace.
    label = serializers.CharField()  # type: ignore[assignment]
    url = serializers.CharField()
    token = serializers.CharField(required=False)
    note = serializers.CharField(required=False)


class IssuedEncoderKeySerializer(EncoderKeySerializer):
    """The one response that carries the password, with the encoder settings
    built from it. Nothing here is stored: lose it and issue a new key."""

    password = serializers.CharField(read_only=True)
    connections = ConnectionExampleSerializer(many=True, read_only=True)
    blackmagic_config = serializers.CharField(
        read_only=True,
        allow_null=True,
        help_text="An XML file Blackmagic Web Presenter and ATEM Mini can import.",
    )

    class Meta(EncoderKeySerializer.Meta):
        fields = (*EncoderKeySerializer.Meta.fields, "password", "connections", "blackmagic_config")
