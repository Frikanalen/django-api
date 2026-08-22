from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING

from django.contrib.postgres.fields import DateTimeRangeField
from django.contrib.postgres.indexes import GistIndex
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _

from api.schedule.query_set import ScheduleitemQuerySet

from .organization import Organization

if TYPE_CHECKING:
    # Only for the direct_videos annotation; a real import here would put
    # video ahead of schedule in the package's import order.
    from .video import Video


def airtime_end(starttime: datetime, duration: timedelta) -> datetime:
    """When an item starting at `starttime` stops occupying the air.

    Saved rows carry this already, as `Scheduleitem.airtime.upper`; this is
    for the items validation sees before there is a row to ask.

    It deliberately does not write `starttime + duration`. Python adds a
    timedelta to an aware datetime in *wall-clock* terms, so an Oslo-aware
    start -- which is what DRF hands over, given `default_timezone=OSLO` --
    gains an hour across the autumn transition. Playout, and the generated
    column, count elapsed time. Converting first pins it to that.
    """
    return starttime.astimezone(UTC) + duration


class Scheduleitem(models.Model):
    REASON_LEGACY = 1
    REASON_ADMIN = 2
    REASON_USER = 3
    REASON_AUTO = 4
    REASON_JUKEBOX = 5
    SCHEDULE_REASONS = (
        (REASON_LEGACY, "Legacy"),
        (REASON_ADMIN, "Administrative"),
        (REASON_USER, "User"),
        (REASON_AUTO, "Automatic"),
        (REASON_JUKEBOX, "Jukebox"),
    )

    id = models.AutoField(primary_key=True)
    default_name = models.CharField(max_length=255, blank=True)
    video = models.ForeignKey("Video", null=True, blank=True, on_delete=models.SET_NULL)
    schedulereason = models.IntegerField(blank=True, choices=SCHEDULE_REASONS)
    starttime = models.DateTimeField()
    duration = models.DurationField(validators=[MinValueValidator(timedelta(0))])
    # Which WeeklySlot placed this item, for REASON_AUTO placements made
    # after this field existed. The nightly filler may re-pick its *own*
    # unfrozen placements when the source's answer changes; an item
    # with no slot recorded (member picks, admin entries, pre-provenance
    # rows) is deliberate programming and is never touched. SET_NULL so
    # deleting a slot definition strands its drafted items as deliberate
    # programming instead of ripping them off the air.
    weekly_slot = models.ForeignKey("WeeklySlot", null=True, blank=True, on_delete=models.SET_NULL)

    # Published as <Live> in the TV-Anytime feed. A property of the
    # transmission, not of the video, which is why it sits here: the same
    # recording can go out live once and as a repeat afterwards, and a live
    # transmission often has no video row at all (see default_name).
    is_live = models.BooleanField(
        "Live broadcast",
        default=False,
        help_text="Whether this item goes out live rather than from a file.",
    )

    # The airtime this item occupies, as a half-open range, so "is that
    # slot taken" is one indexed `&&` against a GiST index rather than a
    # scan computing every row's end time. Derived rather than stored:
    # starttime and duration remain the writable truth, and a generated
    # column cannot drift from them.
    #
    # The arithmetic is pinned to UTC because `timestamptz + interval` is
    # merely *stable* -- adding a day component consults the session
    # TimeZone -- and a generation expression must be immutable. This
    # changes nothing: Django runs its connections in UTC (USE_TZ without
    # a per-database TIME_ZONE), so the pinned expression is exactly what
    # the old `starttime + duration` annotation already evaluated to.
    airtime = models.GeneratedField(
        expression=models.Func(
            models.F("starttime"),
            models.Func(
                models.Value("UTC"),
                models.ExpressionWrapper(
                    models.Func(models.Value("UTC"), models.F("starttime"), function="timezone")
                    + models.F("duration"),
                    output_field=models.DateTimeField(),
                ),
                function="timezone",
            ),
            models.Value("[)"),
            function="tstzrange",
        ),
        output_field=DateTimeRangeField(),
        db_persist=True,
    )

    objects = ScheduleitemQuerySet.as_manager()

    class Meta:
        verbose_name = "TX schedule entry"
        verbose_name_plural = "TX schedule entries"
        ordering = ("-id",)
        indexes = [
            # by_day() and the front page both filter and sort on starttime;
            # a GiST index over airtime answers neither, having no ordering.
            models.Index(fields=["starttime"], name="scheduleitem_starttime_idx"),
            GistIndex(fields=["airtime"], name="scheduleitem_airtime_gist"),
        ]
        constraints = [
            # An item's airtime would otherwise end before it began, which
            # makes it invisible to the jukebox's gap search and lets it
            # schedule over programming that is really going out.
            models.CheckConstraint(
                condition=models.Q(duration__gte=timedelta(0)),
                name="scheduleitem_duration_not_negative",
            ),
        ]

    def __str__(self):
        # %f renders microseconds as six digits; drop four to get hundredths
        timestamp = self.starttime.strftime("%Y-%m-%d %H:%M:%S.%f")[:-4]
        return f"{timestamp}: {self.video or self.default_name}"

    @property
    def endtime(self) -> datetime:
        """When this item stops occupying the air.

        Read off the generated column wherever there is one, so the instant
        the API reports is the instant the conflict queries used.

        A zero-length item makes an *empty* range, and an empty range has no
        bounds at all -- `upper` is None, not the starttime. It ends where it
        begins, which is what this has always reported for one. The remaining
        branch is for items the database has not seen yet.
        """
        if self.airtime is not None and self.airtime.upper is not None:
            return self.airtime.upper
        if not self.duration:
            return self.starttime
        return airtime_end(self.starttime, self.duration)

    def save(self, *args, **kwargs):
        """Keep `airtime` in step with the row it describes.

        Postgres recomputes a generated column on every write, but Django
        only reads one back on INSERT. After an UPDATE the in-memory value
        would still describe where the item used to air -- which is the
        value the API hands back to whoever just moved it.
        """
        updating = not self._state.adding
        super().save(*args, **kwargs)
        if updating:
            self.refresh_from_db(fields=["airtime"])

    def _timing_changed(self):
        """Whether this save moves the item in time, for an item that exists."""
        stored = Scheduleitem.objects.filter(pk=self.pk).values("starttime", "duration").first()
        if stored is None:
            return True
        return stored["starttime"] != self.starttime or stored["duration"] != self.duration

    def clean(self):
        """Refuse to put two programmes on the air at once.

        ModelForms call this, so it covers the admin. It deliberately does not
        run on every save: editing an unrelated field on one of the historical
        overlapping rows would otherwise fail on a conflict the editor neither
        caused nor can resolve. Only a change of airtime is re-checked, which
        is the same rule ScheduleitemModifySerializer applies.

        Note this is validation, not enforcement -- save() does not call it.
        The schedule fillers ask ScheduleitemQuerySet.overlapping() directly
        and skip, rather than raising.
        """
        super().clean()
        if self.starttime is None or not self.duration:
            # Missing values are field-level errors, and a zero-length item
            # occupies no airtime, so it cannot collide with anything.
            return
        if self.pk and not self._timing_changed():
            return
        conflict = (
            Scheduleitem.objects.overlapping(
                self.starttime, airtime_end(self.starttime, self.duration)
            )
            .exclude(pk=self.pk)
            .first()
        )
        if conflict:
            raise ValidationError({"duration": _("Conflict with '%s'.") % conflict})


