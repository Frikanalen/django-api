from django.conf import settings
from rest_framework import serializers

from api.organization.serializers import OrganizationSerializer
from fk.models import Category, IngestJob, IngestState, Organization, User, Video


class BaseVideoSerializer(serializers.ModelSerializer):
    """Everything the read and write video serializers share.

    They differ in one field: reads nest the whole organization, writes
    take its primary key. Declaring that field on each subclass rather
    than having one override the other keeps them siblings, so neither
    has to contradict the type it inherits. Never used directly -- both
    subclasses supply the missing `organization`.
    """

    # Attribution, not a choice: the creator is whoever performs the
    # creation, and is never changeable through the API afterwards.
    creator: serializers.SlugRelatedField[User] = serializers.SlugRelatedField(
        slug_field="email", read_only=True
    )
    categories = serializers.SlugRelatedField(
        slug_field="name", many=True, queryset=Category.objects.all()
    )
    files = serializers.SerializerMethodField()
    duration_sec = serializers.SerializerMethodField()

    @staticmethod
    def get_duration_sec(obj) -> float | None:
        return obj.duration.total_seconds() if obj.duration is not None else None

    @staticmethod
    def get_files(video) -> dict[str, str]:
        return {
            vf.format.fsname: settings.FK_MEDIA_URLPREFIX + vf.location(relative=True)
            for vf in video.videofile_set.all()
        }

    class Meta:
        model = Video
        fields = (
            "id",
            "name",
            "header",
            "description",
            "files",
            "creator",
            "files",
            "organization",
            "duration",
            "duration_sec",
            "categories",
            "framerate",
            "proper_import",
            "has_tono_records",
            "publish_on_web",
            "is_filler",
            "ref_url",
            "created_time",
            "updated_time",
            "uploaded_time",
            "ogv_url",
            "large_thumbnail_url",
        )
        read_only_fields = ("framerate", "created_time", "updated_time", "files")

    def validate(self, data):
        is_creation = not self.instance
        if is_creation:
            data["creator"] = self.context["request"].user
            if not data.get("organization"):
                potential_orgs = data["creator"].organization_set.all()
                if len(potential_orgs) == 0:
                    raise serializers.ValidationError(
                        {"organization": "Field required when editor has no organization."}
                    )
                elif len(potential_orgs) > 1:
                    raise serializers.ValidationError(
                        {
                            "organization": "Field required when editor has more than one organization."
                        }
                    )
                data["organization"] = potential_orgs[0]
        return data


class VideoSerializer(BaseVideoSerializer):
    """The read serializer: the organization arrives whole."""

    organization = OrganizationSerializer(read_only=True)


class VideoCreateSerializer(BaseVideoSerializer):
    """The write serializer: the organization is named by primary key,
    or left out entirely for validate() to infer from the creator."""

    organization = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.all(), required=False
    )


class VideoUploadTokenSerializer(serializers.ModelSerializer):
    upload_url = serializers.CharField(default=settings.FK_UPLOAD_URL, read_only=True)

    class Meta:
        model = Video
        fields = (
            "upload_token",
            "upload_url",
        )


class UploadTokenVerificationSerializer(serializers.Serializer):
    """The upload capability presented by ingest for a specific video."""

    upload_token = serializers.CharField(max_length=32, trim_whitespace=False)


class IngestJobSerializer(serializers.ModelSerializer):
    """What ingest reports about an upload, and what its uploader is shown.

    `status_text` is write-only deliberately: it carries ffmpeg's
    complaints and the archive paths behind them, which belong in the
    admin and the logs rather than in a response to an organization's
    members. What they get instead is `error_code`, which the frontend
    turns into words -- ingest has no business choosing the Norwegian.
    """

    # Null until ingest has reported anything at all -- which is the state
    # every video uploaded before this endpoint existed is in. The model
    # field cannot express that, because a saved row always has a time.
    updated_time = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = IngestJob
        fields = (
            "video",
            "state",
            "percentage_done",
            "status_text",
            "error_code",
            "updated_time",
        )
        read_only_fields = ("video", "updated_time")
        extra_kwargs = {
            "status_text": {"write_only": True},
            # The model defaults this to `pending`, which would make an
            # otherwise empty report mean something. A report that does not
            # say what state it describes is not a report.
            "state": {"required": True},
        }

    def validate(self, data):
        # The same rule as the ingest_job_error_code_only_when_failed
        # constraint, stated where it produces a 400 rather than the 500 an
        # IntegrityError would surface as.
        if data.get("error_code") and data.get("state") != IngestState.FAILED:
            raise serializers.ValidationError(
                {"error_code": "Only a failed ingest may carry an error code."}
            )
        return data
