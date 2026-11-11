"""Organizations' requests for recurring airtime, and the staff decision.

The slots themselves are scheduling.RecurringSlot. These models stay in
fk until scheduling owns them (see the airtime grants ADR).
"""

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .organization import Organization

DAY_OF_THE_WEEK = (
    (0, _("Monday")),
    (1, _("Tuesday")),
    (2, _("Wednesday")),
    (3, _("Thursday")),
    (4, _("Friday")),
    (5, _("Saturday")),
    (6, _("Sunday")),
)


class ReviewStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    DENIED = "denied", "Denied"


def decision_audit_constraint(name):
    return models.CheckConstraint(
        condition=(
            models.Q(
                status=ReviewStatus.PENDING,
                reviewed_by__isnull=True,
                reviewed_at__isnull=True,
            )
            | models.Q(
                status__in=(
                    ReviewStatus.APPROVED,
                    ReviewStatus.DENIED,
                ),
                reviewed_by__isnull=False,
                reviewed_at__isnull=False,
            )
        ),
        name=name,
    )


class ReviewedRequest(models.Model):
    """Shared audit state and one-way decision workflow: a staff decision
    with a required comment, recorded once."""

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(
        max_length=16,
        choices=ReviewStatus,
        default=ReviewStatus.PENDING,
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
            raise PermissionDenied("Only an administrator may decide a request.")
        if status not in (
            ReviewStatus.APPROVED,
            ReviewStatus.DENIED,
        ):
            raise ValidationError({"status": "Choose approved or denied."})
        comment = comment.strip()
        if not comment:
            raise ValidationError({"admin_comment": "A decision comment is required."})

        with transaction.atomic():
            request = type(self).objects.select_for_update().get(pk=self.pk)
            if request.status != ReviewStatus.PENDING:
                raise ValidationError({"status": "This request has already been decided."})

            update_fields = ["status", "reviewed_by", "admin_comment", "reviewed_at"]
            if status == ReviewStatus.APPROVED:
                update_fields.extend(request._approve())
            request.status = status
            request.reviewed_by = admin
            request.admin_comment = comment
            request.reviewed_at = timezone.now()
            request.save(update_fields=update_fields)

        self.refresh_from_db()
        return self


class RecurringSlotCreationRequest(ReviewedRequest):
    """An organization's request for a newly allocated recurring slot."""

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="slot_creation_requests",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reviewed_slot_creation_requests",
    )
    day = models.IntegerField(choices=DAY_OF_THE_WEEK)
    start_time = models.TimeField()
    duration = models.DurationField(validators=[MinValueValidator(timedelta(0))])
    slot = models.ForeignKey(
        "scheduling.RecurringSlot",
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
                name="recurringslotcreationrequest_duration_not_negative",
            ),
            decision_audit_constraint("recurringslotcreationrequest_decision_audit_consistent"),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=ReviewStatus.APPROVED,
                        slot__isnull=False,
                    )
                    | models.Q(
                        status__in=(
                            ReviewStatus.PENDING,
                            ReviewStatus.DENIED,
                        ),
                        slot__isnull=True,
                    )
                ),
                name="recurringslotcreationrequest_result_consistent",
            ),
        ]

    def _approve(self):
        from scheduling.services import create_weekly_slot

        self.slot = create_weekly_slot(
            organization=self.organization,
            day=self.day,
            start_time=self.start_time,
            duration=self.duration,
        )
        return ("slot",)

    def __str__(self):
        return f"{self.organization}: {self.get_day_display()} {self.start_time} ({self.status})"


class RecurringSlotOwnershipRequest(ReviewedRequest):
    """An organization's request to take control of an existing slot."""

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="slot_ownership_requests",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reviewed_slot_ownership_requests",
    )
    slot = models.ForeignKey(
        "scheduling.RecurringSlot",
        on_delete=models.PROTECT,
        related_name="ownership_requests",
    )
    previous_organization = models.ForeignKey(
        "Organization",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="slot_transfer_requests",
        help_text="The target slot's owner when the request was submitted.",
    )

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            decision_audit_constraint("recurringslotownershiprequest_decision_audit_consistent"),
            models.UniqueConstraint(
                fields=("organization", "slot"),
                condition=models.Q(status=ReviewStatus.PENDING),
                name="one_pending_ownership_request_per_organization_slot",
            ),
        ]

    def _approve(self):
        from scheduling.models import RecurringSlot
        from scheduling.services import transfer_slot

        slot = RecurringSlot.objects.select_for_update().get(pk=self.slot_id)
        if slot.organization_id != self.previous_organization_id:
            raise ValidationError(
                {"slot": "The slot's ownership changed after this request was submitted."}
            )
        transfer_slot(slot, self.organization)
        return ()

    def __str__(self):
        return f"{self.organization} requests {self.slot} ({self.status})"
