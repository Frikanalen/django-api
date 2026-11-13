# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
from base64 import b64encode

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.template.response import TemplateResponse
from django.utils import timezone

from fk.forms import UserChangeForm, UserCreationForm
from fk.models import (
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
    list_display = ("name", "creator", "organization", "series", "episode_number")
    inlines = [VideoFileInline, ProgramImageInline, LiveTransmissionInline]
    search_fields = ["name", "description", "organization__name", "creator__email"]
    list_filter = ("proper_import", "is_filler", "publish_on_web", "has_tono_records")


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
    list_display = ("name", "organization", "enabled", "pull_target")
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
