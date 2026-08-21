# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group

from fk.forms import UserChangeForm, UserCreationForm
from fk.models import (
    Category,
    IngestJob,
    Organization,
    Scheduleitem,
    SchedulePurpose,
    User,
    Video,
    VideoFile,
    WeeklySlot,
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


class VideoAdmin(admin.ModelAdmin):
    list_display = ("name", "creator", "organization")
    inlines = [VideoFileInline]
    search_fields = ["name", "description", "organization__name", "header", "creator__email"]
    list_filter = ("proper_import", "is_filler", "publish_on_web", "has_tono_records")


class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "fkmember", "orgnr")
    filter_horizontal = ("members",)
    list_filter = ("fkmember",)
    ordering = ("name",)


class ScheduleitemAdmin(admin.ModelAdmin):
    list_filter = ("starttime", "schedulereason", "video__organization", "is_live")
    list_display = ("__str__", "video", "schedulereason", "starttime", "duration", "is_live")
    # list_display_links = ('starttime', 'video',)
    # inlines = [VideoInline]
    # exclude = ('video',)
    search_fields = ["video__name", "video__organization__name"]
    ordering = ("starttime",)
    # Provenance is system-owned: visible for debugging, never
    # hand-assigned -- an item with a slot recorded is one the nightly
    # re-pick may replace.
    readonly_fields = ("weekly_slot",)

    def save_model(self, request, obj, form, change):
        if change:
            # An admin edit makes the item deliberate programming, same
            # as an API edit: the nightly re-pick must not overwrite it.
            obj.weekly_slot = None
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


class SchedulePurposeAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "videos_str",
    )
    filter_horizontal = ("direct_videos",)


class CategoryAdmin(admin.ModelAdmin):
    """Where the TV-Anytime genre mapping is maintained.

    `tva_genre` is editable straight from the list because the job it
    exists for is comparing all ten categories against each other and the
    classification scheme, which is not a task for ten separate forms.
    """

    list_display = ("name", "tva_genre")
    list_editable = ("tva_genre",)
    ordering = ("name",)


class WeeklySlotAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "day",
        "start_time",
        "duration",
        "purpose",
    )


admin.site.register(Category, CategoryAdmin)
admin.site.register(IngestJob, IngestJobAdmin)
admin.site.register(Organization, OrganizationAdmin)
admin.site.register(SchedulePurpose, SchedulePurposeAdmin)
admin.site.register(Scheduleitem, ScheduleitemAdmin)
admin.site.register(User, UserAdmin)
admin.site.register(Video, VideoAdmin)
admin.site.register(VideoFile)
admin.site.register(WeeklySlot, WeeklySlotAdmin)

# We're not using Django's built-in permissions as it is
admin.site.unregister(Group)
