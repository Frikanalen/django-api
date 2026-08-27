# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
from typing import ClassVar

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db.models import Count

from fk.forms import UserChangeForm, UserCreationForm
from fk.models import (
    Category,
    IngestJob,
    Organization,
    ProgramImage,
    Scheduleitem,
    Series,
    SlotSourceType,
    User,
    Video,
    VideoFile,
    WeeklySlot,
    WeeklySlotCreationRequest,
    WeeklySlotOwnershipRequest,
    WeeklySlotRequestStatus,
    WeeklySlotSource,
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


class VideoAdmin(admin.ModelAdmin):
    list_display = ("name", "creator", "organization", "series", "episode_number")
    inlines = [VideoFileInline, ProgramImageInline]
    search_fields = ["name", "description", "organization__name", "creator__email"]
    list_filter = ("proper_import", "is_filler", "publish_on_web", "has_tono_records")


class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "fkmember", "orgnr")
    filter_horizontal = ("members",)
    list_filter = ("fkmember",)
    ordering = ("name",)


class SeriesAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")
    list_filter = ("organization",)
    readonly_fields = ("image_url",)
    search_fields = ("name", "synopsis", "organization__name")
    ordering = ("organization__name", "name")


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


class WeeklySlotInline(admin.TabularInline):
    """The slots a source fills, shown from the source's side.

    Read-only: a slot is airtime, and airtime is edited where airtime
    lives. This is here so that "what does this source actually do?" has
    an answer on the page, rather than requiring a trawl of the slot
    list.
    """

    model = WeeklySlot
    extra = 0
    fields = ("day", "start_time", "duration")
    readonly_fields = fields
    can_delete = False
    show_change_link = True
    verbose_name_plural = "Slots filled from this source"

    def has_add_permission(self, request, obj=None):
        return False


