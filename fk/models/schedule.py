import datetime
from zoneinfo import ZoneInfo

from django.core.cache import caches
from django.db import models
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext as _
from model_utils import Choices

from fk.models import Video
from fk.models.organization import Organization


class ScheduleitemManager(models.Manager):
    def by_day(self, date=None, days=1, surrounding=False):
        if not date:
            date = timezone.now().astimezone(ZoneInfo("Europe/Oslo")).date()
        elif hasattr(date, "date"):
            date.replace(tzinfo=timezone.get_current_timezone())
            date = date.date()
        startdt = datetime.datetime.combine(date, datetime.time(0, tzinfo=ZoneInfo("Europe/Oslo")))
        enddt = startdt + datetime.timedelta(days=days)
        if surrounding:
            startdt, enddt = self.expand_to_surrounding(startdt, enddt)
        return self.get_queryset().filter(starttime__gte=startdt, starttime__lte=enddt)

    def expand_to_surrounding(self, startdt, enddt):
        # Try to find the event before the given date
        try:
            startdt = (
                Scheduleitem.objects.filter(starttime__lte=startdt)
                .order_by("-starttime")[0]
                .starttime
            )
        except IndexError:
            pass
        # Try to find the event after the end date
        try:
            enddt = (
                Scheduleitem.objects.filter(starttime__gte=enddt).order_by("starttime")[0].starttime
            )
        except IndexError:
            pass
        return startdt, enddt


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
    video = models.ForeignKey(Video, null=True, blank=True, on_delete=models.SET_NULL)
    schedulereason = models.IntegerField(blank=True, choices=SCHEDULE_REASONS)
    starttime = models.DateTimeField()
    duration = models.DurationField()

    objects = ScheduleitemManager()

    class Meta:
        verbose_name = "TX schedule entry"
        verbose_name_plural = "TX schedule entries"
        ordering = ("-id",)

    @staticmethod
    @receiver([post_save, post_delete])
    def _clear_cache(**kwargs):
        # logger.warning('[Scheduleitem] cache flush')
        caches["default"].clear()

    def __str__(self):
        t = self.starttime
        s = t.strftime("%Y-%m-%d %H:%M:%S")
        # format microsecond to hundreths
        s += ".%02i" % (t.microsecond / 10000)
        if self.video:
            return str(s) + ": " + str(self.video)
        else:
            return str(s) + ": " + self.default_name

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
    organization = models.ForeignKey(Organization, blank=True, null=True, on_delete=models.SET_NULL)
    direct_videos = models.ManyToManyField(Video, blank=True)

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
            raise Exception("Unhandled type %s" % self.type)
        if max_duration:
            qs = qs.filter(duration__lte=max_duration)
        # Workaround playout not handling broken files correctly
        qs = qs.filter(proper_import=True)
        return qs

    def single_video(self, max_duration=None):
        """
        Get a single video based on the settings of this purpose
        """
        qs = self.videos_queryset(max_duration)
        if self.strategy == self.STRATEGY.latest:
            try:
                return qs.latest()
            except Video.DoesNotExist:
                return None
        elif self.strategy == self.STRATEGY.random:
            # This might be slow, but hopefully few records
            return qs.order_by("?").first()
        elif self.strategy == self.STRATEGY.least_scheduled:
            # Get the video which has been scheduled the least
            return qs.annotate(num_sched=models.Count("scheduleitem")).order_by("num_sched").first()
        else:
            raise Exception("Unhandled strategy %s" % self.strategy)

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
    duration = models.DurationField()

    class Meta:
        ordering = ("day", "start_time", "pk")

    @property
    def end_time(self):
        if not self.duration:
            return self.start_time
        return self.start_time + self.duration

    def next_date(self, from_date=None):
        if not from_date:
            from_date = datetime.date.today()
        days_ahead = self.day - from_date.weekday()
        if days_ahead <= 0:
            # target date already happened this week
            days_ahead += 7
        return from_date + datetime.timedelta(days_ahead)

    def next_datetime(self, from_date=None):
        next_date = self.next_date(from_date)
        return timezone.make_aware(datetime.datetime.combine(next_date, self.start_time))

    def __str__(self):
        return "{day} {s.start_time} ({s.purpose})".format(day=self.get_day_display(), s=self)
