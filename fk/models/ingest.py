from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone


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

    @classmethod
    def in_progress(cls) -> frozenset["IngestState"]:
        """The states a worker holds a job in while it is doing the work.

        A job sitting in one of these is either being worked on or was
        abandoned mid-flight -- there is no third possibility, since
        nothing but a worker ever puts a job here. Which of the two it is
        can only be told from how long ago it last reported, which is
        what the claim lease decides.
        """
        return frozenset({cls.PROBING, cls.ARCHIVING, cls.TRANSCODING})


class IngestKind(models.TextChoices):
    """Where the source file a job has to read is.

    This is not a description of the work -- both kinds run the same
    pipeline -- but of what a worker needs to be able to reach in order
    to take the job. An upload's source is still in the tusd volume,
    which is ReadWriteOnce and therefore mounted by exactly one pod; a
    backfill reads the media archive, which any worker can reach. So the
    kind is what decides which workers may claim a given row.
    """

    UPLOAD = "upload", "Source is a fresh upload in the tusd volume"
    BACKFILL = "backfill", "Source is the media archive"


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
    # Higher is sooner. A member watching their own upload must not queue
    # behind a catalogue-wide backfill, so uploads enqueue above zero and
    # bulk work enqueues at it.
    priority = models.SmallIntegerField(
        default=0,
        db_index=True,
        help_text="Claim order among waiting jobs; higher is claimed sooner.",
    )
    kind = models.CharField(
        max_length=16,
        choices=IngestKind,
        default=IngestKind.UPLOAD,
        db_index=True,
        help_text="Where this job's source file is, and therefore which workers can take it.",
    )
    # Free text, and observability only: nothing is authorized or refused
    # on the strength of it. It exists so that "which pod is holding video
    # 1234" has an answer at all.
    claimed_by = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text="Identity of the worker that last claimed this job, if any.",
    )
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

    @classmethod
    def claimable(cls, kind: "IngestKind | str | None" = None) -> models.QuerySet["IngestJob"]:
        """The jobs a worker could take right now, best candidate first.

        Two disjoint sets, and the second is the interesting one. The
        first is simply everything waiting. The second is everything a
        worker took and then stopped talking about for longer than the
        lease: before this existed, an ingest interrupted by a pod
        restart was lost for good -- no exception handler runs when the
        process is killed, so the row kept whatever state it last
        reported and nothing ever looked at it again. Treating a silent
        job as available is what makes that recoverable.

        Ordered the way the queue should drain: by priority, then oldest
        report first, so nothing at a given priority can be starved.
        """
        abandoned_since = timezone.now() - settings.FK_INGEST_LEASE
        waiting = models.Q(state=IngestState.PENDING)
        abandoned = models.Q(
            state__in=IngestState.in_progress(),
            updated_time__lt=abandoned_since,
        )

        jobs = cls.objects.filter(waiting | abandoned)
        # No kind means "whatever you have" -- the single-pool case, and
        # the one the backfill CLI uses when it does not care.
        if kind is not None:
            jobs = jobs.filter(kind=kind)
        return jobs.order_by("-priority", "updated_time")

    @classmethod
    def claim(cls, kind: "IngestKind | str | None" = None, worker: str = "") -> "IngestJob | None":
        """Hand exactly one job to one worker, or report that there is none.

        `SKIP LOCKED` is the whole point of this method, not an
        optimisation of it: it is what lets several workers drain the
        same queue without ever contending and without ever being handed
        the same row. Read the row, then write it back, and the race is
        invisible in testing and duplicates work in production -- two
        workers transcode the same video, and the second one's output
        overwrites the first's halfway through.

        Returns None where nothing is claimable. That is the ordinary
        state of an idle queue, not a failure: the caller sleeps and asks
        again.
        """
        with transaction.atomic():
            job = cls.claimable(kind).select_for_update(skip_locked=True).first()
            if job is None:
                return None

            job.state = IngestState.PROBING
            job.claimed_by = worker or None
            # Whatever the previous holder last reported describes work
            # that is being started over. Probing has nothing to count
            # anyway, and leaving a reclaimed job showing the dead
            # worker's 73% would tell its uploader something false.
            job.percentage_done = None
            # `updated_time` is auto_now, so naming it here is what
            # restarts the lease: from this moment the worker has until
            # it expires to say something, and its progress reports keep
            # pushing it back.
            job.save(update_fields=["state", "claimed_by", "percentage_done", "updated_time"])
            return job

    def __str__(self):
        return f"{self.state} ingest of video {self.video_id}"
