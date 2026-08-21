from pathlib import PurePosixPath

from django.conf import settings
from rest_framework import serializers

from fk.models import ImageMediaType, ImageRole, ProgramImage

MEDIA_TYPE_SUFFIXES = {
    ImageMediaType.JPEG: ".jpg",
    ImageMediaType.PNG: ".png",
    ImageMediaType.WEBP: ".webp",
}


class ProgramImageSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(choices=ImageRole.choices)
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProgramImage
        fields = (
            "id",
            "video",
            "role",
            "filename",
            "media_type",
            "width",
            "height",
            "url",
            "created_time",
        )
        read_only_fields = (
            "id",
            "video",
            "filename",
            "media_type",
            "width",
            "height",
            "url",
            "created_time",
        )

    @staticmethod
    def get_url(image: ProgramImage) -> str:
        return settings.FK_MEDIA_URLPREFIX + image.filename


class ProgramImageRegistrationSerializer(ProgramImageSerializer):
    """Trusted archive metadata reported by ingest after publication."""

    media_type = serializers.ChoiceField(choices=ImageMediaType.choices)
    width = serializers.IntegerField(min_value=1, max_value=65_535)
    height = serializers.IntegerField(min_value=1, max_value=65_535)

    class Meta:
        model = ProgramImage
        fields = ProgramImageSerializer.Meta.fields
        read_only_fields = ("id", "video", "url", "created_time")
        extra_kwargs: dict[str, dict[str, list]] = {"filename": {"validators": []}}

    def validate(self, data):
        video_id = self.context["video_id"]
        filename = PurePosixPath(data["filename"])
        expected_parent = PurePosixPath(str(video_id), "images")
        if filename.parent != expected_parent or filename.name != data["filename"].split("/")[-1]:
            raise serializers.ValidationError(
                {"filename": f"Must be a file directly below {expected_parent}/."}
            )
        expected_suffix = MEDIA_TYPE_SUFFIXES[data["media_type"]]
        if not filename.stem.isalnum() or filename.suffix.lower() != expected_suffix:
            raise serializers.ValidationError(
                {"filename": f"Must have an alphanumeric name and {expected_suffix} suffix."}
            )
        return data
