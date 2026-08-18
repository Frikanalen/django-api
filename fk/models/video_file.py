import os

from django.conf import settings
from django.db import models


class VideoFileVariant(models.TextChoices):
    """Which rendition of a video a file is.

    Not quite a format, which is what this used to be called: `srt` is a
    subtitle track and `cloudflare_id` is not a file at all. What they
    have in common is being one of the things that exist *for* a video.

    The name doubles as the directory the file lives in -- see
    VideoFile.location() -- so these values are baked into media URLs
    and into the layout of the media volume. Renaming one is a
    filesystem migration, not a code change.
    """

    LARGE_THUMB = "large_thumb", "Large thumbnail"
    BROADCAST = "broadcast", "Broadcast master"
    VC1 = "vc1", "VC-1"
    MED_THUMB = "med_thumb", "Medium thumbnail"
    SMALL_THUMB = "small_thumb", "Small thumbnail"
    ORIGINAL = "original", "Original upload"
    THEORA = "theora", "Ogg Theora"
    SRT = "srt", "SubRip subtitles"
    CLOUDFLARE_ID = "cloudflare_id", "Cloudflare Stream identifier"
    DASH = "dash", "MPEG-DASH manifest"

    @property
    def mime_type(self) -> str | None:
        """How the file is served, where we have said so. None for the
        variants nothing asks the question about."""
        return MIME_TYPES.get(self)

    @classmethod
    def vod_published(cls) -> frozenset["VideoFileVariant"]:
        """The variants vod_files() offers to a player: sources a
        <video> element can play as they are. A DASH manifest needs a
        player to interpret it, so it is not one of them."""
        return frozenset({cls.THEORA})


# Kept beside the enum rather than in it: a member's value is the string
# itself, so metadata has to hang off a lookup either way, and a dict
# says "this is a table" more plainly than ten three-tuples would.
MIME_TYPES = {
    VideoFileVariant.THEORA: "video/ogg",
    VideoFileVariant.DASH: "application/dash+xml",
}


class VideoFile(models.Model):
    id = models.AutoField(primary_key=True)
    # uploader = models.ForeignKey(User) # Not migrated
    video = models.ForeignKey("Video", on_delete=models.CASCADE)
    variant = models.CharField(max_length=20, choices=VideoFileVariant)
    filename = models.CharField(max_length=256)
    # source = video = models.ForeignKey("VideoFile")
    integrated_lufs = models.FloatField(
        "Integrated LUFS of file defined in ITU R.128", blank=True, null=True
    )
    truepeak_lufs = models.FloatField(
        "True peak LUFS of file defined in ITU R.128", blank=True, null=True
    )
    # Set by Django on every save; the nullability only ever described
    # legacy imports, of which production had none. See migration 0020.
    created_time = models.DateTimeField(
        auto_now_add=True, help_text="Time the video file was created"
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
        constraints = [
            # Consumers look files up by (video, variant) and expect a
            # single result -- videofile_url() and the thumbnail helpers
            # all call .get() on the pair.
            models.UniqueConstraint(fields=("video", "variant"), name="unique_variant_per_video"),
        ]

    def __str__(self):
        return f"{self.get_variant_display()} of {self.video.name}"

    def location(self, relative=False):
        filename = os.path.basename(self.filename)

        path = "/".join((str(self.video.id), self.variant, filename))

        if relative:
            return path
        else:
            return f"{settings.FK_MEDIA_ROOT}/{path}"
