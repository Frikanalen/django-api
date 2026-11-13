from zoneinfo import ZoneInfo

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from fk.models import LiveSource, Organization, Series, Video
from scheduling import services
from scheduling.models import AirtimeGrant, FillRule, GrantItem

OSLO = ZoneInfo("Europe/Oslo")


class GrantOrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ("id", "name")
        read_only_fields = fields


class GrantItemLiveSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = LiveSource
        fields = ("id", "name")
        read_only_fields = fields


class GrantItemVideoSerializer(serializers.ModelSerializer):
    organization = GrantOrganizationSerializer(read_only=True)
    pending_upload = serializers.BooleanField(
        read_only=True,
        help_text="An entry made ahead of its file; its duration was set by hand.",
    )
    live_source = GrantItemLiveSourceSerializer(
        source="live_transmission.source",
        read_only=True,
        allow_null=True,
        help_text="Set for a live programme: it airs from this source until it has a file.",
    )

    class Meta:
        model = Video
        fields = ("id", "name", "duration", "organization", "pending_upload", "live_source")
        read_only_fields = fields


class GrantItemSerializer(serializers.ModelSerializer):
    video = GrantItemVideoSerializer(read_only=True)

    class Meta:
        model = GrantItem
        fields = ("position", "video", "pinned")
        read_only_fields = fields


class AirtimeGrantSerializer(serializers.ModelSerializer):
    organization = GrantOrganizationSerializer(read_only=True)
    starttime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    endtime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    locks_at = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    items = GrantItemSerializer(many=True, read_only=True)
    items_duration = serializers.DurationField(read_only=True)
    can_edit = serializers.SerializerMethodField()
    fill_series = serializers.IntegerField(
        source="fill_rule.series_id",
        read_only=True,
        allow_null=True,
        default=None,
        help_text="The series the grant fills itself from, if its rule draws on one.",
    )
    fill_series_name = serializers.CharField(
        source="fill_rule.series.name", read_only=True, allow_null=True, default=None
    )

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
            "fill_series",
            "fill_series_name",
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
    video = serializers.PrimaryKeyRelatedField(queryset=Video.objects.all())


class GrantItemsWriteSerializer(serializers.Serializer):
    """The videos the organization chose, in playing order. They replace
    the current list and are pinned."""

    items = GrantItemWriteSerializer(many=True)
    automatic = serializers.BooleanField(
        default=False,
        help_text="True: the grant's rule fills the time these videos leave, and "
        "keeps doing so. False: the list stays exactly as given.",
    )
    series = serializers.PrimaryKeyRelatedField(
        queryset=Series.objects.all(),
        required=False,
        allow_null=True,
        help_text="With `automatic`: what the grant fills itself from. One of the "
        "owner's series, or null for everything they upload. Leave out to keep "
        "what it has.",
    )
    whole_slot = serializers.BooleanField(
        default=False,
        help_text="With `series`: make it the choice for the grant's recurring slot "
        "too, so its other broadcasts follow.",
    )