class SlotSourceType(models.TextChoices):
    """Where a source's candidate videos come from.

    Declared beside the model rather than inside it so that the admin, the
    serializers and the tests can name a type without importing the model.
    """

    VIDEOS = "videos", "Hand-picked videos"
    ORGANIZATION = "organization", "Everything one organization has uploaded"


class SlotSourceStrategy(models.TextChoices):
    """Which of the candidates goes on the air when a slot comes round."""

    LATEST = "latest", "The newest upload"
    RANDOM = "random", "A random one"
    LEAST_SCHEDULED = "least_scheduled", "The one that has aired the least"


class WeeklySlotSource(models.Model):
    """A named answer to "what should air in this slot?".

    A WeeklySlot says *when* airtime recurs; it points here for *what*
    fills it. This model is that rule in two halves: `type` (plus the
    organization or the hand-picked list it implies) is the pool of
    candidates, and `strategy` picks one of them per occurrence.

    Nothing here is a fixed programme. The pool is re-read and the
    strategy re-applied every time the nightly filler considers a slot,
    which is how a weekly slot keeps carrying an organization's newest
    upload without anyone touching the schedule.
    """

    name = models.CharField(
        max_length=100,
        help_text="Shown wherever a slot names its source, including the public planner.",
    )
    type = models.CharField(
        max_length=32,
        choices=SlotSourceType,
        help_text="Where the candidate videos come from.",
    )
    strategy = models.CharField(
        max_length=32,
        choices=SlotSourceStrategy,
        help_text="Which candidate airs when a slot using this source comes round.",
    )

    organization = models.ForeignKey(
        "Organization",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        help_text="For organization sources: whose uploads to draw from. Ignored otherwise.",
    )
    # Quoted so the subscript is never evaluated: ManyToManyField is not
    # subscriptable at runtime, only to django-stubs.
    direct_videos: "models.ManyToManyField[Video, models.Model]" = models.ManyToManyField(
        "Video",
        blank=True,
        help_text="For hand-picked sources: the videos to draw from. Ignored otherwise.",
    )

    class Meta:
        ordering = ("-id",)

    def candidate_videos(self):
        """The pool this source draws from, before eligibility filtering.

        Separate from `videos_queryset` so the admin can say how many
        videos a source *would* have and why they were dropped; nothing
        that schedules anything should use this one.
        """
        if self.type == SlotSourceType.ORGANIZATION:
            return self.organization.video_set.all()
        if self.type == SlotSourceType.VIDEOS:
            return self.direct_videos.all()
        raise ValueError(f"Unhandled type {self.type}")

    def videos_queryset(self, max_duration=None):
        """
        Get the queryset for the available videos
        """
        qs = self.candidate_videos()
        if max_duration:
            qs = qs.filter(duration__lte=max_duration)
        # Workaround playout not handling broken files correctly
        qs = qs.filter(proper_import=True)
        # Nothing airs unattended on behalf of an organization that has
        # no ansvarlig redaktor to answer for it.
        qs = qs.filter(organization__in=Organization.objects.with_responsible_editor())
        return qs

    def still_current(self, video, max_duration=None):
        """Whether a draft placement of `video` still stands.

        `latest` keeps chasing the newest upload, so an outdated pick
        gets replaced. The other strategies only require the video to
        remain eligible: re-rolling `random` nightly, or letting
        `least_scheduled` oscillate with the counts its own placements
        create, would churn the draft for no editorial gain.
        """
        if video is None:
            return False
        if self.strategy == SlotSourceStrategy.LATEST:
            return self.single_video(max_duration) == video
        return self.videos_queryset(max_duration).filter(pk=video.pk).exists()

    def single_video(self, max_duration=None):
        """
        Get a single video based on the settings of this source
        """
        qs = self.videos_queryset(max_duration)
        if self.strategy == SlotSourceStrategy.LATEST:
            return qs.order_by("-created_time", "-id").first()
        elif self.strategy == SlotSourceStrategy.RANDOM:
            # This might be slow, but hopefully few records
            return qs.order_by("?").first()
        elif self.strategy == SlotSourceStrategy.LEAST_SCHEDULED:
            # Get the video which has been scheduled the least
            return qs.annotate(num_sched=models.Count("scheduleitem")).order_by("num_sched").first()
        else:
            raise ValueError(f"Unhandled strategy {self.strategy}")

    def __str__(self):
        return self.name


