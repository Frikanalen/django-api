from zoneinfo import ZoneInfo
from rest_framework import serializers

from fk.models import Category, Video, Scheduleitem, AsRun, Organization


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

    class Meta:
        model = Video
        fields = (
            "id",
            "name",
            "header",
            "description",
            "organization",
            "categories",
        )
        read_only_fields = ("framerate", "created_time", "updated_time")


class ScheduleitemModifySerializer(serializers.ModelSerializer):
    starttime = serializers.DateTimeField(default_timezone=ZoneInfo("Europe/Oslo"))
    endtime = serializers.DateTimeField(default_timezone=ZoneInfo("Europe/Oslo"), read_only=True)

    class Meta:
        model = Scheduleitem
        fields = ("id", "video", "schedulereason", "starttime", "endtime", "duration")

    def validate(self, data):
        if "starttime" in data or "duration" in data:

            def g(v):
                return self.instance and getattr(self.instance, v)

            start = data.get("starttime", g("starttime"))
            end = start + data.get("duration", g("duration"))
            sur_start, sur_end = Scheduleitem.objects.expand_to_surrounding(start, end)
            items = (
                Scheduleitem.objects.exclude(pk=g("id"))
                .filter(starttime__gte=sur_start, starttime__lte=sur_end)
                .order_by("starttime")
            )
            for entry in items:
                if entry.starttime <= start < entry.endtime():
                    raise serializers.ValidationError({"duration": "Conflict with '%s'." % entry})
                if entry.starttime < end < entry.endtime():
                    raise serializers.ValidationError({"duration": "Conflict with '%s'." % entry})
        return data


class ScheduleitemReadSerializer(serializers.ModelSerializer):
    video = ScheduleitemVideoSerializer()
    starttime = serializers.DateTimeField(default_timezone=ZoneInfo("Europe/Oslo"))
    endtime = serializers.DateTimeField(default_timezone=ZoneInfo("Europe/Oslo"), read_only=True)

    class Meta:
        model = Scheduleitem
        fields = ("id", "video", "starttime", "endtime")


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
