# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
from base64 import b64encode

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.text import capfirst

from fk.forms import UserChangeForm, UserCreationForm
from fk.models import (
    AffidavitStatus,
    AffidavitSubmission,
    Category,
    IngestJob,
    LiveSource,
    LiveTransmission,
    Organization,
    ProgramImage,
    Scheduleitem,
    Series,
    StreamCredential,
    User,
    Video,
    VideoFile,
)
from fk.stream_connections import (
    PLACEHOLDER_STREAM_ID,
    blackmagic_web_presenter_config,
    connection_examples,
)


class UserAdmin(BaseUserAdmin):
    # The forms to add and change user instances
    form = UserChangeForm
    add_form = UserCreationForm

    # The fields to be used in displaying the User model.
    # These override the definitions on the base UserAdmin
    # that reference specific fields on auth.User.
    list_display = ("email", "date_of_birth", "is_superuser")
    list_filter = ("is_superuser",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("date_of_birth", "phone_number", "first_name", "last_name")}),
        ("Administrative info", {"fields": ("identity_confirmed",)}),
        ("Permissions", {"fields": ("is_superuser",)}),
    )
    # add_fieldsets is not a standard ModelAdmin attribute. UserAdmin
    # overrides get_fieldsets to use this attribute when creating a user.
    add_fieldsets = (
        (
            None,
            {"classes": ("wide",), "fields": ("email", "date_of_birth", "password1", "password2")},
        ),
    )
    search_fields = ("email",)
    ordering = ("email",)
    filter_horizontal = ()


class VideoFileInline(admin.StackedInline):
    fields = ("variant", "filename")
    model = VideoFile
    extra = 0


class ProgramImageInline(admin.StackedInline):
    fields = ("role", "filename", "media_type", "width", "height")
    readonly_fields = ("filename", "media_type", "width", "height")
    model = ProgramImage
    extra = 0


class LiveTransmissionInline(admin.StackedInline):
    """Makes the video a live programme: it airs from this source until
    it has a file. The model's clean() checks the source."""

    model = LiveTransmission
    fields = ("source",)
    extra = 0
    max_num = 1


class VideoAdmin(admin.ModelAdmin):
    list_display = ("name", "creator", "organization", "series", "episode_number", "is_banned")
    inlines = [VideoFileInline, ProgramImageInline, LiveTransmissionInline]
    search_fields = ["name", "description", "organization__name", "creator__email"]
    list_filter = ("is_banned", "proper_import", "is_filler", "publish_on_web", "has_tono_records")

    # Grant lists refer to videos with PROTECT, so a delete goes through
    # the scheduling service, which takes the video out of them first. The
    # confirmation page need not list them as blocking.
    def get_deleted_objects(self, objs, request):
        from scheduling.models import GrantItem

        to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        listed = f"{capfirst(GrantItem._meta.verbose_name)}: "
        protected = [p for p in protected if not str(p).startswith(listed)]
        return to_delete, model_count, perms_needed, protected

    def delete_model(self, request, obj):
        from scheduling.services import delete_video

        delete_video(obj)

    def delete_queryset(self, request, queryset):
        from scheduling.services import delete_video

        for video in queryset:
            delete_video(video)


class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "fkmember", "is_rogue", "orgnr")
    filter_horizontal = ("members",)
    list_filter = ("fkmember", "is_rogue")
    ordering = ("name",)


class SeriesAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")
    list_filter = ("organization",)
    readonly_fields = ("image_url",)
    search_fields = ("name", "synopsis", "organization__name")
    ordering = ("organization__name", "name")


class LiveSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "enabled", "logo_position", "pull_target")
    list_filter = ("enabled", "organization")
    search_fields = ("name", "organization__name")
    # The pull address is set through the API, which validates it; here
    # only its redacted target shows.
    readonly_fields = ("stream_id", "pull_target")


