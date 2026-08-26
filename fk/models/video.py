from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models

from .category import Category
from .organization import Organization
from .video_file import VideoFileVariant


class VideoManager(models.Manager):
    def with_responsible_editor(self):
        """
        Videos an organization may answer for, reusing Organization's
        definition so the rule has exactly one home.
        """
        return (
            super()
            .get_queryset()
            .filter(organization__in=Organization.objects.with_responsible_editor())
        )

    def visible_to(self, user):
        """Everything for staff, only accountable videos otherwise."""
        if getattr(user, "is_staff", False):
            return super().get_queryset()
        return self.with_responsible_editor()

    def public(self):
        return self.with_responsible_editor().filter(publish_on_web=True, proper_import=True)

    def fillers(self):
        return self.with_responsible_editor().filter(
            is_filler=True,
            has_tono_records=False,
            organization__fkmember=True,
            proper_import=True,
        )


class Video(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    # A stored vector means PostgreSQL can answer searches with the GIN
    # index below rather than re-tokenizing every video on every request.
    # Organization names have their own vector because an index cannot span
    # the foreign-key join.
    search_document = models.GeneratedField(
        expression=(
            SearchVector("name", config="norwegian", weight="A")
            + SearchVector("description", config="norwegian", weight="B")
        ),
        output_field=SearchVectorField(),
        db_persist=True,
    )
    # Quoted so the subscript is never evaluated: ManyToManyField is not
    # subscriptable at runtime, only to django-stubs.
    categories: "models.ManyToManyField[Category, models.Model]" = models.ManyToManyField(Category)
    creator = models.ForeignKey(get_user_model(), on_delete=models.PROTECT)
    has_tono_records = models.BooleanField(default=False)
    is_filler = models.BooleanField(
        "Play automatically?",
        help_text="You still have the editorial responsibility.  Only affect videos from members.",
        default=False,
    )
    publish_on_web = models.BooleanField(default=True)

    proper_import = models.BooleanField(
        default=False, help_text="Has the video been properly imported?"
    )
    played_count_web = models.IntegerField(
        default=0, help_text="Number of times it has been played"
    )
    # Django sets both of these on save, so the nullability only ever
    # described legacy rows imported around it. Those were backfilled
    # from uploaded_time (falling back to updated_time) in 0020.
    created_time = models.DateTimeField(
        auto_now_add=True, help_text="Time the program record was created"
    )
    updated_time = models.DateTimeField(
        auto_now=True, help_text="Time the program record has been updated"
    )
    uploaded_time = models.DateTimeField(
        blank=True, null=True, help_text="Time the original video for the program was uploaded"
    )
    framerate = models.IntegerField(
        default=25000, help_text="Framerate of master video in thousands / second"
    )
    organization = models.ForeignKey(
        "Organization", help_text="Organization for video", on_delete=models.PROTECT
    )
    series = models.ForeignKey(
        "Series",
        blank=True,
        null=True,
        related_name="videos",
        on_delete=models.PROTECT,
        help_text="Series this video is an episode of, if any.",
    )
    episode_number = models.PositiveIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1)],
        help_text="Episode number within the series. Leave blank when the series is unordered.",
    )
    ref_url = models.CharField(blank=True, max_length=1024, help_text="URL for reference")

    # Published as <Language> in the TV-Anytime feed. A free-text tag rather
    # than a choice list because Frikanalen carries minority-language
    # programming -- Sami, Kven, and the languages its immigrant member
    # organizations broadcast in -- and a fixed list would quietly push all
    # of those into "other".
    spoken_language = models.CharField(
        "Spoken language",
        blank=True,
        max_length=32,
        default="no",
        validators=[
            RegexValidator(
                # BCP 47 in the shape TV-Anytime accepts: a primary subtag,
                # optionally refined. Deliberately permissive about which
                # tags exist -- this rules out prose in the field, not
                # unusual languages.
                regex=r"^[a-zA-Z]{2,8}(-[a-zA-Z0-9]{1,8})*$",
                message="Use a language tag such as 'no', 'nn', 'se' or 'en-GB'.",
            )
        ],
        help_text=(
            "Language tag for the speech in this programme, e.g. 'no', 'nn', "
            "'se' (Northern Sami) or 'en'. Leave blank if there is no speech."
        ),
    )

    # Published as <ParentalGuidance><MinimumAge>. Null means we are not
    # making a claim, which is not the same as a 0 rating: Frikanalen does
    # not rate its members' programmes centrally, so most rows stay null.
    minimum_age = models.PositiveSmallIntegerField(
        "Minimum age",
        blank=True,
        null=True,
        choices=[(0, "All ages"), (6, "6"), (9, "9"), (12, "12"), (15, "15"), (18, "18")],
        help_text=(
            "Norwegian age rating (Medietilsynet's scale). Leave blank when the "
            "programme has not been rated -- blank publishes no rating at all, "
            "while 'All ages' publishes one."
        ),
    )

    duration = models.DurationField(
        blank=True,
        default=timedelta(0),
        validators=[MinValueValidator(timedelta(0))],
    )

    # This field is used by the new ingest.
    media_metadata = models.JSONField(blank=True, default=dict)

    # This function is a workaround so we can pass a callable
    # to default argument. Otherwise, the migration analyser evaluates
    # the UUID and then concludes a new default value has been assigned,
    # helpfully generating a migration.
    #
    # upload_token should be migrated to a UUIDField, and that transition
    # needs to be tested throughout the upload chain.
    # upload_token = models.UUIDField(blank=True, default=uuid.uuid4,
    #                 editable=False,
    #                 help_text='Video upload token (used by fkupload/frontend)')

    @staticmethod
    def default_uuid_value():
        return uuid4().hex

    upload_token = models.CharField(
        blank=True,
        # Unwrapping the staticmethod is what keeps the default a callable
        # Django can serialize back to this same name; passing the
        # descriptor itself makes the autodetector see a changed default
        # and write a migration on every run. mypy reads the name in the
        # class body as a plain function, which has no __func__.
        default=default_uuid_value.__func__,  # type: ignore[attr-defined]
        max_length=32,
        help_text="Video upload token (used by fkupload/frontend)",
    )

    objects = VideoManager()

    class Meta:
        get_latest_by = "created_time"
        ordering = ("-id",)
        indexes = [GinIndex(fields=["search_document"], name="video_search_document_gin")]
        constraints = [
            # A negative length is not a shorter programme, it is corrupt
            # data, and the schedulers do arithmetic on this field.
            models.CheckConstraint(
                condition=models.Q(duration__gte=timedelta(0)),
                name="video_duration_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(series__isnull=False) | models.Q(episode_number__isnull=True),
                name="video_episode_number_requires_series",
            ),
            models.UniqueConstraint(
                fields=("series", "episode_number"),
                condition=models.Q(episode_number__isnull=False),
                name="video_episode_number_unique_per_series",
            ),
        ]

    def __str__(self):
        return self.name

    def is_public(self):
        return self.publish_on_web and self.proper_import

    def tags(self):
        tags = []
        if self.has_tono_records:
            tags.append("tono")
        if self.publish_on_web:
            tags.append("www")
        if self.is_filler:
            tags.append("filler")
        return ", ".join(tags)

    def category_list(self):
        categories = self.categories.filter(video=self)
        return categories

    def schedule(self):
        return self.scheduleitem_set.all()

    def first_broadcast(self):
        return self.scheduleitem_set.all().order_by("starttime").first()

    def last_broadcast(self):
        return self.scheduleitem_set.all().order_by("-starttime").first()

    def videofile_url(self, variant: VideoFileVariant) -> str:
        return self.videofile_set.get(variant=variant).location(relative=True)

    def small_thumbnail_url(self) -> str:
        try:
            video_file = self.videofile_set.get(video=self, variant=VideoFileVariant.SMALL_THUMB)
        except ObjectDoesNotExist:
            return "/static/default_small_thumbnail.png"
        return settings.FK_MEDIA_URLPREFIX + video_file.location(relative=True)

    def large_thumbnail_url(self) -> str:
        try:
            video_file = self.videofile_set.get(video=self, variant=VideoFileVariant.LARGE_THUMB)
        except ObjectDoesNotExist:
            return "/static/default_large_thumbnail.png"
        return settings.FK_MEDIA_URLPREFIX + video_file.location(relative=True)

    def ogv_url(self) -> str | None:
        # None where the thumbnail methods fall back to a placeholder:
        # a video with no theora file has no OGV URL to offer, and the
        # API exposes the field as null. Pinned by test_ogv_url.
        try:
            return settings.FK_MEDIA_URLPREFIX + self.videofile_url(VideoFileVariant.THEORA)
        except ObjectDoesNotExist:
            return None

    def vod_files(self):
        """Return a list of video files fit for the video on demand
        presentation, with associated MIME type.

        [
          {
            'url: 'https://../.../file.ogv',
            'mime_type': 'video/ogg',
          },
        ]

        """

        vodfiles = []
        published = VideoFileVariant.vod_published()
        for videofile in self.videofile_set.all().filter(variant__in=published):
            url = settings.FK_MEDIA_URLPREFIX + videofile.location(relative=True)
            mime_type = VideoFileVariant(videofile.variant).mime_type
            vodfiles.append({"url": url, "mime_type": mime_type})
        return vodfiles

    def get_absolute_url(self):
        return f"/video/{self.id}/"
