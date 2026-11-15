from rest_framework import serializers

from api.grants.serializers import GrantOrganizationSerializer
from fk.models import Organization
from scheduling.models import Poster


class PosterSerializer(serializers.ModelSerializer):
    """A text poster made from an on-air graphics template."""

    organization = GrantOrganizationSerializer(read_only=True)
    organization_id = serializers.PrimaryKeyRelatedField(
        source="organization",
        queryset=Organization.objects.all(),
        write_only=True,
        help_text="The organization the poster is for. Only when creating one.",
    )
    minimum_duration = serializers.DurationField(
        read_only=True,
        help_text="The time it takes to read every slide once: the shortest "
        "`duration` allowed. A blank line in `text` starts a new slide.",
    )

    class Meta:
        model = Poster
        fields = (
            "id",
            "organization",
            "organization_id",
            "template",
            "heading",
            "text",
            "duration",
            "minimum_duration",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "organization", "minimum_duration", "created_at", "updated_at")


class PosterUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Poster
        fields = ("template", "heading", "text", "duration")
        extra_kwargs = {field: {"required": False} for field in fields}
