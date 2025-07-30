import os

from django.conf import settings
from django.db import models


# we are slowly getting these out of the database, but can't make schema changes
# until we have migrated away from the old version of Django.
FILE_FORMATS = [
    "large_thumb",
    "broadcast",
    "vc1",
    "med_thumb",
    "small_thumb",
    "original",
    "theora",
    "srt",
    "cloudflare_id",
]


class FileFormat(models.Model):
    id = models.AutoField(primary_key=True)
    description = models.TextField(unique=True, max_length=255, null=True, blank=True)
    fsname = models.CharField(max_length=20, choices=[(f, f) for f in FILE_FORMATS])
    vod_publish = models.BooleanField("Present video format to video on demand?", default=False)
    mime_type = models.CharField(max_length=256, null=True, blank=True)

    # httpprefix = models.CharField(max_length=200)
    # metadata framerate, resolution, etc?

    class Meta:
        verbose_name = "video file format"
        verbose_name_plural = "video file formats"
        ordering = ("fsname", "-id")

    def __str__(self):
        return self.fsname


class VideoFile(models.Model):
    id = models.AutoField(primary_key=True)
    # uploader = models.ForeignKey(User) # Not migrated
    video = models.ForeignKey("Video", on_delete=models.CASCADE)
    format = models.ForeignKey("FileFormat", on_delete=models.PROTECT)
    filename = models.CharField(max_length=256)
    # source = video = models.ForeignKey("VideoFile")
    integrated_lufs = models.FloatField(
        "Integrated LUFS of file defined in ITU R.128", blank=True, null=True
    )
    truepeak_lufs = models.FloatField(
        "True peak LUFS of file defined in ITU R.128", blank=True, null=True
    )
    created_time = models.DateTimeField(
        auto_now_add=True, null=True, help_text="Time the video file was created"
    )
    # metadata frames, width, height, framerate? mlt profile name?
    # edl for in/out?

    class Meta:
        verbose_name = "video file"
        verbose_name_plural = "video files"
        ordering = (
            "-video_id",
            "-id",
        )

    def __str__(self):
        return "%s version of %s" % (self.format.fsname, self.video.name)

    def location(self, relative=False):
        filename = os.path.basename(self.filename)

        path = "/".join((str(self.video.id), self.format.fsname, filename))

        if relative:
            return path
        else:
            return "/".join((settings.FK_MEDIA_ROOT, path))
