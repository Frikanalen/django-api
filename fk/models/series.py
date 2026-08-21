from django.core.validators import URLValidator
from django.db import models


class SeriesQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Everything for staff, series from accountable organizations otherwise."""
        if getattr(user, "is_staff", False):
            return self
        return self.filter(
            organization__editor__isnull=False,
            organization__editor__is_active=True,
        )


class Series(models.Model):
    """An organization-owned programme strand containing related videos."""

    id = models.AutoField(primary_key=True)
    organization = models.ForeignKey(
        "Organization",
        related_name="series",
        on_delete=models.PROTECT,
        help_text="Organization responsible for the series.",
    )
    name = models.CharField(max_length=255)
    synopsis = models.TextField(blank=True, max_length=2048)
    image_url = models.CharField(
        "Image URL",
        blank=True,
        max_length=1024,
        validators=[URLValidator(schemes=("https",))],
        help_text="Public HTTPS URL for series artwork. Leave blank when no artwork is available.",
    )

    objects = SeriesQuerySet.as_manager()

    class Meta:
        verbose_name_plural = "series"
        ordering = ("name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "name"),
                name="series_name_unique_per_organization",
            )
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return f"/series/{self.id}/"