class WeeklySlotSourceAdmin(admin.ModelAdmin):
    """A source answers "what airs here?" for a recurring slot.

    The form is split along the two questions the model actually asks:
    which videos are candidates, and which one of them goes on the air.
    `eligible_videos` reports the answer the scheduler would get right
    now -- including the videos it silently drops -- because a source
    that quietly picks nothing looks identical to one that works.
    """

    list_display = (
        "__str__",
        "type",
        "strategy",
        "organization",
        "slot_count",
        "video_count",
    )
    list_filter = ("type", "strategy")
    search_fields = ("name", "organization__name")
    filter_horizontal = ("direct_videos",)
    inlines = [WeeklySlotInline]
    readonly_fields = ("eligible_videos",)
    fieldsets = (
        (None, {"fields": ("name",)}),
        (
            "Which videos are candidates",
            {
                "fields": ("type", "organization", "direct_videos"),
                "description": (
                    "Only the field matching the chosen type is read; the other is ignored."
                ),
            },
        ),
        (
            "Which candidate airs",
            {
                "fields": ("strategy",),
                "description": (
                    "Applied afresh every time a slot comes round, so the answer may "
                    "change between one week and the next."
                ),
            },
        ),
        ("Right now", {"fields": ("eligible_videos",)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_slot_count=Count("weeklyslot"))

    @admin.display(description="slots", ordering="_slot_count")
    def slot_count(self, obj):
        return obj._slot_count

    @admin.display(description="videos")
    def video_count(self, obj):
        """How many videos a source has, as eligible-of-total.

        This column used to dump every title, which made the changelist
        unreadable at the width a list is read for. The number that
        matters here is whether the scheduler has anything to pick from;
        which videos those are is a question for the change page.
        """
        if obj.type == SlotSourceType.ORGANIZATION and obj.organization_id is None:
            return "--"
        try:
            total = obj.candidate_videos().count()
        except ValueError:
            # An unhandled type. The change page says so at length;
            # a changelist cell has no room to explain.
            return "--"
        eligible = obj.videos_queryset().count()
        return str(total) if eligible == total else f"{eligible} of {total}"

    @admin.display(description="Eligible videos")
    def eligible_videos(self, obj=None):
        """What the scheduler would find, and what it threw away.

        The two filters in `videos_queryset` are invisible to an editor:
        a failed import and an organization with no responsible editor
        both just produce a source that never airs anything."""
        if obj is None or obj.pk is None:
            return "Save the source to see what it would pick from."
        if obj.type == SlotSourceType.ORGANIZATION and obj.organization_id is None:
            return "Type is organization, but no organization is set, so nothing is eligible."
        try:
            candidates = obj.candidate_videos()
        except ValueError as exc:
            return str(exc)

        total = candidates.count()
        if not total:
            return "The pool is empty."
        eligible = obj.videos_queryset().count()
        reasons = []
        broken = candidates.filter(proper_import=False).count()
        if broken:
            reasons.append(f"{broken} did not import cleanly")
        unattended = (
            candidates.filter(proper_import=True)
            .exclude(organization__in=Organization.objects.with_responsible_editor())
            .count()
        )
        if unattended:
            reasons.append(f"{unattended} from an organization with no responsible editor")
        summary = f"{eligible} of {total} videos eligible"
        return f"{summary} -- {', '.join(reasons)}." if reasons else f"{summary}."


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
    """A recurring hole in the schedule: when airtime repeats, and which
    source fills it. A slot with no source still reserves the airtime --
    it just means nothing is placed there automatically."""

    list_display = (
        "__str__",
        "day",
        "start_time",
        "duration",
        "end_time",
        "organization",
        "source",
    )
    list_filter = ("day", "organization", "source")
    list_select_related = ("organization", "source")
    autocomplete_fields = ("source",)


class WeeklySlotRequestAdmin(admin.ModelAdmin):
    """Shared audited decision controls for the two request types."""

    list_filter: ClassVar[tuple[str, ...]] = ("status", "organization")
    actions = ("approve_requests", "deny_requests")
    readonly_fields: ClassVar[tuple[str, ...]] = (
        "organization",
        "requested_by",
        "status",
        "reviewed_by",
        "created_at",
        "reviewed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.status != WeeklySlotRequestStatus.PENDING:
            fields.append("admin_comment")
        return fields

    def _decide(self, request, queryset, status):
        decided = 0
        for slot_request in queryset.filter(status=WeeklySlotRequestStatus.PENDING):
            comment = slot_request.admin_comment.strip() or (
                f"{WeeklySlotRequestStatus(status).label} in Django admin."
            )
            try:
                slot_request.decide(admin=request.user, status=status, comment=comment)
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{slot_request}: {'; '.join(error.messages)}",
                    level=messages.ERROR,
                )
                continue
            decided += 1
        self.message_user(request, f"{decided} request(s) {status}.")

    @admin.action(description="Approve selected pending requests")
    def approve_requests(self, request, queryset):
        self._decide(request, queryset, WeeklySlotRequestStatus.APPROVED)

    @admin.action(description="Deny selected pending requests")
    def deny_requests(self, request, queryset):
        self._decide(request, queryset, WeeklySlotRequestStatus.DENIED)


class WeeklySlotCreationRequestAdmin(WeeklySlotRequestAdmin):
    list_display = (
        "__str__",
        "organization",
        "day",
        "start_time",
        "duration",
        "requested_by",
        "status",
        "reviewed_by",
        "created_at",
    )
    list_filter = ("status", "organization", "day")
    list_select_related = ("organization", "requested_by", "reviewed_by", "weekly_slot")
    readonly_fields = WeeklySlotRequestAdmin.readonly_fields + (
        "day",
        "start_time",
        "duration",
        "weekly_slot",
    )


class WeeklySlotOwnershipRequestAdmin(WeeklySlotRequestAdmin):
    list_display = (
        "__str__",
        "organization",
        "weekly_slot",
        "previous_organization",
        "requested_by",
        "status",
        "reviewed_by",
        "created_at",
    )
    list_select_related = (
        "organization",
        "weekly_slot",
        "previous_organization",
        "requested_by",
        "reviewed_by",
    )
    readonly_fields = WeeklySlotRequestAdmin.readonly_fields + (
        "weekly_slot",
        "previous_organization",
    )


admin.site.register(Category, CategoryAdmin)
admin.site.register(IngestJob, IngestJobAdmin)
admin.site.register(Organization, OrganizationAdmin)
admin.site.register(WeeklySlotSource, WeeklySlotSourceAdmin)
admin.site.register(WeeklySlotCreationRequest, WeeklySlotCreationRequestAdmin)
admin.site.register(WeeklySlotOwnershipRequest, WeeklySlotOwnershipRequestAdmin)
admin.site.register(Scheduleitem, ScheduleitemAdmin)
admin.site.register(Series, SeriesAdmin)
admin.site.register(User, UserAdmin)
admin.site.register(Video, VideoAdmin)
admin.site.register(VideoFile)
admin.site.register(WeeklySlot, WeeklySlotAdmin)

# We're not using Django's built-in permissions as it is
admin.site.unregister(Group)
