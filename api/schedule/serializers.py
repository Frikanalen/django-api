from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from fk.models import (
    AsRun,
    Category,
    LiveTransmission,
    Organization,
    Scheduleitem,
    Video,
    VideoFile,
    VideoFileVariant,
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
            "description",
            "organization",
            "categories",
            "files",
        )
        read_only_fields = ("framerate", "created_time", "updated_time")


class ScheduleitemModifySerializer(serializers.ModelSerializer):
    """Staff corrections to the compiled schedule.

    Organizations program airtime through grants; only the compiler and
    staff write schedule items. A staff entry carries no grant and no
    schedule reason, which holds it to the database's no-overlap
    constraint -- its violation is reported here as a 400.
    """

    starttime = serializers.DateTimeField(default_timezone=OSLO)
    endtime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)

    class Meta:
        model = Scheduleitem
        fields = ("id", "video", "starttime", "endtime", "duration")

    def create(self, validated_data):
        return self._saving(
            lambda: super(ScheduleitemModifySerializer, self).create(validated_data)
        )

    def update(self, instance, validated_data):
        return self._saving(
            lambda: super(ScheduleitemModifySerializer, self).update(instance, validated_data)
        )

    @staticmethod
    def _saving(save):
        try:
            with transaction.atomic():
                return save()
        except IntegrityError as error:
            if "scheduleitem_no_overlap" in str(error):
                raise serializers.ValidationError(
                    {"starttime": "Another programme is scheduled at that time."}
                ) from error
            raise


class LiveTransmissionReadSerializer(serializers.ModelSerializer):
    stream_id = serializers.UUIDField(source="source.stream_id", read_only=True)

    class Meta:
        model = LiveTransmission
        fields = ("id", "source", "stream_id")
        read_only_fields = fields


class ScheduleitemReadSerializer(serializers.ModelSerializer):
    live_transmission = LiveTransmissionReadSerializer(read_only=True, allow_null=True)
    video = ScheduleitemVideoSerializer(allow_null=True)
    starttime = serializers.DateTimeField(default_timezone=OSLO)
    endtime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    # Deprecated, kept for one release so clients generated from the old
    # schema (playout's is strict about required fields) keep working.
    displaceable = serializers.SerializerMethodField()
    weekly_slot = serializers.SerializerMethodField()

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
            "grant",
            "displaceable",
            "weekly_slot",
            "live_transmission",
        )
        read_only_fields = fields

    @extend_schema_field(
        serializers.BooleanField(help_text="Deprecated: always false. Nothing is displaceable.")
    )
    def get_displaceable(self, item) -> bool:
        return False

    @extend_schema_field(
        serializers.IntegerField(allow_null=True, help_text="Deprecated: always null. See grant.")
    )
    def get_weekly_slot(self, item) -> None:
        return None


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
