from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from fk.models import Category, Organization, Series
from programming import services
from programming.models import Recipe

# What only the jukebox takes.
JUKEBOX_FIELDS = ("name", "categories", "shortest", "longest", "newer_than_days", "older_than_days")


class RecipeSerializer(serializers.Serializer):
    """How a slot or a grant programs itself.

    Read, it describes the recipe. Written, it is a choice: the kind and
    what that kind needs. `uploads` (everything the owner uploads, new
    first, then the archive) needs nothing; `series` needs one of the
    owner's series; `jukebox` (staff only, the station's airtime) takes
    an optional theme: categories, shortest and longest length, and how
    new or old the videos may be.
    """

    id = serializers.IntegerField(read_only=True)
    kind = serializers.ChoiceField(choices=Recipe.Kind.choices)
    description = serializers.CharField(read_only=True)
    series = serializers.PrimaryKeyRelatedField(
        queryset=Series.objects.all(), required=False, allow_null=True
    )
    series_name = serializers.CharField(read_only=True, allow_null=True)
    name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    categories = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(), many=True, required=False
    )
    shortest = serializers.DurationField(required=False, allow_null=True)
    longest = serializers.DurationField(required=False, allow_null=True)
    newer_than_days = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    older_than_days = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def to_representation(self, recipe: Recipe) -> dict:
        series = services.series_of(recipe)
        jukebox = getattr(recipe, "jukebox_settings", None)
        duration = serializers.DurationField()
        return {
            "id": recipe.pk,
            "kind": recipe.kind,
            "description": recipe.describe(),
            "series": series.pk if series else None,
            "series_name": series.name if series else None,
            "name": jukebox.name if jukebox else "",
            "categories": [c.pk for c in jukebox.categories.all()] if jukebox else [],
            "shortest": duration.to_representation(jukebox.shortest)
            if jukebox and jukebox.shortest is not None
            else None,
            "longest": duration.to_representation(jukebox.longest)
            if jukebox and jukebox.longest is not None
            else None,
            "newer_than_days": jukebox.newer_than_days if jukebox else None,
            "older_than_days": jukebox.older_than_days if jukebox else None,
        }

    def validate(self, attrs):
        kind = attrs["kind"]
        if kind == Recipe.Kind.PLAYLIST:
            raise serializers.ValidationError(
                {"kind": "Playlists are set up by staff in the admin."}
            )
        if kind == Recipe.Kind.SERIES and attrs.get("series") is None:
            raise serializers.ValidationError({"series": "Choose a series."})
        if kind != Recipe.Kind.SERIES and attrs.get("series") is not None:
            raise serializers.ValidationError({"series": "Only a series recipe takes a series."})
        if kind != Recipe.Kind.JUKEBOX:
            given = [field for field in JUKEBOX_FIELDS if attrs.get(field)]
            if given:
                raise serializers.ValidationError(
                    {field: "Only the jukebox takes this." for field in given}
                )
        return attrs


def build_recipe(choice: dict, *, organization: Organization | None, user) -> Recipe:
    """The recipe for a validated choice. `organization` is who the
    airtime belongs to; the jukebox is the station's own and staff's to
    set up, so it takes no owner."""
    kind = choice["kind"]
    if kind == Recipe.Kind.JUKEBOX:
        if not user.is_staff:
            raise serializers.ValidationError({"kind": "Only staff set up the jukebox."})
        station = Organization.objects.filter(pk=settings.STATION_ORGANIZATION_ID).first()
        if station is None:
            raise serializers.ValidationError(
                {"kind": "The station organization is missing, so nothing can own the jukebox."}
            )
        if organization is not None and organization.pk != station.pk:
            raise serializers.ValidationError(
                {"kind": "The jukebox runs in the station's airtime, not a member's."}
            )
        try:
            return services.jukebox_recipe(
                station, **{field: choice[field] for field in JUKEBOX_FIELDS if field in choice}
            )
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
    if organization is None:
        raise serializers.ValidationError({"kind": "This needs an organization to draw on."})
    if kind == Recipe.Kind.SERIES:
        series = choice["series"]
        if series.organization_id != organization.pk:
            raise serializers.ValidationError(
                {"series": "The series must belong to the organization."}
            )
        return services.series_recipe(series)
    return services.uploads_recipe(organization)
