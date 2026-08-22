from zoneinfo import ZoneInfo

from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from agenda.scheduling import policy
from fk.models import (
    AsRun,
    Category,
    Organization,
    Scheduleitem,
    Video,
    VideoFile,
    VideoFileVariant,
    WeeklySlot,
    WeeklySlotSource,
    airtime_end,
)

OSLO = ZoneInfo("Europe/Oslo")


class ScheduleitemVideoFileSerializer(serializers.ModelSerializer):
    # Was `fsname`, reading through the format table that no longer
    # exists. Renamed to match the videofile endpoint, which calls the
    # same value `variant`: one name for one thing, now that both are
    # the same enum.
    variant = serializers.ChoiceField(choices=VideoFileVariant.choices, read_only=True)

    class Meta:
        model = VideoFile
        fields = ("id", "variant", "filename")
        read_only_fields = ("id", "variant", "filename")


class ScheduleitemOrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ("id", "name", "description")
        read_only_fields = ("id", "name", "description")


class ScheduleitemVideoSerializer(serializers.ModelSerializer):
    organization = ScheduleitemOrganizationSerializer(read_only=True)

    categories = serializers.SlugRelatedField(
        slug_field="name", many=True, queryset=Category.objects.all()
    )

    files = ScheduleitemVideoFileSerializer(many=True, read_only=True, source="videofile_set")

    class Meta:
        model = Video
        fields = (
            "id",
            "name",
            "header",
            "description",
            "organization",
            "categories",
            "files",
        )
        read_only_fields = ("framerate", "created_time", "updated_time")


class ScheduleitemModifySerializer(serializers.ModelSerializer):
    starttime = serializers.DateTimeField(default_timezone=OSLO)
    endtime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    schedulereason = serializers.ChoiceField(
        choices=Scheduleitem.SCHEDULE_REASONS,
        required=False,
        help_text="Staff may choose provenance. Member writes are always recorded as User.",
    )

    class Meta:
        model = Scheduleitem
        fields = ("id", "video", "schedulereason", "starttime", "endtime", "duration")

    def validate(self, data):
        self._enforce_scheduling_window(data)
        if "starttime" in data or "duration" in data:

            def g(v):
                return self.instance and getattr(self.instance, v)

            start = data.get("starttime", g("starttime"))
            end = airtime_end(start, data.get("duration", g("duration")))
            # Jukebox fillers do not block a pick, so only blocking
            # conflicts refuse here. This early check is for the 400;
            # save re-checks under lock before displacing.
            blocking, _ = policy.airtime_conflicts(start, end, exclude_pk=g("id"))
            if blocking:
                raise serializers.ValidationError({"duration": f"Conflict with '{blocking[0]}'."})
        return data

    def _enforce_scheduling_window(self, data):
        """Members may only touch airtime wholly inside the open week.

        Both positions matter on a move: an item may neither leave nor
        land outside the open week. Staff is exempt (the same
        exemption IsInOrganizationOrReadOnly grants on objects).
        """
        request = self.context.get("request")
        if request is None or request.user.is_staff:
            return
        boundary = policy.freeze_boundary()
        horizon = policy.scheduling_horizon()
        current_start = self.instance.starttime if self.instance else None
        new_start = data.get("starttime", current_start)
        current_duration = self.instance.duration if self.instance else None
        new_duration = data.get("duration", current_duration)
        for starttime, duration in (
            (current_start, current_duration),
            (new_start, new_duration),
        ):
            if starttime is None or duration is None:
                continue
            if not policy.is_open_airtime(starttime, airtime_end(starttime, duration)):
                raise serializers.ValidationError(
                    {"starttime": policy.scheduling_window_message(boundary, horizon)}
                )

    def create(self, validated_data):
        request = self.context["request"]
        if request.user.is_staff:
            validated_data.setdefault("schedulereason", Scheduleitem.REASON_ADMIN)
        else:
            validated_data["schedulereason"] = Scheduleitem.REASON_USER
        with transaction.atomic():
            self._claim_airtime(validated_data, instance=None)
            return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context["request"]
        if not request.user.is_staff:
            validated_data["schedulereason"] = Scheduleitem.REASON_USER
        with transaction.atomic():
            self._claim_airtime(validated_data, instance)
            # A human edit makes the item deliberate programming: strip
            # slot provenance so the nightly re-pick cannot overwrite
            # what someone changed on source.
            instance.weekly_slot = None
            return super().update(instance, validated_data)

    def _claim_airtime(self, validated_data, instance):
        """Check-and-displace at save time, under row locks.

        validate() already answered once, but without a database
        exclusion constraint (blocked on historical overlapping rows) a
        concurrent write between validation and save is the one overlap
        source the application can still narrow: locking the conflict
        rows serializes concurrent displacements of the same fillers,
        and a blocking item that appeared since validation refuses here
        instead of being scheduled over.
        """

        def current(field):
            return validated_data.get(field, instance and getattr(instance, field))

        start, duration = current("starttime"), current("duration")
        if start is None or duration is None:
            # A partial update that leaves the airtime untouched cannot
            # create a new conflict.
            return
        blocking, displaceable = policy.airtime_conflicts(
            start,
            airtime_end(start, duration),
            exclude_pk=instance and instance.pk,
            for_update=True,
        )
        if blocking:
            raise serializers.ValidationError({"duration": f"Conflict with '{blocking[0]}'."})
        policy.displace(displaceable)


