from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


class AffidavitStatus(models.TextChoices):
    PENDING = "pending", "Awaiting review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class AffidavitSubmission(models.Model):
    """
    A scan of an organization's signed editor affidavit (redaktorerklaering),
    waiting for staff to check it against the organization's details.

    Approving it is what makes the organization a member, and so also what
    confirms its editor's identity (see Organization.save).

    The scan carries a signature and personal details, so it lives in the
    database, where backups already reach it, rather than on a public media
    path, and only staff can fetch it.
    """

    organization = models.ForeignKey(
        "Organization", on_delete=models.CASCADE, related_name="affidavit_submissions"
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    submitted_at = models.DateTimeField(default=timezone.now)
    file_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    content = models.BinaryField(editable=False)

    status = models.CharField(
        max_length=16, choices=AffidavitStatus.choices, default=AffidavitStatus.PENDING
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(
        blank=True,
        help_text="Shown to the organization when the scan is rejected: say what to fix.",
    )

    class Meta:
        ordering = ("-submitted_at", "-id")
        verbose_name = "affidavit submission"

    def __str__(self):
        return f"{self.organization} ({self.submitted_at:%Y-%m-%d})"

    @property
    def size(self) -> int:
        return len(self.content or b"")

    def approve(self, reviewer) -> None:
        """Accept the scan: the organization becomes a member."""
        with transaction.atomic():
            self._decide(AffidavitStatus.APPROVED, reviewer, "")
            organization = self.organization
            if not organization.fkmember:
                organization.fkmember = True
                organization.save()

    def reject(self, reviewer, note: str) -> None:
        """Turn the scan down, saying why; the organization may upload another."""
        self._decide(AffidavitStatus.REJECTED, reviewer, note)

    def _decide(self, status: str, reviewer, note: str) -> None:
        self.status = status
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_note = note
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])
