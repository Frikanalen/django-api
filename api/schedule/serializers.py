from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from fk.models import (
    AsRun,
    Category,
    LiveSource,
    Organization,
    Scheduleitem,
    Video,
    VideoFile,
    VideoFileVariant,
)
from scheduling.services import airs_live

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

    def validate(self, attrs):
        # A live programme airs live until it has a file, here as in grants.
        video = attrs.get("video", getattr(self.instance, "video", None))
        attrs["is_live"] = video is not None and airs_live(video)
        return attrs

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


class ScheduleitemLiveSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = LiveSource
        fields = ("id", "stream_id")
        read_only_fields = fields


class ScheduleitemReadSerializer(serializers.ModelSerializer):
    video = ScheduleitemVideoSerializer(allow_null=True)
    starttime = serializers.DateTimeField(default_timezone=OSLO)
    endtime = serializers.DateTimeField(default_timezone=OSLO, read_only=True)
    live_source = serializers.SerializerMethodField()

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
            "is_live",
            "live_source",
        )
        read_only_fields = fields

    @extend_schema_field(
        ScheduleitemLiveSourceSerializer(
            allow_null=True,
            help_text="Set when this airing goes out live: the video's live source.",
        )
    )
    def get_live_source(self, item):
        live = getattr(item.video, "live_transmission", None) if item.is_live else None
        return ScheduleitemLiveSourceSerializer(live.source).data if live else None


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
