from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MinValueValidator
from django.db import models

from .category import Category
from .organization import Organization


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
    # Retire, use description instead
    header = models.TextField(blank=True, null=True, max_length=2048)
    name = models.CharField(max_length=255)
    description = models.CharField(blank=True, null=True, max_length=2048)
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
    ref_url = models.CharField(blank=True, max_length=1024, help_text="URL for reference")
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
        get_latest_by = "uploaded_time"
        ordering = ("-id",)
        constraints = [
            # A negative length is not a shorter programme, it is corrupt
            # data, and the schedulers do arithmetic on this field.
            models.CheckConstraint(
                condition=models.Q(duration__gte=timedelta(0)),
                name="video_duration_not_negative",
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

    def videofile_url(self, fsname) -> str:
        return self.videofile_set.get(format__fsname=fsname).location(relative=True)

    def small_thumbnail_url(self) -> str:
        try:
            video_file = self.videofile_set.get(video=self, format__fsname="small_thumb")
        except ObjectDoesNotExist:
            return "/static/default_small_thumbnail.png"
        return settings.FK_MEDIA_URLPREFIX + video_file.location(relative=True)

    def large_thumbnail_url(self) -> str:
        try:
            video_file = self.videofile_set.get(video=self, format__fsname="large_thumb")
        except ObjectDoesNotExist:
            return "/static/default_large_thumbnail.png"
        return settings.FK_MEDIA_URLPREFIX + video_file.location(relative=True)

    def ogv_url(self) -> str | None:
        # None where the thumbnail methods fall back to a placeholder:
        # a video with no theora file has no OGV URL to offer, and the
        # API exposes the field as null. Pinned by test_ogv_url.
        try:
            return settings.FK_MEDIA_URLPREFIX + self.videofile_url("theora")
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
        for videofile in self.videofile_set.all().filter(format__vod_publish=True):
            url = settings.FK_MEDIA_URLPREFIX + videofile.location(relative=True)
            vodfiles.append({"url": url, "mime_type": videofile.format.mime_type})
        return vodfiles

    def get_absolute_url(self):
        return f"/video/{self.id}/"
