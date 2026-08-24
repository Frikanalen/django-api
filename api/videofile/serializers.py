from rest_framework.serializers import ChoiceField, ModelSerializer, PrimaryKeyRelatedField

from fk.models import Video, VideoFile, VideoFileVariant


class VideoFileSerializer(ModelSerializer):
    # The variant is its name -- "broadcast", "dash" -- where this used
    # to be `format` carrying the primary key of a row in a lookup
    # table. Named explicitly rather than left to ModelSerializer so
    # that the schema points at one shared enum component, see
    # ENUM_NAME_OVERRIDES.
    variant = ChoiceField(choices=VideoFileVariant.choices)
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
            "variant",
            "filename",
            "integrated_lufs",
            "truepeak_lufs",
            # Writable: ingest stamps the revision of the profile it just
            # encoded with when it registers the file. Nothing else has any
            # business claiming to know what produced a file, and anything
            # that does not say reads as 0 -- older than every real profile.
            "profile_revision",
        )
