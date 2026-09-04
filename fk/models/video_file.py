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
    # A single low bitrate rung, encoded first so that a freshly
    # uploaded video is playable in minutes rather than after the whole
    # ladder finishes. It is transient by design: once `dash` exists for
    # the same video, ingest deletes the preview and its segments, and a
    # player should prefer `dash` whenever both are present.
    DASH_PREVIEW = "dash_preview", "Transient low-quality MPEG-DASH manifest"
    WEBM_MED = "webm_med", "Medium-quality WebM"

    @property
    def mime_type(self) -> str | None:
        """How the file is served when the variant determines it."""
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
    VideoFileVariant.LARGE_THUMB: "image/jpeg",
    VideoFileVariant.BROADCAST: "video/DV",
    VideoFileVariant.VC1: "video/vc1",
    VideoFileVariant.MED_THUMB: "image/jpeg",
    VideoFileVariant.SMALL_THUMB: "image/jpeg",
    VideoFileVariant.ORIGINAL: "application/octet-stream",
    VideoFileVariant.THEORA: "video/ogg",
    VideoFileVariant.SRT: "application/x-subrip",
    VideoFileVariant.DASH: "application/dash+xml",
    VideoFileVariant.DASH_PREVIEW: "application/dash+xml",
    VideoFileVariant.WEBM_MED: "video/webm",
}


class VideoFile(models.Model):
    id = models.AutoField(primary_key=True)
    # uploader = models.ForeignKey(User) # Not migrated
    # No index of its own: unique_variant_per_video below is a btree on
    # (video, variant), and a video_id-only lookup uses its leading column
    # just as well. A second index on video_id would only cost writes.
    video = models.ForeignKey("Video", on_delete=models.CASCADE, db_index=False)
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
    # What the field is for is telling apart "this video has DASH" from
    # "this video has *current* DASH", so that a profile change can be
    # backfilled without crawling the archive.
    #
    # Zero is the sentinel rather than NULL, and not nullable at all.
    # Ingest numbers its profile templates from 1, so 0 is a revision no
    # template can ever claim, and it is what every row already in the
    # table honestly means: made before any of this was recorded. Keeping
    # the column NOT NULL is also what lets the planning query be a plain
    # `profile_revision__lt` -- under three-valued logic a NULL row would
    # fall out of `< 2`, and those rows are precisely the ones ingest most
    # needs to find.
    profile_revision = models.PositiveSmallIntegerField(
        default=0,
        help_text=(
            "Revision of the encoding profile that produced this file. "
            "0 means it predates profile tracking."
        ),
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
