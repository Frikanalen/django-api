from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class IngestState(models.TextChoices):
    """The states the ingest pipeline reports itself to be in.

    Declared beside the model rather than inside it because a nested class
    body is not in scope for the `Meta` below, and the constraint there has
    to name a state.
    """

    PENDING = "pending", "Waiting to be picked up"
    PROBING = "probing", "Inspecting the uploaded file"
    ARCHIVING = "archiving", "Storing the original"
    TRANSCODING = "transcoding", "Generating derived files"
    DONE = "done", "Finished"
    FAILED = "failed", "Failed"

    @classmethod
    def terminal(cls) -> frozenset["IngestState"]:
        """The states after which no further report is coming. A reader
        that sees one of these can stop asking."""
        return frozenset({cls.DONE, cls.FAILED})


class IngestJob(models.Model):
    """How far the ingest pipeline has got with a video's upload.

    One row per video, replaced in place: ingest reports the state it is
    currently in rather than appending to a log, so a reader never has to
    reduce several rows into a single answer. The pipeline is linear --
    probe the upload, archive the original, transcode each derived format
    -- and any failure aborts the whole of it, so there is no partial
    success to describe either.
    """

    video = models.OneToOneField(
        "Video",
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="ingest_job",
    )
    state = models.CharField(max_length=32, choices=IngestState, default=IngestState.PENDING)
    # Null rather than zero where there is nothing to count: probing and
    # archiving know only that they are running. A bar frozen at 0% for
    # minutes reads as a broken upload, where "working" reads as working.
    percentage_done = models.IntegerField(
        null=True,
        blank=True,
        default=None,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text=(
            "Progress through the current state, not through the pipeline as a whole. "
            "Null where the current state has no progress to report."
        ),
    )
    # Operator-facing: ffmpeg's complaints, and the paths it was working
    # with when it made them. Write-only over the API and read in the
    # admin, so that internal detail never reaches an organization's
    # members.
    status_text = models.TextField(max_length=1000, blank=True, default="")
    error_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "Machine-readable reason the ingest failed; empty otherwise. The wording "
            "shown to the uploader is the frontend's to choose, not ingest's."
        ),
    )
    # An ingest that dies mid-transcode leaves its last report standing
    # forever. Publishing when it was made is what lets a reader tell a
    # slow job from an abandoned one.
    updated_time = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ingest job"

        constraints = [
            models.CheckConstraint(
                condition=models.Q(percentage_done__gte=0, percentage_done__lte=100),
                name="ingest_job_percentage_within_range",
            ),
            # Carrying an error code is what failure means here. Any other
            # state carrying one describes two outcomes at once, and the
            # frontend would have to pick which half to believe.
            models.CheckConstraint(
                condition=models.Q(state=IngestState.FAILED) | models.Q(error_code=""),
                name="ingest_job_error_code_only_when_failed",
            ),
        ]

    @classmethod
    def for_video(cls, video) -> "IngestJob":
        """The job for a video, or the one its history implies.

        Nothing ever wrote this table before it was reshaped, so every
        video predating it has no row. Rather than make every caller
        special-case that, describe those videos from what is known:
        `proper_import` is precisely the flag ingest sets when it finishes,
        so a video carrying it was ingested and one without it never was.
        The synthesized job is deliberately unsaved -- reporting is
        ingest's business, not a reader's.
        """
        try:
            return video.ingest_job
        except cls.DoesNotExist:
            if video.proper_import:
                return cls(video=video, state=IngestState.DONE, percentage_done=100)
            return cls(video=video, state=IngestState.PENDING)

    def __str__(self):
        return f"{self.state} ingest of video {self.video_id}"
