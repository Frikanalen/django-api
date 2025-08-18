from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import serializers

from api.organization.serializers import OrganizationSerializer
from fk.models import Category, Video, Organization


class VideoSerializer(serializers.ModelSerializer):
    organization = OrganizationSerializer(read_only=True)
    creator = serializers.SlugRelatedField(
        slug_field="email",
        queryset=get_user_model().objects.all(),
        default=serializers.CurrentUserDefault(),
    )
    categories = serializers.SlugRelatedField(
        slug_field="name", many=True, queryset=Category.objects.all()
    )
    files = serializers.SerializerMethodField()
    duration_sec = serializers.SerializerMethodField()

    @staticmethod
    def get_duration_sec(obj):
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
        if is_creation and not data.get("organization"):
            potential_orgs = data["creator"].organization_set.all()
            if len(potential_orgs) == 0:
                raise serializers.ValidationError(
                    {"organization": "Field required when editor has no organization."}
                )
            elif len(potential_orgs) > 1:
                raise serializers.ValidationError(
                    [{"organization": "Field required when editor has more than one organization."}]
                )
            data["organization"] = potential_orgs[0]
        return data


class VideoCreateSerializer(VideoSerializer):
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
