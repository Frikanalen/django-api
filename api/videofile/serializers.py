from rest_framework import serializers

from fk.models import VideoFile, FileFormat, Video


class VideoFileSerializer(serializers.ModelSerializer):
    format = serializers.SlugRelatedField(slug_field="fsname", queryset=FileFormat.objects.all())
    video = serializers.PrimaryKeyRelatedField(queryset=Video.objects.all())

    class Meta:
        model = VideoFile
        read_only_fields = (
            "id",
            "created_time",
        )
        fields = (
            *read_only_fields,
            "video",
            "format",
            "filename",
            "integrated_lufs",
            "truepeak_lufs",
        )
