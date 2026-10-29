from django.db import models


class ImageRole(models.TextChoices):
    """Editorial image roles from NorDig HowRelatedNordigCS:2022."""

    NETWORK_LOGO = "network_logo", "Network logo"
    CHANNEL_LOGO = "channel_logo", "Channel logo"
    SHOW_LOGO = "show_logo", "Show logo"
    SHOW_STILL = "show_still", "Show still"
    EPISODE_STILL = "episode_still", "Episode still"
    KEY_ART_TITLED = "key_art_titled", "Key art with title"
    KEY_ART_UNTITLED = "key_art_untitled", "Key art without title"
    BEHIND_THE_SCENES = "behind_the_scenes", "Behind the scenes"
    LOCATION = "location", "Location"
    NEWS_EVENT = "news_event", "News event"
    PORTRAIT_HEADSHOT = "portrait_headshot", "Portrait, headshot"
    PORTRAIT_HALF_BODY = "portrait_half_body", "Portrait, half body"
    PORTRAIT_FULL_BODY = "portrait_full_body", "Portrait, full body"
    CAST_ENSEMBLE = "cast_ensemble", "Cast ensemble"

    @property
    def how_related(self) -> str:
        return f"urn:nordig:metadata:cs:HowRelatedNordigCS:2022:{IMAGE_ROLE_TERMS[self]}"


IMAGE_ROLE_TERMS = {
    ImageRole.NETWORK_LOGO: "19.1",
    ImageRole.CHANNEL_LOGO: "19.2",
    ImageRole.SHOW_LOGO: "19.3",
    ImageRole.SHOW_STILL: "19.4",
    ImageRole.EPISODE_STILL: "19.5",
    ImageRole.KEY_ART_TITLED: "19.6",
    ImageRole.KEY_ART_UNTITLED: "19.7",
    ImageRole.BEHIND_THE_SCENES: "19.8",
    ImageRole.LOCATION: "19.9",
    ImageRole.NEWS_EVENT: "19.10",
    ImageRole.PORTRAIT_HEADSHOT: "19.11.1",
    ImageRole.PORTRAIT_HALF_BODY: "19.11.2",
    ImageRole.PORTRAIT_FULL_BODY: "19.11.3",
    ImageRole.CAST_ENSEMBLE: "19.12",
}


class ImageMediaType(models.TextChoices):
    JPEG = "image/jpeg", "JPEG"
    PNG = "image/png", "PNG"
    WEBP = "image/webp", "WebP"


class ProgramImage(models.Model):
    """Metadata for an editorial image already published by ingest.

    Django deliberately never opens or writes the archive file.
    """

    video = models.ForeignKey("Video", related_name="images", on_delete=models.CASCADE)
    role = models.CharField(max_length=32, choices=ImageRole.choices)
    filename = models.CharField(max_length=300, unique=True)
    media_type = models.CharField(max_length=16, choices=ImageMediaType.choices)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    created_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("video_id", "role", "id")

    def __str__(self) -> str:
        return f"{self.get_role_display()} for {self.video.name}"
