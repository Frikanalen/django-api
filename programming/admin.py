"""Staff's view of recipes. Recipes are values, so this is for looking
and for the occasional hand-made playlist, not for day-to-day changes."""

from django.contrib import admin

from programming.models import JukeboxSettings, PlaylistSource, Recipe, SeriesSource


class SeriesSourceInline(admin.StackedInline):
    model = SeriesSource
    extra = 0
    autocomplete_fields = ("series",)


class PlaylistSourceInline(admin.StackedInline):
    model = PlaylistSource
    extra = 0
    filter_horizontal = ("videos",)


class JukeboxSettingsInline(admin.StackedInline):
    model = JukeboxSettings
    extra = 0
    filter_horizontal = ("categories",)


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("__str__", "organization", "kind")
    list_filter = ("kind",)
    search_fields = ("organization__name",)
    list_select_related = ("organization",)
    inlines = (SeriesSourceInline, PlaylistSourceInline, JukeboxSettingsInline)
