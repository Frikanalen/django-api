from rest_framework.serializers import ModelSerializer, PrimaryKeyRelatedField

from fk.models import VideoFile, Video, FileFormat


class VideoFileSerializer(ModelSerializer):
    # this gives a much better DX, but we have to remain compatible with the Django 3.x backend.
    # should be removed when we drop support for the old Django backend.
    # format = serializers.SlugRelatedField(slug_field="fsname", queryset=FileFormat.objects.all())
    format = PrimaryKeyRelatedField(queryset=FileFormat.objects.all())
    video = PrimaryKeyRelatedField(queryset=Video.objects.all())

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
