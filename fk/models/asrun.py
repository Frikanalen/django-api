from django.db import models
from django.utils import timezone
from model_utils.models import TimeStampedModel


class AsRun(TimeStampedModel):
    """
    AsRun model is a historic log over what was sent through playout.
    """

    video = models.ForeignKey(
        "Video",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        help_text="""Points to the Video which was played if there is one. Can be empty if something other than a video was played. The field is mutually exclusive with `programName`.""",
    )
    program_name = models.CharField(
        max_length=160,
        blank=True,
        default="",
        help_text="""A free form text input saying what was played. If `video` is set, this field should not be set. Examples of where you'd use this field is e.g. when broadcasting live. Defaults to the empty string.""",
    )
    playout = models.CharField(
        max_length=255,
        blank=True,
        default="main",
        help_text="""The playout this entry corresponds with. This will almost always be 'main' which it defaults to.""",
    )
    played_at = models.DateTimeField(
        blank=True,
        default=timezone.now,
        help_text="Time when the playout started. Defaults to now.",
    )

    in_ms = models.IntegerField(
        blank=True,
        default=0,
        help_text="The inpoint where the video/stream was started at. In milliseconds. Normally 0 which it defaults to.",
    )
    out_ms = models.IntegerField(
        blank=True,
        null=True,
        help_text="The outpoint where the video/stream stopped. This would often be the duration of the video, or how long we live streamed a particular URL. Can be null (None) if this is 'currently happening'.",
    )

    def __str__(self):
        if self.video:
            return f"{self.playout} video: {self.video}"
        return f"{self.playout}: {self.program_name}"

    class Meta:
        ordering = (
            "-played_at",
            "-id",
        )
        indexes = [
            # An append-only playout log, listed newest-first and paged
            # through. Matching the ordering exactly lets that walk the
            # index instead of sorting the whole log per request.
            models.Index(fields=["-played_at", "-id"], name="asrun_played_at_desc_idx"),
        ]
