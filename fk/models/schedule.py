from datetime import UTC, datetime, timedelta

from django.contrib.postgres.fields import DateTimeRangeField
from django.contrib.postgres.indexes import GistIndex
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext as _

from api.schedule.query_set import ScheduleitemQuerySet


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
