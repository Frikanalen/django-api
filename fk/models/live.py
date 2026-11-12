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
    # Set for a source MediaMTX pulls from an address, rather than one an
    # encoder pushes to. Encrypted (fk.pull_sources); never shown again.
    pull_url_encrypted = models.TextField(blank=True, default="", editable=False)
    # Scheme, host and port of that address, for showing.
    pull_target = models.CharField(max_length=300, blank=True, default="", editable=False)

    @property
    def is_pulled(self) -> bool:
        return bool(self.pull_url_encrypted)

    def set_pull_url(self, url: str) -> None:
        """Make this a pulled source (or, with "", a pushed one). Not saved."""
        from fk import pull_sources

        if url:
            self.pull_url_encrypted = pull_sources.encrypt(url)
            self.pull_target = pull_sources.redact(url)
        else:
            self.pull_url_encrypted = ""
            self.pull_target = ""

    def get_pull_url(self) -> str | None:
        from fk import pull_sources

        return pull_sources.decrypt(self.pull_url_encrypted) if self.is_pulled else None

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
    """A video whose picture comes from a live source rather than a file.

    The video stays the programme -- title, organization, category, the
    length set by hand -- and is scheduled like any other. It airs live
    until it has a file; once a recording is imported, later airings play
    the file.
    """

    video = models.OneToOneField(
        "Video", on_delete=models.CASCADE, related_name="live_transmission"
    )
    source = models.ForeignKey(LiveSource, on_delete=models.PROTECT, related_name="transmissions")

    def clean(self):
        super().clean()
        if self.source_id and self.video_id:
            if not self.source.enabled:
                raise ValidationError({"source": "The live source is disabled."})
            if self.source.organization_id != self.video.organization_id:
                raise ValidationError(
                    {"source": "The live source must belong to the video's organization."}
                )

    def __str__(self):
        return f"{self.video}: {self.source}"


class StreamCredential(models.Model):
    """A revocable encoder or reader credential.

    All but the playout reader and the pull relay are scoped to one source. The key ID is the
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
        # MediaMTX's relay fetches the address of whichever pulled source is
        # being read. Never accepted by MediaMTX itself.
        PULL = "pull", "Pull relay (addresses of all enabled pulled sources)"

    source = models.ForeignKey(
        LiveSource,
        on_delete=models.CASCADE,
        related_name="credentials",
        null=True,
        blank=True,
        help_text="Required, except for the playout reader and the pull relay.",
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
                condition=models.Q(action__in=("playout", "pull"), source__isnull=True)
                | (~models.Q(action__in=("playout", "pull")) & models.Q(source__isnull=False)),
                name="streamcredential_source_unless_global",
            ),
        ]

    def clean(self):
        super().clean()
        is_global = self.action in (self.Action.PLAYOUT, self.Action.PULL)
        if is_global and self.source_id is not None:
            raise ValidationError({"source": "This credential is not tied to a source."})
        if not is_global and self.source_id is None:
            raise ValidationError({"source": "This credential needs a source."})

    def generate_secret(self):
        """Set a fresh password hash and return the secret without saving."""
        secret = secrets.token_urlsafe(32)
        self.secret_hash = make_password(secret)
        return secret

    def check_secret(self, secret):
        return check_password(secret, self.secret_hash)

    def __str__(self):
        return f"{self.source or self.get_action_display()}: {self.label}"
