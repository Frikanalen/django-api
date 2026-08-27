from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .organization import Organization

if TYPE_CHECKING:
    # A real import would put video ahead of weekly-slot models in the
    # package's import order. The string model reference is sufficient at
    # runtime; this import exists only for the M2M annotation.
    from .video import Video


class SlotSourceType(models.TextChoices):
    """Where a source's candidate videos come from."""

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
        help_text=(
            "The organization that controls this source. For organization sources, "
            "its uploads are also the candidate pool."
        ),
    )
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
        """Get the queryset for the available videos."""
        qs = self.candidate_videos()
        if max_duration:
            qs = qs.filter(duration__lte=max_duration)
        # Workaround playout not handling broken files correctly.
        qs = qs.filter(proper_import=True)
        # Nothing airs unattended on behalf of an organization that has
        # no ansvarlig redaktor to answer for it.
        return qs.filter(organization__in=Organization.objects.with_responsible_editor())

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
        """Select one currently eligible video using this source's strategy."""
        qs = self.videos_queryset(max_duration)
        if self.strategy == SlotSourceStrategy.LATEST:
            return qs.order_by("-created_time", "-id").first()
        if self.strategy == SlotSourceStrategy.RANDOM:
            return qs.order_by("?").first()
        if self.strategy == SlotSourceStrategy.LEAST_SCHEDULED:
            return qs.annotate(num_sched=models.Count("scheduleitem")).order_by("num_sched").first()
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

    organization = models.ForeignKey(
        "Organization",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        help_text=(
            "The organization that controls what fills this slot. Legacy unowned slots "
            "are staff-only."
        ),
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
    day = models.IntegerField(choices=DAY_OF_THE_WEEK)
    start_time = models.TimeField()
    duration = models.DurationField(validators=[MinValueValidator(timedelta(0))])

    class Meta:
        ordering = ("day", "start_time", "pk")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duration__gte=timedelta(0)),
                name="weeklyslot_duration_not_negative",
            ),
        ]

    def clean(self):
        super().clean()
        if self.source_id and self.source.organization_id != self.organization_id:
            raise ValidationError({"source": "The source must belong to the slot's organization."})

    @property
    def end_time(self) -> time:
        if not self.duration:
            return self.start_time
        dummy_date = datetime.combine(date(1970, 1, 1), self.start_time)
        return (dummy_date + self.duration).time()

    def next_date(self, from_date=None):
        """The slot's next occurrence on or after from_date."""
        if not from_date:
            from_date = timezone.localdate()
        days_ahead = self.day - from_date.weekday()
        if days_ahead < 0:
            days_ahead += 7
        return from_date + timedelta(days_ahead)

    def next_datetime(self, from_date=None):
        if from_date is not None:
            return timezone.make_aware(datetime.combine(self.next_date(from_date), self.start_time))
        now = timezone.localtime()
        result = timezone.make_aware(datetime.combine(self.next_date(now.date()), self.start_time))
        if result <= now:
            result += timedelta(days=7)
        return result

    def __str__(self):
        return f"{self.get_day_display()} {self.start_time} ({self.source})"


class WeeklySlotRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    DENIED = "denied", "Denied"


def decision_audit_constraint(name):
    return models.CheckConstraint(
        condition=(
            models.Q(
                status=WeeklySlotRequestStatus.PENDING,
                reviewed_by__isnull=True,
                reviewed_at__isnull=True,
            )
            | models.Q(
                status__in=(
                    WeeklySlotRequestStatus.APPROVED,
                    WeeklySlotRequestStatus.DENIED,
                ),
                reviewed_by__isnull=False,
                reviewed_at__isnull=False,
            )
        ),
        name=name,
    )


