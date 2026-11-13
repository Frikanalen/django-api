"""Recipes: how a window of airtime programs itself.

This app answers one question -- given a window, its owner and what the
owner pinned, what should play? -- and owns every setting that goes
into the answer. Scheduling keeps slots, grants, locking and compiling,
and only points at a Recipe.

A Recipe is a kind plus the owner. What a kind needs to know lives in a
table of its own, hanging off the recipe, with real columns and foreign
keys; "everything the owner uploads" needs none. Nothing here has ever
been on the air, so these tables can change freely: the worst a recipe
can do is leave a window empty.

Recipes are values. They are never edited in place: choosing something
else for a slot or a grant points it at another recipe (see
:mod:`programming.services`), so a change to one never reaches another.
"""

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class Recipe(models.Model):
    class Kind(models.TextChoices):
        UPLOADS = "uploads", "Everything the owner uploads"
        SERIES = "series", "One of the owner's series"
        PLAYLIST = "playlist", "A list of videos"
        JUKEBOX = "jukebox", "The video jukebox"

    organization = models.ForeignKey(
        "fk.Organization",
        on_delete=models.PROTECT,
        related_name="recipes",
        help_text="Whose recipe this is. For the jukebox, the station.",
    )
    kind = models.CharField(max_length=16, choices=Kind)

    class Meta:
        ordering = ("organization", "kind", "id")

    def __str__(self):
        return f"{self.organization}: {self.describe()}"

    def describe(self) -> str:
        if self.kind == self.Kind.SERIES and hasattr(self, "series_source"):
            return f"Series: {self.series_source.series.name}"
        if self.kind == self.Kind.PLAYLIST and hasattr(self, "playlist_source"):
            return f"Playlist: {self.playlist_source.name}"
        if self.kind == self.Kind.JUKEBOX:
            name = self.jukebox_settings.name if hasattr(self, "jukebox_settings") else ""
            return f"Jukebox: {name}" if name else "Jukebox"
        return "New uploads, then the archive"


class SeriesSource(models.Model):
    """A series recipe leads with the series' newest episode not yet
    aired, then its earlier ones. Deleting the series deletes this row,
    and the recipe falls back to everything the owner uploads."""

    recipe = models.OneToOneField(Recipe, on_delete=models.CASCADE, related_name="series_source")
    series = models.ForeignKey("fk.Series", on_delete=models.CASCADE, related_name="+")

    def clean(self):
        super().clean()
        if self.series.organization_id != self.recipe.organization_id:
            raise ValidationError({"series": "The series must belong to the recipe's owner."})


class PlaylistSource(models.Model):
    """A hand-picked list of videos to draw from."""

    recipe = models.OneToOneField(Recipe, on_delete=models.CASCADE, related_name="playlist_source")
    name = models.CharField(max_length=100)
    videos = models.ManyToManyField("fk.Video", blank=True, related_name="+")


class JukeboxSettings(models.Model):
    """What one jukebox slot draws from the filler pool: its theme.

    Every limit is optional; with none set the jukebox draws on the
    whole pool. The draw itself is weighted: fresh, varied across
    organizations, and no repeats (see :mod:`programming.selection`).
    """

    recipe = models.OneToOneField(Recipe, on_delete=models.CASCADE, related_name="jukebox_settings")
    name = models.CharField(
        max_length=100, blank=True, help_text="What staff call this theme, e.g. 'Short films'."
    )
    categories = models.ManyToManyField(
        "fk.Category",
        blank=True,
        related_name="+",
        help_text="Only videos in one of these categories. Empty means any.",
    )
    shortest = models.DurationField(
        null=True, blank=True, help_text="Only videos at least this long."
    )
    longest = models.DurationField(
        null=True, blank=True, help_text="Only videos at most this long."
    )
    newer_than_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Only videos uploaded within this many days.",
    )
    older_than_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Only videos uploaded at least this many days ago.",
    )

    class Meta:
        verbose_name_plural = "jukebox settings"

    def clean(self):
        super().clean()
        if self.shortest and self.longest and self.shortest > self.longest:
            raise ValidationError({"longest": "Must be at least the shortest length."})
        if (
            self.newer_than_days
            and self.older_than_days
            and self.older_than_days >= self.newer_than_days
        ):
            raise ValidationError(
                {"older_than_days": "Leaves no videos: older than the newest allowed."}
            )