class StreamCredentialAdmin(admin.ModelAdmin):
    list_display = (
        "label",
        "source",
        "action",
        "created_at",
        "last_used_at",
        "expires_at",
        "revoked_at",
    )
    list_filter = ("action", "revoked_at", "source__organization")
    search_fields = ("label", "source__name", "source__organization__name")
    fields = ("source", "label", "action", "key_id", "created_at", "expires_at", "revoked_at")
    readonly_fields = ("key_id", "created_at", "revoked_at")
    actions = ("revoke_credentials",)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return (*self.readonly_fields, "source", "action")
        return self.readonly_fields

    def response_add(self, request, obj, post_url_continue=None):
        # Called inside the admin's transaction. The cleartext secret exists
        # only in this response, never in a message/session or a model field.
        secret = obj.generate_secret()
        obj.save(update_fields=["secret_hash"])
        blackmagic_config = blackmagic_web_presenter_config(obj, secret)
        response = TemplateResponse(
            request,
            "admin/fk/streamcredential/issued.html",
            {
                **self.admin_site.each_context(request),
                "title": "Stream credential created",
                "opts": self.model._meta,
                "credential": obj,
                "secret": secret,
                "stream_path": (
                    obj.source.contribution_path
                    if obj.source
                    else LiveSource(stream_id=PLACEHOLDER_STREAM_ID).contribution_path
                ),
                "connection_examples": connection_examples(obj, secret),
                "blackmagic_config_base64": (
                    b64encode(blackmagic_config.encode()).decode() if blackmagic_config else None
                ),
            },
        )
        response["Cache-Control"] = "no-store, private"
        response["Referrer-Policy"] = "no-referrer"
        return response

    @admin.action(description="Revoke selected stream credentials", permissions=["change"])
    def revoke_credentials(self, request, queryset):
        count = queryset.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
        self.message_user(request, f"Revoked {count} stream credential(s).")


class ScheduleitemAdmin(admin.ModelAdmin):
    list_filter = ("starttime", "schedulereason", "video__organization", "is_live")
    list_display = ("__str__", "video", "schedulereason", "starttime", "duration", "is_live")
    # list_display_links = ('starttime', 'video',)
    # inlines = [VideoInline]
    # exclude = ('video',)
    search_fields = ["video__name", "video__organization__name"]
    ordering = ("starttime",)
    # Provenance is system-owned: the grant the compiler wrote the row
    # from, and the historical schedule reason. Staff entries leave both
    # empty, which is what holds them to the no-overlap constraint.
    readonly_fields = ("grant", "schedulereason", "is_live")

    def save_model(self, request, obj, form, change):
        # Whether an airing goes out live follows from its video: a live
        # programme airs from its source until it has a file.
        from scheduling.services import airs_live

        obj.is_live = obj.video is not None and airs_live(obj.video)
        super().save_model(request, obj, form, change)


class IngestJobAdmin(admin.ModelAdmin):
    """The operator's view of what ingest is doing, and what it said when
    it stopped. `status_text` is here rather than in the API precisely so
    that ffmpeg's output has somewhere to be read without showing it to
    the organization that uploaded the file."""

    list_display = ("video", "state", "percentage_done", "error_code", "updated_time")
    list_filter = ("state",)
    search_fields = ("video__name", "error_code")
    ordering = ("-updated_time",)
    # Ingest owns every one of these. An operator reads them to find out
    # what happened; editing them here would only lie to the uploader.
    readonly_fields = (
        "video",
        "state",
        "percentage_done",
        "status_text",
        "error_code",
        "updated_time",
    )

    def has_add_permission(self, request):
        return False


class CategoryAdmin(admin.ModelAdmin):
    """Where the TV-Anytime genre mapping is maintained.

    `tva_genre` is editable straight from the list because the job it
    exists for is comparing all ten categories against each other and the
    classification scheme, which is not a task for ten separate forms.
    """

    list_display = ("name", "tva_genre")
    list_editable = ("tva_genre",)
    ordering = ("name",)


