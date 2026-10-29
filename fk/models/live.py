import secrets
from uuid import uuid4

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models


class LiveSource(models.Model):
    """A reusable contribution input owned by an organization."""

    organization = models.ForeignKey(
        "Organization", on_delete=models.PROTECT, related_name="live_sources"
    )
    name = models.CharField(max_length=100)
    stream_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    enabled = models.BooleanField(default=True)

    # MediaMTX path names (no leading slash). Everything lives under contrib/
    # so its WebRTC endpoints claim one prefix on the site's hostname.
    @property
    def contribution_path(self):
        return f"contrib/live/{self.stream_id}"

    @property
    def preview_path(self):
        return f"contrib/preview/{self.stream_id}"

    def __str__(self):
        return self.name


class LiveTransmission(models.Model):
    """Optional live playback configuration for one existing schedule entry.

    Timing and editorial metadata remain on the schedule entry and its Video.
    """

    schedule_item = models.OneToOneField(
        "Scheduleitem", on_delete=models.CASCADE, related_name="live_transmission"
    )
    source = models.ForeignKey(LiveSource, on_delete=models.PROTECT, related_name="transmissions")

    def __str__(self):
        return f"{self.schedule_item}: {self.source}"


class StreamCredential(models.Model):
    """A revocable encoder or reader credential.

    All but the playout reader are scoped to one source. The key ID is the
    MediaMTX username. Only the issuing caller sees the generated password;
    the database stores its Django password hash.
    """

    class Action(models.TextChoices):
        PUBLISH = "publish", "Publish"
        READ = "read", "Read"
        CONVERT = "convert", "Preview conversion worker"
        # Playout reads whichever source the schedule books, so it cannot
        # hold one credential per source without restarting for each new one.
        PLAYOUT = "playout", "Playout reader (all enabled sources)"

    source = models.ForeignKey(
        LiveSource,
        on_delete=models.CASCADE,
        related_name="credentials",
        null=True,
        blank=True,
        help_text="Required, except for the playout reader, which has no source.",
    )
    label = models.CharField(max_length=100)
    key_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    action = models.CharField(max_length=7, choices=Action.choices, default=Action.PUBLISH)
    secret_hash = models.CharField(max_length=128, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action="playout", source__isnull=True)
                | (~models.Q(action="playout") & models.Q(source__isnull=False)),
                name="streamcredential_source_unless_playout",
            ),
        ]

    def clean(self):
        super().clean()
        if self.action == self.Action.PLAYOUT and self.source_id is not None:
            raise ValidationError({"source": "The playout reader is not tied to a source."})
        if self.action != self.Action.PLAYOUT and self.source_id is None:
            raise ValidationError({"source": "This credential needs a source."})

    def generate_secret(self):
        """Set a fresh password hash and return the secret without saving."""
        secret = secrets.token_urlsafe(32)
        self.secret_hash = make_password(secret)
        return secret

    def check_secret(self, secret):
        return check_password(secret, self.secret_hash)

    def __str__(self):
        return f"{self.source or 'Playout'}: {self.label}"
