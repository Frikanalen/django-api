"""Airtime grants and the rules that fill them.

A grant is a window of airtime given to one organization, plus the list
of what plays in it. Grants never overlap, and only the compiler (see
:mod:`scheduling.services`) turns them into Scheduleitem rows. Recurring
slots issue grants; fill rules fill them. See the ADR "Airtime grants
replace member scheduling".
"""

from collections.abc import Iterator
from datetime import date, datetime, timedelta

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import ArrayField, DateTimeRangeField, RangeOperators
from django.contrib.postgres.indexes import GistIndex
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from scheduling import broadcast_time


class Playlist(models.Model):
    """A list of videos an organization manages, for fill rules to draw from."""

    organization = models.ForeignKey(
        "fk.Organization", on_delete=models.PROTECT, related_name="playlists"
    )
    name = models.CharField(max_length=100)
    videos = models.ManyToManyField("fk.Video", blank=True, related_name="playlists")

    class Meta:
        ordering = ("organization", "name", "id")

    def __str__(self):
        return self.name


class FillRule(models.Model):
    """How a grant fills itself: where candidates come from, how one is
    chosen, and whether to stop after one or fill the window."""

    class Pool(models.TextChoices):
        ORGANIZATION = "organization", "The organization's uploads"
        PLAYLIST = "playlist", "A playlist"
        FILLER = "filler", "The filler pool"

    class Ordering(models.TextChoices):
        LATEST = "latest", "Newest first"
        LEAST_AIRED = "least_aired", "Least aired first"
        SHUFFLED = "shuffled", "Shuffled"
        WEIGHTED = "weighted", "Weighted draw (fresh, diverse, no repeats)"

    class Mode(models.TextChoices):
        ONE = "one", "One video"
        FILL = "fill", "Fill the window"

    organization = models.ForeignKey(
        "fk.Organization",
        on_delete=models.PROTECT,
        related_name="fill_rules",
        help_text="The organization that owns this rule. For organization pools, "
        "its uploads are also the candidates.",
    )
    name = models.CharField(max_length=100)
    pool = models.CharField(max_length=16, choices=Pool)
    playlist = models.ForeignKey(
        Playlist,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fill_rules",
        help_text="For playlist pools only.",
    )
    ordering = models.CharField(max_length=16, choices=Ordering)
    mode = models.CharField(max_length=8, choices=Mode, default=Mode.FILL)

    class Meta:
        ordering = ("organization", "name", "id")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(pool="playlist", playlist__isnull=False)
                    | (~models.Q(pool="playlist") & models.Q(playlist__isnull=True))
                ),
                name="fillrule_playlist_iff_playlist_pool",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.pool == self.Pool.PLAYLIST and self.playlist is None:
            raise ValidationError({"playlist": "A playlist pool needs a playlist."})
        if self.pool != self.Pool.PLAYLIST and self.playlist is not None:
            raise ValidationError({"playlist": "Only playlist pools take a playlist."})


class RecurringSlot(models.Model):
    """Recurring airtime: a recurrence rule, an owner and a default fill rule.

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
    default_fill_rule = models.ForeignKey(
        FillRule,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Copied onto every grant this slot issues.",
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
        rule = self.default_fill_rule
        if (
            rule is not None
            and self.organization_id
            and rule.organization_id != self.organization_id
        ):
            raise ValidationError(
                {"default_fill_rule": "The rule must belong to the slot's organization."}
            )

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
    fill_rule = models.ForeignKey(
        FillRule,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="grants",
        help_text="While set, the grant fills itself. Editing the list clears it.",
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
        return sum((item.video.duration for item in self.items.all()), timedelta(0))

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


class GrantItem(models.Model):
    """One entry in a grant's list. Items play back-to-back from the
    grant's start; title and length come from the video.

    Live transmissions are not listable yet: LiveTransmission is still
    one-to-one with a schedule item and has no duration of its own.
    """

    grant = models.ForeignKey(AirtimeGrant, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveIntegerField()
    video = models.ForeignKey("fk.Video", on_delete=models.PROTECT, related_name="grant_items")

    class Meta:
        ordering = ("grant", "position")
        constraints = [
            models.UniqueConstraint(
                fields=("grant", "position"),
                name="grantitem_unique_position",
                deferrable=models.Deferrable.DEFERRED,
            ),
        ]

    def __str__(self):
        return f"{self.grant} #{self.position}: {self.video}"
