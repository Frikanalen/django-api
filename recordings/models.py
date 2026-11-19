"""A request to record a live source for a stretch of time.

Nothing records unless someone asks: a request names the source, the
times, and the video whose original the recording becomes. The times are
exactly what ends up in the file, whatever the schedule airs. MediaMTX
records the source on a path of the request's own (see
:mod:`recordings.reconcile`), and ingest archives the requested window as
the video's original once the request has ended.
"""

from django.conf import settings
from django.db import models


class RecordingRequest(models.Model):
    class State(models.TextChoices):
        WAITING = "waiting", "Venter"
        RECORDING = "recording", "Tar opp"
        IMPORTING = "importing", "Opptaket behandles"
        ARCHIVED = "archived", "Arkivert"
        FAILED = "failed", "Feilet"
        CANCELLED = "cancelled", "Avbrutt"

    source = models.ForeignKey(
        "fk.LiveSource", on_delete=models.PROTECT, related_name="recording_requests"
    )
    # The video the recording becomes the original of.
    video = models.ForeignKey(
        "fk.Video", on_delete=models.CASCADE, related_name="recording_requests"
    )
    starttime = models.DateTimeField()
    endtime = models.DateTimeField()
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    state = models.CharField(max_length=16, choices=State, default=State.WAITING)
    # Hand-offs to ingest: how many, and when the last one was.
    import_attempts = models.PositiveSmallIntegerField(default=0)
    import_requested_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starttime"]
        indexes = [models.Index(fields=["state", "starttime"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(endtime__gt=models.F("starttime")),
                name="recordingrequest_ends_after_start",
            )
        ]

    def __str__(self):
        return f"Opptak {self.pk} av {self.source} {self.starttime:%Y-%m-%d %H:%M}–{self.endtime:%H:%M}"

    @property
    def path(self) -> str:
        """The MediaMTX path that records this request."""
        return f"contrib/record/{self.pk}"
