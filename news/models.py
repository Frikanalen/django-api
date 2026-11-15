from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _

from scheduling import posters


class BulletinQuerySet(models.QuerySet):
    def current(self, now: datetime | None = None):
        """Published, and inside the dates it is set to run between."""
        now = now or timezone.now()
        return self.filter(
            models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now),
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now),
            is_published=True,
        )


class Bulletin(models.Model):
    """A message from the channel: a heading and a text, shown between
    programmes by the on-air graphics and in the infographics rotation.
    It is paged for the screen like a text poster
    (:mod:`scheduling.posters`), and it stops by itself at `ends_at`, so
    a message is never on air after it has gone out of date."""

    class Template(models.TextChoices):
        STANDARD = "standard", "Vanlig"
        NOTICE = "notice", "Viktig melding"

    heading = models.CharField(_("Heading"), max_length=80)
    text = models.TextField(_("Text"), max_length=posters.TEXT_LENGTH)
    template = models.CharField(max_length=16, choices=Template, default=Template.STANDARD)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    is_published = models.BooleanField(default=False)
    starts_at = models.DateTimeField(
        null=True, blank=True, help_text="Shown from this time. Empty: as soon as it is published."
    )
    ends_at = models.DateTimeField(
        null=True, blank=True, help_text="Shown until this time. Empty: until it is unpublished."
    )
    priority = models.SmallIntegerField(
        default=0, help_text="Higher is shown first, and more often when there is little time."
    )
    on_air = models.BooleanField(
        default=True, help_text="Shown by the on-air graphics between programmes."
    )
    on_infographics = models.BooleanField(
        default=True, help_text="Shown in the infographics rotation."
    )

    objects = BulletinQuerySet.as_manager()

    class Meta:
        ordering = ("-priority", "-created", "-id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(starts_at__isnull=True)
                | models.Q(ends_at__isnull=True)
                | models.Q(ends_at__gt=models.F("starts_at")),
                name="bulletin_ends_after_it_starts",
            ),
        ]

    def __str__(self):
        return f'[Bulletin "{self.heading}"]'

    @property
    def pages(self) -> list[str]:
        return posters.slides(self.text)

    @property
    def page_seconds(self) -> list[float]:
        return posters.slide_seconds(self.heading, self.text)

    @property
    def reading_seconds(self) -> int:
        """The time it takes to read every page once."""
        return int(posters.minimum_duration(self.heading, self.text).total_seconds())

    def is_current(self, now: datetime | None = None) -> bool:
        now = now or timezone.now()
        return (
            self.is_published
            and (self.starts_at is None or self.starts_at <= now)
            and (self.ends_at is None or self.ends_at > now)
        )

    def clean(self):
        if len(self.pages) > posters.MOST_SLIDES:
            raise ValidationError(
                {"text": f"A bulletin has at most {posters.MOST_SLIDES} pages on screen."}
            )
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "It has to end after it starts."})
