from django.db import models


class Category(models.Model):
    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    desc = models.CharField(max_length=255, blank=True)
    # How this category is published as a <Genre> in the TV-Anytime feed.
    # Held here rather than in a table in code because the mapping is an
    # editorial judgement about what Frikanalen's categories mean, not a
    # fact about the schema: "Kultur" could as defensibly be Arts as
    # Culture/Tradition, and whoever owns the categories should be able to
    # settle that without a deploy.
    #
    # Blank on purpose for the categories no controlled term honestly fits
    # -- ContentCS 2011 has no children's genre at all -- because a feed
    # that omits a genre is merely incomplete, while one that asserts the
    # wrong genre files the programme under it in every receiver's EPG.
    tva_genre = models.CharField(
        "TV-Anytime genre",
        blank=True,
        max_length=255,
        help_text=(
            "Classification scheme term this category is published as in the "
            "TV-Anytime feed, as a full href -- for example "
            "urn:tva:metadata:cs:ContentCS:2011:3.1.2 for Religion/Philosophies. "
            "Leave blank to publish no genre for this category."
        ),
    )

    class Meta:
        verbose_name = "video category"
        verbose_name_plural = "video categories"
        ordering = ("name", "-id")

    def __str__(self):
        return self.name