class AffidavitSubmissionAdmin(admin.ModelAdmin):
    """
    The verification queue for scanned editor affidavits. Check the scan
    against the organization's details beside it, then approve it (the
    organization becomes a member and its editor's identity is confirmed)
    or reject it with a note the organization will see.
    """

    list_display = ("organization", "orgnr", "submitted_by", "submitted_at", "status")
    list_filter = ("status",)
    list_select_related = ("organization", "submitted_by")
    search_fields = ("organization__name", "organization__orgnr", "submitted_by__email")
    ordering = ("submitted_at",)
    actions = ("approve_selected",)
    fields = (
        "scan",
        "organization",
        "orgnr",
        "postal_address",
        "editor",
        "submitted_by",
        "submitted_at",
        "status",
        "reviewed_by",
        "reviewed_at",
        "review_note",
    )
    readonly_fields = tuple(f for f in fields if f != "review_note")
    change_form_template = "admin/fk/affidavitsubmission/change_form.html"

    def get_queryset(self, request):
        return super().get_queryset(request).defer("content")

    def has_add_permission(self, request):
        # Scans come from the organization's own upload, nowhere else.
        return False

    def changelist_view(self, request, extra_context=None):
        # Open on what needs doing: the queue, not the archive.
        if not request.GET:
            return HttpResponseRedirect(f"{request.path}?status__exact={AffidavitStatus.PENDING}")
        return super().changelist_view(request, extra_context)

    @admin.display(description="Org.nr.")
    def orgnr(self, obj):
        return obj.organization.orgnr

    @admin.display(description="Postal address")
    def postal_address(self, obj):
        return obj.organization.postal_address

    @admin.display(description="Editor")
    def editor(self, obj):
        editor = obj.organization.editor
        if editor is None:
            return "No editor"
        return f"{editor.first_name} {editor.last_name} <{editor.email}>"

    @admin.display(description="Scan")
    def scan(self, obj):
        url = reverse("api-organization-affidavit-file", args=[obj.organization_id, obj.pk])
        if obj.content_type.startswith("image/"):
            return format_html(
                '<a href="{}" target="_blank"><img src="{}" style="max-width: 40rem"></a>',
                url,
                url,
            )
        return format_html('<a href="{}" target="_blank">Open {}</a>', url, obj.file_name)

    def response_change(self, request, obj):
        from api.organization.affidavit import notify_submitter_of_decision

        if "_approve" in request.POST or "_reject" in request.POST:
            if obj.status != AffidavitStatus.PENDING:
                self.message_user(request, "This scan has already been reviewed.", messages.ERROR)
                return HttpResponseRedirect(request.path)
            if "_approve" in request.POST:
                obj.approve(request.user)
                self.message_user(
                    request, f"Approved: {obj.organization} is now a member.", messages.SUCCESS
                )
            else:
                if not obj.review_note.strip():
                    self.message_user(
                        request,
                        "Write a review note first: the organization needs to know what to fix.",
                        messages.ERROR,
                    )
                    return HttpResponseRedirect(request.path)
                obj.reject(request.user, obj.review_note)
                self.message_user(request, f"Rejected the scan from {obj.organization}.")
            notify_submitter_of_decision(obj)
            return HttpResponseRedirect(
                reverse("admin:fk_affidavitsubmission_changelist")
                + f"?status__exact={AffidavitStatus.PENDING}"
            )
        return super().response_change(request, obj)

    @admin.action(description="Approve selected scans (makes the organizations members)")
    def approve_selected(self, request, queryset):
        from api.organization.affidavit import notify_submitter_of_decision

        approved = 0
        for submission in queryset.filter(status=AffidavitStatus.PENDING).select_related(
            "organization", "submitted_by"
        ):
            submission.approve(request.user)
            notify_submitter_of_decision(submission)
            approved += 1
        self.message_user(request, f"Approved {approved} scan(s).", messages.SUCCESS)


admin.site.register(AffidavitSubmission, AffidavitSubmissionAdmin)
admin.site.register(Category, CategoryAdmin)
admin.site.register(IngestJob, IngestJobAdmin)
admin.site.register(Organization, OrganizationAdmin)
admin.site.register(Scheduleitem, ScheduleitemAdmin)
admin.site.register(LiveSource, LiveSourceAdmin)
admin.site.register(StreamCredential, StreamCredentialAdmin)
admin.site.register(Series, SeriesAdmin)
admin.site.register(User, UserAdmin)
admin.site.register(Video, VideoAdmin)
admin.site.register(VideoFile)

# We're not using Django's built-in permissions as it is
admin.site.unregister(Group)
