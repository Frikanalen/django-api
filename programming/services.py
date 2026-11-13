"""Getting the recipe for a choice.

Recipes are values: asking twice for the same thing gives the same row
where the kind has nothing to vary (everything an owner uploads, one
series), and a new row for a jukebox theme. Nothing edits a recipe.
"""

from datetime import timedelta

from django.db import transaction

from fk.models import Category, Organization, Series
from programming.models import JukeboxSettings, Recipe, SeriesSource


def uploads_recipe(organization: Organization) -> Recipe:
    """The owner's new uploads first, then their archive, least aired
    first. The standard recipe: doing nothing still works."""
    existing = Recipe.objects.filter(organization=organization, kind=Recipe.Kind.UPLOADS).first()
    return existing or Recipe.objects.create(organization=organization, kind=Recipe.Kind.UPLOADS)


def series_recipe(series: Series) -> Recipe:
    """The series' newest episode not yet aired first, then earlier ones."""
    existing = Recipe.objects.filter(
        organization=series.organization, kind=Recipe.Kind.SERIES, series_source__series=series
    ).first()
    if existing:
        return existing
    with transaction.atomic():
        recipe = Recipe.objects.create(organization=series.organization, kind=Recipe.Kind.SERIES)
        SeriesSource.objects.create(recipe=recipe, series=series)
    return recipe


def jukebox_recipe(
    station: Organization,
    *,
    name: str = "",
    categories: list[Category] | None = None,
    shortest: timedelta | None = None,
    longest: timedelta | None = None,
    newer_than_days: int | None = None,
    older_than_days: int | None = None,
) -> Recipe:
    """A jukebox theme of its own. With no limits, the whole filler pool."""
    with transaction.atomic():
        recipe = Recipe.objects.create(organization=station, kind=Recipe.Kind.JUKEBOX)
        settings = JukeboxSettings(
            recipe=recipe,
            name=name,
            shortest=shortest,
            longest=longest,
            newer_than_days=newer_than_days,
            older_than_days=older_than_days,
        )
        settings.full_clean(exclude=["recipe"])
        settings.save()
        settings.categories.set(categories or [])
    return recipe


def series_of(recipe: Recipe | None) -> Series | None:
    """The series a recipe follows, if it follows one."""
    if recipe is None or not hasattr(recipe, "series_source"):
        return None
    return recipe.series_source.series
