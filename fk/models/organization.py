from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.urls import reverse


class OrganizationQuerySet(models.QuerySet):
    def with_responsible_editor(self):
        """
        Organizations that have an ansvarlig redaktor: an editor whose
        account is still active. Nothing may be broadcast on an
        organization's behalf without one, so this is the single
        definition of "may be seen and aired" - Video reuses it rather
        than restating the condition, which is how the jukebox filter
        drifted before.

        A disabled editor account counts as none: deactivating is the
        documented alternative to deleting a user, and deleting one
        vacates the editor field outright.
        """
        return self.filter(editor__isnull=False, editor__is_active=True)

    def visible_to(self, user):
        """Everything for staff, only accountable organizations otherwise."""
        if getattr(user, "is_staff", False):
            return self
        return self.with_responsible_editor()


class Organization(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, max_length=255)
    search_document = models.GeneratedField(
        expression=SearchVector("name", config="norwegian", weight="A"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True
    )  # User ownership of an organization
    fkmember = models.BooleanField(default=False)
    orgnr = models.CharField(blank=True, max_length=255)
    homepage = models.CharField(
        "Link back to the organisation home page.", blank=True, null=True, max_length=255
    )

    postal_address = models.TextField(
        "Postal address for organization.", blank=True, null=True, max_length=2048
    )
    street_address = models.TextField(
        "Street address for organization.", blank=True, null=True, max_length=2048
    )

    # The user legally marked as the editor for this organization
    editor = models.ForeignKey(
        "User", on_delete=models.SET_NULL, blank=True, null=True, related_name="editor"
    )

    # Videos to feature on their frontpage, incl other members
    # featured_videos = models.ManyToManyField("Video")
    # twitter_email = models.CharField(null=True,max_length=255)
    # twitter_tags = models.CharField(null=True,max_length=255)
    # To be copied into every video they create
    # categories = models.ManyToManyField(Category)

    objects = OrganizationQuerySet.as_manager()

    class Meta:
        ordering = ("name", "-id")
        indexes = [GinIndex(fields=["search_document"], name="org_search_document_gin")]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("vod-org-video-list", kwargs={"orgid": self.id})