class WeeklySlotRequestBase(models.Model):
    """Shared audit state and one-way decision workflow for slot requests."""

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(
        max_length=16,
        choices=WeeklySlotRequestStatus,
        default=WeeklySlotRequestStatus.PENDING,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    admin_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ("-created_at", "-id")

    def _approve(self):
        raise NotImplementedError

    def decide(self, *, admin, status, comment):
        """Record one final staff decision and apply an approval atomically."""
        if not admin.is_staff:
            raise PermissionDenied("Only an administrator may decide a weekly slot request.")
        if status not in (
            WeeklySlotRequestStatus.APPROVED,
            WeeklySlotRequestStatus.DENIED,
        ):
            raise ValidationError({"status": "Choose approved or denied."})
        comment = comment.strip()
        if not comment:
            raise ValidationError({"admin_comment": "A decision comment is required."})

        with transaction.atomic():
            request = type(self).objects.select_for_update().get(pk=self.pk)
            if request.status != WeeklySlotRequestStatus.PENDING:
                raise ValidationError({"status": "This request has already been decided."})

            update_fields = ["status", "reviewed_by", "admin_comment", "reviewed_at"]
            if status == WeeklySlotRequestStatus.APPROVED:
                update_fields.extend(request._approve())
            request.status = status
            request.reviewed_by = admin
            request.admin_comment = comment
            request.reviewed_at = timezone.now()
            request.save(update_fields=update_fields)

        self.refresh_from_db()
        return self


class WeeklySlotCreationRequest(WeeklySlotRequestBase):
    """An organization's request for a newly allocated recurring slot."""

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="weekly_slot_creation_requests",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reviewed_weekly_slot_creation_requests",
    )
    day = models.IntegerField(choices=WeeklySlot.DAY_OF_THE_WEEK)
    start_time = models.TimeField()
    duration = models.DurationField(validators=[MinValueValidator(timedelta(0))])
    weekly_slot = models.ForeignKey(
        WeeklySlot,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="creation_requests",
    )

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duration__gte=timedelta(0)),
                name="weeklyslotcreationrequest_duration_not_negative",
            ),
            decision_audit_constraint("weeklyslotcreationrequest_decision_audit_consistent"),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=WeeklySlotRequestStatus.APPROVED,
                        weekly_slot__isnull=False,
                    )
                    | models.Q(
                        status__in=(
                            WeeklySlotRequestStatus.PENDING,
                            WeeklySlotRequestStatus.DENIED,
                        ),
                        weekly_slot__isnull=True,
                    )
                ),
                name="weeklyslotcreationrequest_result_consistent",
            ),
        ]

    def _approve(self):
        self.weekly_slot = WeeklySlot.objects.create(
            organization=self.organization,
            day=self.day,
            start_time=self.start_time,
            duration=self.duration,
        )
        return ("weekly_slot",)

    def __str__(self):
        return f"{self.organization}: {self.get_day_display()} {self.start_time} ({self.status})"


class WeeklySlotOwnershipRequest(WeeklySlotRequestBase):
    """An organization's request to take control of an existing slot."""

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="weekly_slot_ownership_requests",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reviewed_weekly_slot_ownership_requests",
    )
    weekly_slot = models.ForeignKey(
        WeeklySlot,
        on_delete=models.PROTECT,
        related_name="ownership_requests",
    )
    previous_organization = models.ForeignKey(
        "Organization",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="weekly_slot_transfer_requests",
        help_text="The target slot's owner when the request was submitted.",
    )

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            decision_audit_constraint("weeklyslotownershiprequest_decision_audit_consistent"),
            models.UniqueConstraint(
                fields=("organization", "weekly_slot"),
                condition=models.Q(status=WeeklySlotRequestStatus.PENDING),
                name="one_pending_ownership_request_per_organization_slot",
            ),
        ]

    def _approve(self):
        weekly_slot = WeeklySlot.objects.select_for_update().get(pk=self.weekly_slot_id)
        if weekly_slot.organization_id != self.previous_organization_id:
            raise ValidationError(
                {"weekly_slot": "The slot's ownership changed after this request was submitted."}
            )
        weekly_slot.organization = self.organization
        # A source belongs to its former owner. The new owner deliberately
        # selects one after the transfer through the ordinary slot endpoint.
        weekly_slot.source = None
        weekly_slot.save(update_fields=("organization", "source"))
        return ()

    def __str__(self):
        return f"{self.organization} requests {self.weekly_slot} ({self.status})"
