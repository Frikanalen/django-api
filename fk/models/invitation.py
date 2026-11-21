import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

INVITATION_LIFETIME = timedelta(days=14)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class OrganizationInvitationQuerySet(models.QuerySet):
    def open(self):
        return self.filter(accepted_at__isnull=True, expires_at__gt=timezone.now())


class OrganizationInvitation(models.Model):
    """
    An editor's invitation for someone to become a member of their
    organization: sent to an e-mail address, or handed over as a link or
    QR code. Either way it is used once.

    The editor learns nothing from it about whether the address has an
    account: the answer and the e-mail are the same either way, and the
    person's name only shows in the organization once they accept. Only
    the e-mailed link can accept it (account e-mail addresses are not
    verified, so matching on the address would let anyone who registers
    with it first take the place). Only the token's hash is stored.
    """

    organization = models.ForeignKey(
        "Organization", on_delete=models.CASCADE, related_name="invitations"
    )
    # Blank for an invitation the editor shares as a link or QR code
    # instead of sending it by e-mail.
    email = models.EmailField(blank=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    objects = OrganizationInvitationQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.email or 'link'} → {self.organization}"

    def issue_token(self) -> str:
        """A fresh token, valid for the next 14 days. Returns it in clear, once."""
        token = secrets.token_urlsafe(32)
        self.token_hash = hash_token(token)
        self.created_at = timezone.now()
        self.expires_at = self.created_at + INVITATION_LIFETIME
        return token

    @property
    def is_open(self) -> bool:
        return self.accepted_at is None and self.expires_at > timezone.now()
