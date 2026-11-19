from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField


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

        A suspended (is_rogue) organization counts as having none
        either: its content is withdrawn exactly as if nobody answered
        for it.
        """
        return self.filter(editor__isnull=False, editor__is_active=True, is_rogue=False)

    def visible_to(self, user):
        """Everything for staff, only accountable organizations otherwise."""
        if getattr(user, "is_staff", False):
            return self
        return self.with_responsible_editor()

    def presentable_to(self, user):
        """What an organization page may be fetched for.

        Accountable organizations, plus suspended ones: those stay
        reachable so their page can say why they are gone, while their
        videos and listings do not.
        """
        if getattr(user, "is_staff", False):
            return self
        return self.with_responsible_editor() | self.filter(is_rogue=True)


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
    # Ticked when Frikanalen has received the editor's signed affidavit
    # (redaktorerklaering). Receiving it is also how the editor's identity
    # is confirmed; see save().
    fkmember = models.BooleanField(
        "Member",
        default=False,
        help_text=(
            "Tick when the editor's signed affidavit has been received. This approves the "
            "membership and confirms the editor's identity."
        ),
    )
    # Suspended for trying to subvert the general assembly: the
    # organization's content is withdrawn from the website, listings and
    # scheduling, and its page shows a suspension notice instead.
    is_rogue = models.BooleanField(
        "Suspended",
        default=False,
        help_text="Suspended member: content is hidden and the page shows a suspension notice.",
    )
    orgnr = models.CharField(blank=True, max_length=255)
    # The organization's own number, listed as the editor's contact
    # number. The editor's personal number is never published.
    phone_number = PhoneNumberField(
        blank=True,
        region="NO",
        help_text="The organization's phone number, listed with the editor information.",
        verbose_name="phone number",
    )
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

    def save(self, *args, **kwargs):
        # The signed affidavit names the editor and carries their
        # signature, so receiving it confirms who they are: there is no
        # separate step for it. Only on the way in to membership, so a
        # later change of editor does not confirm someone who has not
        # signed anything.
        becomes_member = self.fkmember and not (
            self.pk and Organization.objects.filter(pk=self.pk, fkmember=True).exists()
        )
        super().save(*args, **kwargs)
        if becomes_member and self.editor_id:
            from fk.models.user import User

            User.objects.filter(pk=self.editor_id).update(identity_confirmed=True)
            if Organization.editor.is_cached(self):
                self.editor.identity_confirmed = True
