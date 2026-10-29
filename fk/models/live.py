from uuid import uuid4

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

    def __str__(self):
        return self.name


class LiveTransmission(models.Model):
    """Optional live playback configuration for one existing schedule entry.

    Timing and editorial metadata remain on the schedule entry and its Video.
    Call full_clean() when booking; validation does not affect recorded entries.
    """

    schedule_item = models.OneToOneField(
        "Scheduleitem", on_delete=models.CASCADE, related_name="live_transmission"
    )
    source = models.ForeignKey(LiveSource, on_delete=models.PROTECT, related_name="transmissions")

    def clean(self):
        super().clean()
        if not self.schedule_item_id or not self.source_id:
            return

        if not self.schedule_item.video_id:
            raise ValidationError({"schedule_item": "A live transmission requires a video."})
        if not self.source.enabled:
            raise ValidationError({"source": "The live source is disabled."})
        if self.schedule_item.video.organization_id != self.source.organization_id:
            raise ValidationError(
                {"source": "The live source must belong to the video's organization."}
            )

    def __str__(self):
        return f"{self.schedule_item}: {self.source}"
