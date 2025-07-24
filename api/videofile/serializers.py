from rest_framework import serializers

from fk.models import VideoFile, FileFormat


class VideoFileSerializer(serializers.ModelSerializer):
    format = serializers.SlugRelatedField(
        slug_field="fsname",
        queryset=FileFormat.objects.all(),
    )

    class Meta:
        model = VideoFile
        read_only_fields = (
            "id",
            "video",
            "created_time",
        )
        fields = (
            *read_only_fields,
            "format",
            "filename",
            "integrated_lufs",
            "truepeak_lufs",
        )
