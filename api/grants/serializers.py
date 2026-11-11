from zoneinfo import ZoneInfo

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from fk.models import LiveSource, Organization, Video
from scheduling import services
from scheduling.models import AirtimeGrant, FillRule, GrantItem

OSLO = ZoneInfo("Europe/Oslo")


class GrantOrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ("id", "name")
        read_only_fields = fields


class GrantItemVideoSerializer(serializers.ModelSerializer):
    organization = GrantOrganizationSerializer(read_only=True)
    pending_upload = serializers.BooleanField(
        read_only=True,
        help_text="An entry made ahead of its file; its duration was set by hand.",
    )

    class Meta:
        model = Video
        fields = ("id", "name", "duration", "organization", "pending_upload")
        read_only_fields = fields


class GrantItemLiveSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = LiveSource
        fields = ("id", "name")
        read_only_fields = fields


class GrantItemLiveTransmissionSerializer(serializers.Serializer):
    source = GrantItemLiveSourceSerializer(read_only=True)
    name = serializers.CharField(
        read_only=True, help_text="The title given, or else the source's name."
    )
    title = serializers.CharField(read_only=True, help_text="The title given; may be empty.")
    duration = serializers.DurationField(read_only=True)


class GrantItemSerializer(serializers.ModelSerializer):
    video = GrantItemVideoSerializer(read_only=True, allow_null=True)
    live_transmission = serializers.SerializerMethodField()

    class Meta:
        model = GrantItem
        fields = ("position", "video", "live_transmission")
        read_only_fields = fields

    @extend_schema_field(GrantItemLiveTransmissionSerializer(allow_null=True))
    def get_live_transmission(self, item):
        if not item.live_source_id:
            return None
        return GrantItemLiveTransmissionSerializer(
            {
                "source": item.live_source,
                "name": item.name,
                "title": item.title,
                "duration": item.duration,
            }
        ).data


class AirtimeGrantSerializer(serializers.ModelSerializer):
    organization = GrantOrganizationSerializer(read_only=True)
    starttime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    endtime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    locks_at = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    items = GrantItemSerializer(many=True, read_only=True)
    items_duration = serializers.DurationField(read_only=True)
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = AirtimeGrant
        fields = (
            "id",
            "organization",
            "starttime",
            "endtime",
            "state",
            "locks_at",
            "lock_exempt",
            "fill_rule",
            "origin_slot",
            "items",
            "items_duration",
            "can_edit",
        )
        read_only_fields = fields

    @extend_schema_field(
        serializers.BooleanField(
            help_text="Whether the requesting user may replace the list right now."
        )
    )
    def get_can_edit(self, grant) -> bool:
        request = self.context.get("request")
        return request is not None and services.can_edit(grant, request.user)


class AirtimeGrantCreateSerializer(serializers.ModelSerializer):
    """Staff gives an organization a window of airtime directly."""

    organization = serializers.PrimaryKeyRelatedField(queryset=Organization.objects.all())
    starttime = serializers.DateTimeField(default_timezone=OSLO)
    endtime = serializers.DateTimeField(default_timezone=OSLO)
    locks_at = serializers.DateTimeField(
        default_timezone=OSLO,
        required=False,
        help_text="Defaults to the Monday 00:00 before the grant's week airs.",
    )
    fill_rule = serializers.PrimaryKeyRelatedField(
        queryset=FillRule.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = AirtimeGrant
        fields = ("organization", "starttime", "endtime", "locks_at", "lock_exempt", "fill_rule")


class AirtimeGrantUpdateSerializer(serializers.Serializer):
    """Staff cancels a grant, moves its deadline or exempts it from locking."""

    state = serializers.ChoiceField(
        choices=[AirtimeGrant.State.CANCELLED],
        required=False,
        help_text="Only 'cancelled': withdraw the grant and free its window.",
    )
    locks_at = serializers.DateTimeField(default_timezone=OSLO, required=False)
    lock_exempt = serializers.BooleanField(required=False)


class GrantItemWriteSerializer(serializers.Serializer):
    """A video, or a live transmission: a live source with a duration."""

    video = serializers.PrimaryKeyRelatedField(
        queryset=Video.objects.all(), required=False, allow_null=True
    )
    live_source = serializers.PrimaryKeyRelatedField(
        queryset=LiveSource.objects.all(), required=False, allow_null=True
    )
    duration = serializers.DurationField(
        required=False, allow_null=True, help_text="Required for a live transmission."
    )
    title = serializers.CharField(
        required=False, allow_blank=True, max_length=255, help_text="For a live transmission."
    )

    def validate(self, attrs):
        video, source = attrs.get("video"), attrs.get("live_source")
        if (video is None) == (source is None):
            raise serializers.ValidationError("Give either a video or a live source.")
        if source is not None and attrs.get("duration") is None:
            raise serializers.ValidationError({"duration": "A live transmission needs a length."})
        if video is not None and (attrs.get("duration") is not None or attrs.get("title")):
            raise serializers.ValidationError("A video's length and title come from the video.")
        return attrs

    def to_entry(self, attrs) -> services.Entry:
        if attrs.get("live_source") is not None:
            return services.LiveEntry(
                attrs["live_source"], attrs["duration"], attrs.get("title", "")
            )
        return attrs["video"]


class GrantItemsWriteSerializer(serializers.Serializer):
    """The whole list, in playing order. It replaces the current one."""

    items = GrantItemWriteSerializer(many=True)