class WeeklySlot(models.Model):
    DAY_OF_THE_WEEK = (
        (0, _("Monday")),
        (1, _("Tuesday")),
        (2, _("Wednesday")),
        (3, _("Thursday")),
        (4, _("Friday")),
        (5, _("Saturday")),
        (6, _("Sunday")),
    )

    source = models.ForeignKey(
        WeeklySlotSource,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=(
            "Which source picks the video for this slot. Blank means the slot "
            "reserves airtime that nothing is scheduled into automatically."
        ),
    )
    day = models.IntegerField(
        choices=DAY_OF_THE_WEEK,
    )
    start_time = models.TimeField()
    duration = models.DurationField(validators=[MinValueValidator(timedelta(0))])

    class Meta:
        ordering = ("day", "start_time", "pk")
        constraints = [
            # end_time would wrap backwards past start_time.
            models.CheckConstraint(
                condition=models.Q(duration__gte=timedelta(0)),
                name="weeklyslot_duration_not_negative",
            ),
        ]

    @property
    def end_time(self) -> time:
        if not self.duration:
            return self.start_time

        # any fixed date will do; only the time survives the arithmetic
        dummy_date = datetime.combine(date(1970, 1, 1), self.start_time)
        end_datetime = dummy_date + self.duration
        return end_datetime.time()

    def next_date(self, from_date=None):
        """The slot's next occurrence on or after from_date."""
        if not from_date:
            # next_datetime() combines this with make_aware(), which resolves
            # against Django's timezone, so the date has to come from there too
            from_date = timezone.localdate()
        days_ahead = self.day - from_date.weekday()
        if days_ahead < 0:
            # target weekday already happened this week
            days_ahead += 7
        return from_date + timedelta(days_ahead)

    def next_datetime(self, from_date=None):
        if from_date is not None:
            return timezone.make_aware(datetime.combine(self.next_date(from_date), self.start_time))
        # Anchored to the clock: today's occurrence only counts while its
        # start time is still ahead.
        now = timezone.localtime()
        result = timezone.make_aware(datetime.combine(self.next_date(now.date()), self.start_time))
        if result <= now:
            result += timedelta(days=7)
        return result

    def __str__(self):
        return f"{self.get_day_display()} {self.start_time} ({self.source})"