class ScheduleitemReadSerializer(serializers.ModelSerializer):
    video = ScheduleitemVideoSerializer(allow_null=True)
    starttime = serializers.DateTimeField(default_timezone=OSLO)
    endtime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    displaceable = serializers.SerializerMethodField()

    class Meta:
        model = Scheduleitem
        fields = (
            "id",
            "default_name",
            "video",
            "schedulereason",
            "starttime",
            "endtime",
            "duration",
            "displaceable",
            "weekly_slot",
        )
        read_only_fields = fields

    @extend_schema_field(
        serializers.BooleanField(
            help_text="Whether this item is automatic jukebox filler, which a member "
            "organization's own pick replaces when scheduled over it. Deliberate "
            "programming is never displaceable. Only meaningful for items starting at "
            "or after the freezeBoundary of /api/scheduling/policy; frozen airtime "
            "cannot be changed regardless."
        )
    )
    def get_displaceable(self, item) -> bool:
        return policy.is_displaceable(item)


class WeeklySlotSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = WeeklySlotSource
        fields = ("id", "name")
        read_only_fields = fields


class WeeklySlotReadSerializer(serializers.ModelSerializer):
    """A recurring reservation shown alongside the drafted schedule."""

    # The payload keeps saying `purpose` while the model has moved on:
    # renaming the model is this repo's business, renaming a published
    # field is the clients'.
    purpose = WeeklySlotSourceSerializer(source="source", allow_null=True, read_only=True)

    class Meta:
        model = WeeklySlot
        fields = ("id", "purpose", "day", "start_time", "duration")
        read_only_fields = fields


class SchedulingPolicySerializer(serializers.Serializer):
    """The broadcast-week policy and its recurring reservations.

    Clients should derive per-item state from the boundary instants rather
    than re-implement the week arithmetic (see agenda.scheduling.policy):
    items before freezeBoundary are frozen, items between freezeBoundary
    and schedulingHorizon are in the open week, and airtime beyond
    schedulingHorizon has not been drafted yet. Weekly slots describe
    reserved airtime even when no concrete schedule item has been drafted.
    """

    freeze_boundary = serializers.DateTimeField(
        default_timezone=OSLO,
        read_only=True,
        help_text="Schedule items starting before this instant are frozen: member "
        "organizations can no longer create, move, edit, or delete them. It is the "
        "Monday midnight (Europe/Oslo) before the open broadcast week, and advances "
        "one week every Monday at midnight.",
    )
    scheduling_horizon = serializers.DateTimeField(
        default_timezone=OSLO,
        read_only=True,
        help_text="The end of the drafted schedule: the open week runs from "
        "freezeBoundary to here. The nightly jobs do not schedule beyond this "
        "instant, and neither should clients present later airtime as available.",
    )
    server_time = serializers.DateTimeField(
        default_timezone=OSLO,
        read_only=True,
        help_text="The server's clock when this response was produced. Compare "
        "against it, not the local clock, when deciding which side of a boundary "
        "the present moment falls on.",
    )
    weekly_slots = WeeklySlotReadSerializer(
        many=True,
        read_only=True,
        help_text="Recurring reservations that the automatic scheduler fills before jukebox airtime.",
    )


class AsRunSerializer(serializers.ModelSerializer):
    """One entry in the playout log: a video or a named programme, and
    when it went to air."""

    class Meta:
        model = AsRun
        fields = (
            "id",
            "video",
            "program_name",
            "playout",
            "played_at",
            "in_ms",
            "out_ms",
        )
