from rest_framework import serializers

from fk.models import VideoFile, FileFormat


class BaseVideoFileSerializer(serializers.ModelSerializer):
    format = serializers.SlugRelatedField(
        slug_field="fsname",
        queryset=FileFormat.objects.all(),
    )

    class Meta:
        model = VideoFile
        fields = (
            "format",
            "filename",
            "integrated_lufs",
            "truepeak_lufs",
        )


class VideoFileCreateSerializer(BaseVideoFileSerializer):
    pass


class VideoFileSerializer(BaseVideoFileSerializer):
    class Meta(BaseVideoFileSerializer.Meta):
        read_only_fields = (
            "id",
            "video",
            "created_time",
        )
        fields = (
            *read_only_fields,
            *BaseVideoFileSerializer.Meta.fields,
        )
