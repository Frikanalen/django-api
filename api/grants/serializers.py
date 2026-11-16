from zoneinfo import ZoneInfo

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.programming.serializers import RecipeSerializer
from fk.models import LiveSource, Organization, Video
from scheduling import services
from scheduling.models import GRAPHIC_SHORTEST, AirtimeGrant, GrantItem, Graphic, Poster, Showing

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


class GrantItemPosterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Poster
        fields = ("id", "template", "heading", "text", "duration")
        read_only_fields = fields


class GrantItemGraphicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Graphic
        fields = ("id", "name", "url")
        read_only_fields = fields


class GrantItemSerializer(serializers.ModelSerializer):
    video = GrantItemVideoSerializer(read_only=True, allow_null=True)
    poster = GrantItemPosterSerializer(
        read_only=True, allow_null=True, help_text="Set instead of `video` for a text poster."
    )
    graphic = GrantItemGraphicSerializer(
        read_only=True,
        allow_null=True,
        help_text="Set instead of `video` for a staff graphic, on for `duration`.",
    )
    duration = serializers.DurationField(
        read_only=True, allow_null=True, help_text="How long the graphic is on. Only for a graphic."
    )

    class Meta:
        model = GrantItem
        fields = ("position", "video", "poster", "graphic", "duration", "pinned")
        read_only_fields = fields


class AirtimeGrantSerializer(serializers.ModelSerializer):
    organization = GrantOrganizationSerializer(read_only=True)
    starttime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    endtime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    locks_at = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    items = GrantItemSerializer(many=True, read_only=True)
    items_duration = serializers.DurationField(
        read_only=True,
        help_text="From the grant's start to the end of the list, with the gaps that "
        "start each programme on the five-minute grid.",
    )
    padded = serializers.BooleanField(
        read_only=True,
        help_text="Whether programmes start on the five-minute grid, at least 30 s "
        "after the programme before. Posters follow whatever is before them. "
        "False for a jukebox grant, which plays back to back.",
    )
    can_edit = serializers.SerializerMethodField()
    started_items = serializers.SerializerMethodField()
    aired_items = serializers.SerializerMethodField()
    recipe = RecipeSerializer(
        read_only=True,
        allow_null=True,
        help_text="How the grant fills the time its pinned items leave. Null: hand-made.",
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
            "recipe",
            "origin_slot",
            "items",
            "items_duration",
            "padded",
            "can_edit",
            "started_items",
            "aired_items",
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

    @extend_schema_field(
        serializers.IntegerField(
            help_text="How many entries at the head of the list have aired, are on air "
            "or start within seconds. Staff may change what follows them, and move "
            "the end, while the grant is on air."
        )
    )
    def get_started_items(self, grant) -> int:
        return len(services.started_rows(grant))

    @extend_schema_field(
        serializers.IntegerField(
            help_text="How many entries at the head of the list have finished airing. "
            "An edit must keep them first, as they are. Staff may leave out the ones "
            "between this and `startedItems`: that takes them off the air at once."
        )
    )
    def get_aired_items(self, grant) -> int:
        now = timezone.now()
        return sum(1 for row in services.started_rows(grant, now) if row.endtime <= now)


class GrantItemWriteSerializer(serializers.Serializer):
    """A video, a text poster, or a graphic with its duration: exactly one."""

    video = serializers.PrimaryKeyRelatedField(queryset=Video.objects.all(), required=False)
    poster = serializers.PrimaryKeyRelatedField(queryset=Poster.objects.all(), required=False)
    graphic = serializers.PrimaryKeyRelatedField(
        queryset=Graphic.objects.all(), required=False, help_text="Staff only."
    )
    duration = serializers.DurationField(
        required=False, min_value=GRAPHIC_SHORTEST, help_text="With `graphic`: how long it is on."
    )

    def validate(self, attrs):
        if sum(key in attrs for key in ("video", "poster", "graphic")) != 1:
            raise serializers.ValidationError("Give one of a video, a poster or a graphic.")
        if ("graphic" in attrs) != ("duration" in attrs):
            raise serializers.ValidationError("A graphic takes a duration, and only a graphic.")
        return attrs


def entries(items) -> list:
    """The videos, posters and graphics a written list names, in order."""
    return [
        Showing(item["graphic"], item["duration"])
        if "graphic" in item
        else item.get("video") or item["poster"]
        for item in items
    ]


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
    recipe = RecipeSerializer(required=False, allow_null=True)
    items = GrantItemWriteSerializer(
        many=True,
        required=False,
        help_text="Videos already in the grant, pinned, in playing order. They must fit "
        "the window; otherwise no grant is made.",
    )

    class Meta:
        model = AirtimeGrant
        fields = (
            "organization",
            "starttime",
            "endtime",
            "locks_at",
            "lock_exempt",
            "recipe",
            "items",
        )


class AirtimeGrantUpdateSerializer(serializers.Serializer):
    """Staff cancels a grant, moves its deadline or end, or exempts it from locking."""

    state = serializers.ChoiceField(
        choices=[AirtimeGrant.State.CANCELLED],
        required=False,
        help_text="Only 'cancelled': withdraw the grant and free its window.",
    )
    locks_at = serializers.DateTimeField(default_timezone=OSLO, required=False)
    lock_exempt = serializers.BooleanField(required=False)
    starttime = serializers.DateTimeField(
        default_timezone=OSLO,
        required=False,
        help_text="Move the start, until the grant is on air. A start that has passed "
        "means now. Entries that no longer fit come off the list; an earlier start "
        "needs the extra time to be free.",
    )
    endtime = serializers.DateTimeField(
        default_timezone=OSLO,
        required=False,
        help_text="Move the end, even while the grant is on air: what is on air past it "
        "is cut short there. Entries that no longer fit come off the list. A later end "
        "needs the extra time to be free.",
    )


class GrantItemsWriteSerializer(serializers.Serializer):
    """The videos the organization chose, in playing order. They replace
    the current list and are pinned."""

    items = GrantItemWriteSerializer(many=True)
    automatic = serializers.BooleanField(
        default=False,
        help_text="True: the grant's recipe fills the time these videos leave, and "
        "keeps doing so. False: the list stays exactly as given.",
    )
    recipe = RecipeSerializer(
        required=False,
        help_text="With `automatic`: how the grant fills itself. Leave out to keep what it has.",
    )
    whole_slot = serializers.BooleanField(
        default=False,
        help_text="With `recipe`: make it the choice for the grant's recurring slot "
        "too, so its other broadcasts follow.",
    )
