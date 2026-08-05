from datetime import date, datetime, time, timedelta

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _
from model_utils import Choices

from api.schedule.query_set import ScheduleitemQuerySet

from .organization import Organization


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

    objects = ScheduleitemQuerySet.as_manager()

    class Meta:
        verbose_name = "TX schedule entry"
        verbose_name_plural = "TX schedule entries"
        ordering = ("-id",)
        constraints = [
            # endtime() would otherwise precede starttime, which makes the
            # item invisible to the jukebox's gap search and lets it
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

    def endtime(self):
        if not self.duration:
            return self.starttime
        return self.starttime + self.duration


class SchedulePurpose(models.Model):
    """
    A block of video files having a similar purpose.

    Either an organization and its videos (takes preference) or manually
    connected videos.
    """

    STRATEGY = Choices("latest", "random", "least_scheduled")
    TYPE = Choices("videos", "organization")

    name = models.CharField(max_length=100)
    type = models.CharField(max_length=32, choices=TYPE)
    strategy = models.CharField(max_length=32, choices=STRATEGY)

    # You probably need one of these depending on type and strategy
    organization = models.ForeignKey(
        "Organization", blank=True, null=True, on_delete=models.SET_NULL
    )
    direct_videos = models.ManyToManyField("Video", blank=True)

    class Meta:
        ordering = ("-id",)

    def videos_str(self):
        return ", ".join([str(x) for x in self.videos_queryset()])

    videos_str.short_description = "videos"
    videos_str.admin_order_field = "videos"

    def videos_queryset(self, max_duration=None):
        """
        Get the queryset for the available videos
        """
        if self.type == self.TYPE.organization:
            qs = self.organization.video_set.all()
        elif self.type == self.TYPE.videos:
            qs = self.direct_videos.all()
        else:
            raise ValueError(f"Unhandled type {self.type}")
        if max_duration:
            qs = qs.filter(duration__lte=max_duration)
        # Workaround playout not handling broken files correctly
        qs = qs.filter(proper_import=True)
        # Nothing airs unattended on behalf of an organization that has
        # no ansvarlig redaktor to answer for it.
        qs = qs.filter(organization__in=Organization.objects.with_responsible_editor())
        return qs

    def single_video(self, max_duration=None):
        """
        Get a single video based on the settings of this purpose
        """
        qs = self.videos_queryset(max_duration)
        if self.strategy == self.STRATEGY.latest:
            try:
                return qs.latest()
            except qs.model.DoesNotExist:
                return None
        elif self.strategy == self.STRATEGY.random:
            # This might be slow, but hopefully few records
            return qs.order_by("?").first()
        elif self.strategy == self.STRATEGY.least_scheduled:
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

    purpose = models.ForeignKey(SchedulePurpose, null=True, blank=True, on_delete=models.SET_NULL)
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
        return f"{self.get_day_display()} {self.start_time} ({self.purpose})"
