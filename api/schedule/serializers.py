from zoneinfo import ZoneInfo

from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from agenda.scheduling import policy
from fk.models import AsRun, Category, Organization, Scheduleitem, Video, VideoFile

OSLO = ZoneInfo("Europe/Oslo")


class ScheduleitemVideoFileSerializer(serializers.ModelSerializer):
    fsname = serializers.CharField(source="format.fsname", read_only=True)

    class Meta:
        model = VideoFile
        fields = ("id", "fsname", "filename")
        read_only_fields = ("id", "fsname", "filename")


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

    class Meta:
        model = Scheduleitem
        fields = ("id", "video", "schedulereason", "starttime", "endtime", "duration")

    def validate(self, data):
        self._enforce_freeze(data)
        if "starttime" in data or "duration" in data:

            def g(v):
                return self.instance and getattr(self.instance, v)

            start = data.get("starttime", g("starttime"))
            end = start + data.get("duration", g("duration"))
            conflicts = list(
                Scheduleitem.objects.overlapping(start, end)
                .exclude(pk=g("id"))
                .order_by("starttime")
            )
            blocking = [
                item for item in conflicts if item.schedulereason != Scheduleitem.REASON_JUKEBOX
            ]
            if blocking:
                raise serializers.ValidationError({"duration": f"Conflict with '{blocking[0]}'."})
            # Jukebox fillers do not block a pick: they are displaced
            # when this saves, and the nightly jukebox repacks whatever
            # slivers the displacement leaves behind.
            self._displaced_fillers = conflicts
        return data

    def _enforce_freeze(self, data):
        """Members may only touch items in the open broadcast week.

        Both positions matter on a move: an item may neither leave nor
        land inside the frozen weeks. Staff is exempt (the same
        exemption IsInOrganizationOrReadOnly grants on objects).
        """
        request = self.context.get("request")
        if request is None or request.user.is_staff:
            return
        boundary = policy.freeze_boundary()
        current_start = self.instance.starttime if self.instance else None
        new_start = data.get("starttime", current_start)
        for starttime in (current_start, new_start):
            if starttime is not None and starttime < boundary:
                raise serializers.ValidationError({"starttime": policy.freeze_message(boundary)})

    def create(self, validated_data):
        with transaction.atomic():
            self._displace_fillers()
            return super().create(validated_data)

    def update(self, instance, validated_data):
        with transaction.atomic():
            self._displace_fillers()
            return super().update(instance, validated_data)

    def _displace_fillers(self):
        displaced = getattr(self, "_displaced_fillers", None)
        if displaced:
            Scheduleitem.objects.filter(pk__in=[item.pk for item in displaced]).delete()


class ScheduleitemReadSerializer(serializers.ModelSerializer):
    video = ScheduleitemVideoSerializer()
    starttime = serializers.DateTimeField(default_timezone=OSLO)
    endtime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    displaceable = serializers.SerializerMethodField()

    class Meta:
        model = Scheduleitem
        fields = ("id", "video", "starttime", "endtime", "displaceable")

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
        return item.schedulereason == Scheduleitem.REASON_JUKEBOX


class SchedulingPolicySerializer(serializers.Serializer):
    """The broadcast-week policy, as instants to compare schedule items
    against. Clients should derive per-item state from these rather than
    re-implement the week arithmetic (see agenda.scheduling.policy):
    items before freezeBoundary are frozen, items between freezeBoundary
    and schedulingHorizon are in the open week, and airtime beyond
    schedulingHorizon has not been drafted yet.
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


class AsRunSerializer(serializers.ModelSerializer):
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
