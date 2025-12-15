from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import models


class VideoManager(models.Manager):
    def public(self):
        return (
            super(VideoManager, self).get_queryset().filter(publish_on_web=True, proper_import=True)
        )

    def fillers(self):
        return (
            super()
            .get_queryset()
            .filter(
                is_filler=True,
                has_tono_records=False,
                organization__fkmember=True,
                proper_import=True,
            )
        )


class Video(models.Model):
    id = models.AutoField(primary_key=True)
    # Retire, use description instead
    header = models.TextField(blank=True, null=True, max_length=2048)
    name = models.CharField(max_length=255)
    description = models.CharField(blank=True, null=True, max_length=2048)
    categories = models.ManyToManyField("Category")
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
        "Organization", null=True, help_text="Organization for video", on_delete=models.PROTECT
    )
    ref_url = models.CharField(blank=True, max_length=1024, help_text="URL for reference")
    duration = models.DurationField(blank=True, default=timedelta(0))

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
        default=default_uuid_value.__func__,
        max_length=32,
        help_text="Video upload token (used by fkupload/frontend)",
    )

    objects = VideoManager()

    class Meta:
        get_latest_by = "uploaded_time"
        ordering = ("-id",)

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

    def medium_thumbnail_url(self) -> str:
        try:
            video_file = self.videofile_set.get(video=self, format__fsname="medium_thumb")
        except ObjectDoesNotExist:
            return "/static/default_medium_thumbnail.png"
        return settings.FK_MEDIA_URLPREFIX + video_file.location(relative=True)

    def large_thumbnail_url(self) -> str:
        try:
            video_file = self.videofile_set.get(video=self, format__fsname="large_thumb")
        except ObjectDoesNotExist:
            return "/static/default_large_thumbnail.png"
        return settings.FK_MEDIA_URLPREFIX + video_file.location(relative=True)

    def ogv_url(self) -> str:
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
