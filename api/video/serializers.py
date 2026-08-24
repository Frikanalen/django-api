from django.conf import settings
from rest_framework import serializers

from api.organization.serializers import OrganizationSerializer
from api.series.serializers import SeriesSummarySerializer
from fk.models import (
    Category,
    IngestJob,
    IngestKind,
    IngestState,
    Organization,
    Series,
    User,
    Video,
)


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
    series = SeriesSummarySerializer(read_only=True, allow_null=True)
    series_id = serializers.PrimaryKeyRelatedField(
        source="series",
        queryset=Series.objects.all(),
        required=False,
        allow_null=True,
        write_only=True,
    )

    @staticmethod
    def get_duration_sec(obj) -> float | None:
        return obj.duration.total_seconds() if obj.duration is not None else None

    @staticmethod
    def get_files(video) -> dict[str, str]:
        return {
            vf.variant: settings.FK_MEDIA_URLPREFIX + vf.location(relative=True)
            for vf in video.videofile_set.all()
        }

    class Meta:
        model = Video
        # The conditional database constraint on (series, episode_number)
        # becomes a DRF UniqueTogetherValidator that incorrectly makes both
        # optional fields required. validate() below enforces the same rule
        # only when both values are present.
        validators = ()
        fields = (
            "id",
            "name",
            "header",
            "description",
            "files",
            "creator",
            "files",
            "organization",
            "series",
            "series_id",
            "episode_number",
            "duration",
            "duration_sec",
            "categories",
            "framerate",
            "proper_import",
            "has_tono_records",
            "publish_on_web",
            "is_filler",
            "ref_url",
            # Both feed the TV-Anytime EPG and are only ever as good as
            # what the uploading organization tells us, so they are
            # writable here rather than staff-only in the admin.
            "spoken_language",
            "minimum_age",
            "created_time",
            "updated_time",
            "uploaded_time",
            "ogv_url",
            "large_thumbnail_url",
        )
        # `framerate` is writable: ingest works the exact rate out anyway --
        # it has to, to align DASH segments to whole frames -- and until now
        # threw it away, which is why nothing has ever populated the column.
        # Units are the field's own: thousandths of a frame per second, so
        # 25 fps is 25000 and 59.94 is 59940.
        read_only_fields = ("created_time", "updated_time", "files")

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

        organization = data.get("organization")
        if organization is None and self.instance is not None:
            organization = self.instance.organization

        series_was_supplied = "series" in data
        if series_was_supplied and data["series"] is None:
            # Clearing membership also clears the number that only has
            # meaning inside that series.
            data["episode_number"] = None

        series = data.get("series")
        if not series_was_supplied and self.instance is not None:
            series = self.instance.series

        if (
            series is not None
            and organization is not None
            and series.organization_id != organization.pk
        ):
            raise serializers.ValidationError(
                {"series_id": "The series must belong to the video's organization."}
            )

        episode_number = data.get("episode_number")
        if "episode_number" not in data and self.instance is not None:
            episode_number = self.instance.episode_number
        if episode_number is not None and series is None:
            raise serializers.ValidationError(
                {"episode_number": "Choose a series before setting an episode number."}
            )

        if series is not None and episode_number is not None:
            duplicate = Video.objects.filter(
                series=series,
                episode_number=episode_number,
            )
            if self.instance is not None:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise serializers.ValidationError(
                    {"episode_number": "That episode number is already used in this series."}
                )
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
            "priority",
            "kind",
            "claimed_by",
            "percentage_done",
            "status_text",
            "error_code",
            "updated_time",
        )
        # `claimed_by` is set by the claim endpoint and nowhere else. It is
        # readable so an operator can see which worker holds a job, but a
        # progress report has no standing to reassign one.
        read_only_fields = ("video", "claimed_by", "updated_time")
        extra_kwargs = {
            "status_text": {"write_only": True},
            # The model defaults this to `pending`, which would make an
            # otherwise empty report mean something. A report that does not
            # say what state it describes is not a report.
            "state": {"required": True},
            # Both carry model defaults, so DRF leaves them out of
            # validated_data when a report omits them and the stored values
            # survive. That is what keeps this a whole-state PUT without
            # making a mid-pipeline report demote a backfill job to a
            # default-priority upload.
            "priority": {"required": False},
            "kind": {"required": False},
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


class IngestClaimSerializer(serializers.Serializer):
    """What a worker says about itself when it asks for work.

    Both fields are optional, and mean different things by their absence.
    No `kind` is "give me anything", which is right for a single
    undifferentiated worker pool and stays right once the pool splits --
    it is the workers that can only reach one source that have to name
    one. No `worker` is simply an anonymous claim: the identity is
    recorded for operators to read and nothing is decided by it.
    """

    kind = serializers.ChoiceField(choices=IngestKind.choices, required=False, allow_null=True)
    worker = serializers.CharField(max_length=128, required=False, allow_blank=True)
