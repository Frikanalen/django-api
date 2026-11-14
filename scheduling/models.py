"""Airtime grants and the rules that fill them.

A grant is a window of airtime given to one organization, plus the list
of what plays in it. Grants never overlap, and only the compiler (see
:mod:`scheduling.services`) turns them into Scheduleitem rows. Recurring
slots issue grants; recipes (see :mod:`programming`) fill them. See the
ADR "Airtime grants replace member scheduling".
"""

from collections.abc import Iterator
from datetime import date, datetime, timedelta

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import ArrayField, DateTimeRangeField, RangeOperators
from django.contrib.postgres.indexes import GistIndex
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from scheduling import broadcast_time, posters


class RecurringSlot(models.Model):
    """Recurring airtime: a recurrence rule, an owner and a recipe.

    The nightly job turns each occurrence into a grant (see
    :func:`scheduling.services.issue_grants`). Explicit fields rather than
    an iCalendar RRULE, so rules are easy to validate and to show.

    * daily: every `interval` days from `starts_on`, optionally only on
      `weekdays`;
    * weekly: on `weekdays`, every `interval` weeks counted from the week
      of `starts_on`;
    * monthly: on `weekdays` in the `month_weeks` of the month (1-5, or
      -1 for the last), every `interval` months from `starts_on`.
    """

    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"

    organization = models.ForeignKey(
        "fk.Organization",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="recurring_slots",
        help_text="Who gets the airtime. Unowned slots issue grants to the station.",
    )
    frequency = models.CharField(max_length=8, choices=Frequency, default=Frequency.WEEKLY)
    interval = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    weekdays = ArrayField(
        models.PositiveSmallIntegerField(validators=[MaxValueValidator(6)]),
        blank=True,
        default=list,
        help_text="0 is Monday. Required for weekly and monthly slots; for daily "
        "slots, empty means every day.",
    )
    month_weeks = ArrayField(
        models.SmallIntegerField(validators=[MinValueValidator(-1), MaxValueValidator(5)]),
        blank=True,
        default=list,
        help_text="Monthly slots: which occurrence of the weekday in the month, "
        "1-5, or -1 for the last.",
    )
    start_time = models.TimeField(help_text="Wall-clock time in Europe/Oslo.")
    duration = models.DurationField(validators=[MinValueValidator(timedelta(seconds=1))])
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    recipe = models.ForeignKey(
        "programming.Recipe",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="How the slot's grants program themselves. Copied onto every "
        "grant it issues. Empty: they are set up by hand.",
    )

    class Meta:
        ordering = ("starts_on", "start_time", "id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duration__gt=timedelta(0)),
                name="recurringslot_duration_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(interval__gte=1), name="recurringslot_interval_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(ends_on__isnull=True)
                | models.Q(ends_on__gte=models.F("starts_on")),
                name="recurringslot_ends_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.describe()} {self.start_time:%H:%M} ({self.organization})"

    def clean(self):
        super().clean()
        if self.frequency in (self.Frequency.WEEKLY, self.Frequency.MONTHLY) and not self.weekdays:
            raise ValidationError({"weekdays": "Choose at least one weekday."})
        if self.frequency == self.Frequency.MONTHLY and not self.month_weeks:
            raise ValidationError({"month_weeks": "Choose which weeks of the month."})
        if self.frequency != self.Frequency.MONTHLY and self.month_weeks:
            raise ValidationError({"month_weeks": "Only monthly slots take weeks of the month."})
        recipe = self.recipe
        if (
            recipe is not None
            and self.organization_id
            and recipe.organization_id != self.organization_id
        ):
            raise ValidationError({"recipe": "The recipe must belong to the slot's organization."})

    def describe(self) -> str:
        days = ", ".join(_WEEKDAY_NAMES[d] for d in sorted(self.weekdays)) or "every day"
        every = "" if self.interval == 1 else f" every {self.interval}"
        if self.frequency == self.Frequency.MONTHLY:
            weeks = ", ".join("last" if w == -1 else str(w) for w in sorted(self.month_weeks))
            return f"Monthly{every} ({weeks}) {days}"
        return f"{self.get_frequency_display()}{every} {days}"

    def occurs_on(self, day: date) -> bool:
        if day < self.starts_on or (self.ends_on is not None and day > self.ends_on):
            return False
        if self.frequency == self.Frequency.DAILY:
            if self.weekdays and day.weekday() not in self.weekdays:
                return False
            return (day - self.starts_on).days % self.interval == 0
        if day.weekday() not in self.weekdays:
            return False
        if self.frequency == self.Frequency.WEEKLY:
            first_monday = self.starts_on - timedelta(days=self.starts_on.weekday())
            this_monday = day - timedelta(days=day.weekday())
            return ((this_monday - first_monday).days // 7) % self.interval == 0
        months = (day.year - self.starts_on.year) * 12 + day.month - self.starts_on.month
        if months % self.interval:
            return False
        nth = (day.day - 1) // 7 + 1
        last = (day + timedelta(days=7)).month != day.month
        return nth in self.month_weeks or (last and -1 in self.month_weeks)

    def occurrences(self, start: datetime, end: datetime) -> Iterator[datetime]:
        """Every instant this slot starts in [start, end), in order."""
        day = broadcast_time.local_date(start) - timedelta(days=1)
        last = broadcast_time.local_date(end)
        while day <= last:
            if self.occurs_on(day):
                instant = broadcast_time.local_instant(day, self.start_time)
                if start <= instant < end:
                    yield instant
            day += timedelta(days=1)


_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# How long a poster may stay on screen.
POSTER_SHORTEST = posters.SHORTEST
POSTER_LONGEST = posters.LONGEST


class AirtimeGrantQuerySet(models.QuerySet):
    def active(self):
        return self.exclude(state=AirtimeGrant.State.CANCELLED)


class AirtimeGrant(models.Model):
    """A window of airtime given to one organization, and what plays in it.

    Lifecycle: open (the organization edits its list) -> locked (at
    `locks_at`; staff may still edit) -> compiled (the items are in
    Scheduleitem). Cancelled frees the window. A lock-exempt grant skips
    the lock: it stays editable until air, and every save after compiling
    recompiles it.
    """

    class State(models.TextChoices):
        OPEN = "open", "Open"
        LOCKED = "locked", "Locked"
        COMPILED = "compiled", "Compiled"
        CANCELLED = "cancelled", "Cancelled"

    organization = models.ForeignKey(
        "fk.Organization", on_delete=models.PROTECT, related_name="airtime_grants"
    )
    starttime = models.DateTimeField()
    endtime = models.DateTimeField()
    airtime = models.GeneratedField(
        expression=models.Func(
            models.F("starttime"),
            models.F("endtime"),
            models.Value("[)"),
            function="tstzrange",
        ),
        output_field=DateTimeRangeField(),
        db_persist=True,
    )
    state = models.CharField(max_length=16, choices=State, default=State.OPEN)
    locks_at = models.DateTimeField(
        help_text="When members can no longer edit the list. Defaults to the "
        "Monday 00:00 before the grant's week airs."
    )
    lock_exempt = models.BooleanField(
        default=False,
        help_text="Editable until air; every save after compiling recompiles it. Staff only.",
    )
    recipe = models.ForeignKey(
        "programming.Recipe",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="grants",
        help_text="While set, the grant fills the time its pinned items leave. "
        "Saving a hand-made list clears it.",
    )
    origin_slot = models.ForeignKey(
        RecurringSlot,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="grants",
        help_text="Provenance only: the grant copied the slot's time when issued.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AirtimeGrantQuerySet.as_manager()

    class Meta:
        ordering = ("starttime", "id")
        indexes = [
            models.Index(fields=["starttime"], name="airtimegrant_starttime_idx"),
            GistIndex(fields=["airtime"], name="airtimegrant_airtime_gist"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(endtime__gt=models.F("starttime")),
                name="airtimegrant_ends_after_start",
            ),
            ExclusionConstraint(
                name="airtimegrant_no_overlap",
                expressions=[("airtime", RangeOperators.OVERLAPS)],
                condition=~models.Q(state="cancelled"),
            ),
            # Issuing is keyed by slot and start, counting cancelled
            # grants, so a cancelled occurrence is never re-issued.
            models.UniqueConstraint(
                fields=("origin_slot", "starttime"),
                condition=models.Q(origin_slot__isnull=False),
                name="airtimegrant_one_per_slot_occurrence",
            ),
        ]

    def __str__(self):
        local = self.starttime.astimezone(broadcast_time.TZ)
        return f"{local:%Y-%m-%d %H:%M} {self.organization} ({self.get_state_display()})"

    @property
    def duration(self) -> timedelta:
        return self.endtime - self.starttime

    @property
    def items_duration(self) -> timedelta:
        """How long the list runs, from the grant's start to the end of its
        last entry, gaps between programmes included."""
        from scheduling import layout

        entries = [item.entry for item in self.items.all()]
        return layout.end_of(self.starttime, entries, padded=self.padded) - self.starttime

    @property
    def padded(self) -> bool:
        """Whether programmes start on the five-minute grid (see
        :mod:`scheduling.layout`). Not in a jukebox grant."""
        from programming.models import Recipe

        return self.recipe is None or self.recipe.kind != Recipe.Kind.JUKEBOX

    def clean(self):
        super().clean()
        if self.starttime and self.endtime and self.endtime <= self.starttime:
            raise ValidationError({"endtime": "A grant must end after it starts."})
        if self.starttime and self.endtime:
            overlapping = (
                AirtimeGrant.objects.active()
                .filter(starttime__lt=self.endtime, endtime__gt=self.starttime)
                .exclude(pk=self.pk)
                .first()
            )
            if overlapping is not None and self.state != self.State.CANCELLED:
                raise ValidationError(f"Overlaps the grant {overlapping}.")


class Poster(models.Model):
    """A text poster an organization makes from one of the on-air graphics
    templates, to air in its grants: in the gap after a programme, say,
    or as a notice of its own. It is listed in a grant like a video and
    airs for its `duration`; playout shows it with the graphics template
    named by `template`, light by day and dark by night. A blank line in
    the text starts a new slide, and `duration` is at least the time it
    takes to read them all once (:mod:`scheduling.posters`)."""

    class Template(models.TextChoices):
        STANDARD = "standard", "Vanlig"
        NOTICE = "notice", "Viktig melding"

    organization = models.ForeignKey(
        "fk.Organization", on_delete=models.CASCADE, related_name="posters"
    )
    template = models.CharField(max_length=16, choices=Template, default=Template.STANDARD)
    heading = models.CharField(max_length=80)
    text = models.TextField(max_length=posters.MOST_SLIDES * (posters.SLIDE_LENGTH + 2), blank=True)
    duration = models.DurationField(
        default=timedelta(seconds=30),
        validators=[
            MinValueValidator(POSTER_SHORTEST),
            MaxValueValidator(POSTER_LONGEST),
        ],
        help_text="How long it is on screen each time it airs.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duration__gte=POSTER_SHORTEST)
                & models.Q(duration__lte=POSTER_LONGEST),
                name="poster_duration_in_range",
            ),
        ]

    def __str__(self):
        return f"{self.heading} ({self.organization})"

    @property
    def minimum_duration(self) -> timedelta:
        return posters.minimum_duration(self.heading, self.text)

    def clean(self):
        slides = posters.slides(self.text)
        if len(slides) > posters.MOST_SLIDES:
            raise ValidationError({"text": f"A poster has at most {posters.MOST_SLIDES} slides."})
        if any(len(slide) > posters.SLIDE_LENGTH for slide in slides):
            raise ValidationError(
                {
                    "text": f"A slide holds at most {posters.SLIDE_LENGTH} characters. "
                    "A blank line starts a new slide."
                }
            )
        if self.duration is not None and self.duration < self.minimum_duration:
            seconds = int(self.minimum_duration.total_seconds())
            raise ValidationError(
                {
                    "duration": f"The text takes {seconds} s to read, "
                    "so the poster must be on at least that long."
                }
            )


class GrantItem(models.Model):
    """One entry in a grant's list: a video or a text poster. Title and
    length come from it. Where each entry plays is worked out by
    :mod:`scheduling.layout`. A live programme is a video with a live
    transmission and a length set by hand, so it is listed like any
    other.

    A pinned item is one the owner chose: it stays, at the head of the
    list, while the grant's recipe programs the time left."""

    grant = models.ForeignKey(AirtimeGrant, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveIntegerField()
    video = models.ForeignKey(
        "fk.Video", null=True, blank=True, on_delete=models.PROTECT, related_name="grant_items"
    )
    poster = models.ForeignKey(
        Poster, null=True, blank=True, on_delete=models.PROTECT, related_name="grant_items"
    )
    pinned = models.BooleanField(
        default=False,
        help_text="Chosen by the owner. Automatic filling keeps it and fills around it.",
    )

    class Meta:
        ordering = ("grant", "position")
        constraints = [
            models.UniqueConstraint(
                fields=("grant", "position"),
                name="grantitem_unique_position",
                deferrable=models.Deferrable.DEFERRED,
            ),
            models.CheckConstraint(
                condition=models.Q(video__isnull=False, poster__isnull=True)
                | models.Q(video__isnull=True, poster__isnull=False),
                name="grantitem_video_or_poster",
            ),
        ]

    def __str__(self):
        return f"{self.grant} #{self.position}: {self.entry}"

    @property
    def entry(self):
        """What plays: the video or the poster."""
        return self.video if self.video_id is not None else self.poster


# The request models live in their own module; importing them here
# registers them with the app.
from scheduling.requests import *
