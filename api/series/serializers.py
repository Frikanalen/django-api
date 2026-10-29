from rest_framework import serializers

from api.organization.serializers import OrganizationSerializer
from fk.models import Organization, Series


class BaseSeriesSerializer(serializers.ModelSerializer):
    episode_count = serializers.SerializerMethodField()
    # Reserved for the managed artwork upload flow. Members can read the
    # resulting URL, but cannot make the site load an arbitrary remote image
    # by supplying one through ordinary series create/update requests.
    image_url = serializers.URLField(read_only=True)

    @staticmethod
    def get_episode_count(obj: Series) -> int:
        # List/detail querysets annotate this in bulk. Create responses hold a
        # freshly saved instance instead, where one small count keeps the wire
        # shape identical rather than silently omitting the field.
        if "episode_count" in obj.__dict__:
            return obj.__dict__["episode_count"]
        return obj.videos.count()

    class Meta:
        model = Series
        fields = (
            "id",
            "name",
            "synopsis",
            "image_url",
            "organization",
            "episode_count",
        )
        read_only_fields = ("id", "episode_count")


class SeriesSerializer(BaseSeriesSerializer):
    organization = OrganizationSerializer(read_only=True)


class SeriesSummarySerializer(serializers.ModelSerializer):
    image_url = serializers.URLField(read_only=True)

    class Meta:
        model = Series
        fields = ("id", "name", "synopsis", "image_url")


class SeriesWriteSerializer(BaseSeriesSerializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=Organization.objects.all())
