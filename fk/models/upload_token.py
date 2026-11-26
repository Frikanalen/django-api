from datetime import timedelta
from secrets import token_hex

from django.db import models
from django.utils import timezone

UPLOAD_TOKEN_LIFETIME = timedelta(hours=24)


def _new_token() -> str:
    return token_hex(16)


def _expiry():
    return timezone.now() + UPLOAD_TOKEN_LIFETIME


class UploadTokenQuerySet(models.QuerySet):
    def live(self):
        return self.filter(expires__gt=timezone.now())


class UploadToken(models.Model):
    """A short-lived capability to upload to one video through ingest.

    Minted when a member of the video's organization asks for one, and
    checked by ingest when an upload starts. Tokens expire on their own
    and are deleted once the video's original has been ingested, so a
    video holds no standing upload capability; the next request simply
    mints a fresh one, which is what programme-image uploads and
    replacement files do.
    """

    video = models.ForeignKey("Video", on_delete=models.CASCADE, related_name="upload_tokens")
    token = models.CharField(max_length=32, unique=True, default=_new_token)
    created = models.DateTimeField(auto_now_add=True)
    expires = models.DateTimeField(default=_expiry, db_index=True)

    objects = UploadTokenQuerySet.as_manager()

    class Meta:
        verbose_name = "upload token"

    def __str__(self) -> str:
        return f"Upload token for video {self.video_id}, expires {self.expires:%Y-%m-%d %H:%M}"

    @classmethod
    def for_video(cls, video) -> "UploadToken":
        """The video's live token, or a new one if it has none.

        Handing out the same token while it lives keeps two open edit
        pages from invalidating each other. A token about to expire is
        not handed out, so an upload started from it has time to begin.
        """
        cls.objects.filter(expires__lte=timezone.now()).delete()
        token = (
            cls.objects.filter(video=video, expires__gt=timezone.now() + timedelta(hours=1))
            .order_by("-expires")
            .first()
        )
        return token or cls.objects.create(video=video)

    @classmethod
    def is_valid(cls, video, token: str) -> bool:
        return cls.objects.live().filter(video=video, token=token).exists()
