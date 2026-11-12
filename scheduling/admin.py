"""Staff's tools for grants, slots and fill rules.

Grant edits go through the service layer, so the admin cannot bypass
the rules: the items must fit, and a compiled grant is recompiled.
"""

from datetime import timedelta
from typing import ClassVar

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from scheduling import broadcast_time, services
from scheduling.models import (
    AirtimeGrant,
    AirtimeGrantRequest,
    FillRule,
    GrantExemptionRequest,
    GrantItem,
    Playlist,
    RecurringSlot,
    RecurringSlotCreationRequest,
    RecurringSlotOwnershipRequest,
    ReviewStatus,
)


class GrantItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        grant = self.instance
        if not grant.starttime or not grant.endtime:
            return
        videos = [
            form.cleaned_data["video"]
            for form in self.forms
            if form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("video")
        ]
        total = sum((v.duration for v in videos), timedelta(0))
        if total > grant.endtime - grant.starttime:
            raise ValidationError(f"The items run {total}, longer than the window.")


class GrantItemInline(admin.TabularInline):
    model = GrantItem
    formset = GrantItemFormSet
    extra = 1
    fields = ("position", "video")
    autocomplete_fields = ("video",)
    ordering = ("position",)


class AirtimeGrantForm(forms.ModelForm):
    class Meta:
        model = AirtimeGrant
        fields = (
            "organization",
            "starttime",
            "endtime",
            "locks_at",
            "lock_exempt",
            "fill_rule",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["locks_at"].required = False
        self.fields["locks_at"].help_text += " Leave empty for the default."

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("locks_at") and cleaned.get("starttime"):
            cleaned["locks_at"] = broadcast_time.default_locks_at(cleaned["starttime"])
            self.instance.locks_at = cleaned["locks_at"]
        return cleaned


@admin.register(AirtimeGrant)
class AirtimeGrantAdmin(admin.ModelAdmin):
    form = AirtimeGrantForm
    inlines = [GrantItemInline]
    list_display = ("__str__", "organization", "starttime", "endtime", "state", "locks_at")
    list_filter = ("state", "lock_exempt", "organization")
    list_select_related = ("organization",)
    readonly_fields = ("state", "origin_slot")
    date_hierarchy = "starttime"
    ordering = ("starttime",)
    actions = ("compile_now", "cancel")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            # A grant's window is fixed once issued: cancel it and grant
            # the airtime again instead of moving it under its list.
            fields += ["organization", "starttime", "endtime"]
        return fields

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        grant = form.instance
        # Positions are renumbered from 0 in the order staff gave them.
        for position, item in enumerate(grant.items.order_by("position", "id")):
            if item.position != position:
                GrantItem.objects.filter(pk=item.pk).update(position=position)
        if grant.state == AirtimeGrant.State.COMPILED:
            if services.compile_grant(grant):
                self.message_user(request, "Recompiled the grant.")
            else:
                self.message_user(
                    request,
                    "Could not recompile: something else is scheduled in the window.",
                    level=messages.ERROR,
                )

    @admin.action(description="Compile selected grants now")
    def compile_now(self, request, queryset):
        compiled = [g for g in queryset.exclude(state=AirtimeGrant.State.CANCELLED)]
        failed = [g for g in compiled if not services.compile_grant(g)]
        self.message_user(request, f"Compiled {len(compiled) - len(failed)} grant(s).")
        for grant in failed:
            self.message_user(
                request, f"{grant}: something else is scheduled in the window.", messages.ERROR
            )

    @admin.action(description="Cancel selected grants")
    def cancel(self, request, queryset):
        for grant in queryset:
            services.cancel_grant(grant)
        self.message_user(request, f"Cancelled {queryset.count()} grant(s).")


@admin.register(RecurringSlot)
class RecurringSlotAdmin(admin.ModelAdmin):
    list_display = ("__str__", "organization", "frequency", "start_time", "duration", "starts_on")
    list_filter = ("frequency", "organization")
    list_select_related = ("organization", "default_fill_rule")
    autocomplete_fields = ("default_fill_rule",)


@admin.register(FillRule)
class FillRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "pool", "ordering", "mode")
    list_filter = ("pool", "ordering", "mode")
    search_fields = ("name", "organization__name")
    list_select_related = ("organization",)


@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")
    search_fields = ("name", "organization__name")
    filter_horizontal = ("videos",)


# -- Requests -----------------------------------------------------------------


class ReviewedRequestAdmin(admin.ModelAdmin):
    """Shared audited decision controls for every kind of request."""

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
        if obj is not None and obj.status != ReviewStatus.PENDING:
            fields.append("admin_comment")
        return fields

    def _decide(self, request, queryset, status):
        decided = 0
        for slot_request in queryset.filter(status=ReviewStatus.PENDING):
            comment = slot_request.admin_comment.strip() or (
                f"{ReviewStatus(status).label} in Django admin."
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
        self._decide(request, queryset, ReviewStatus.APPROVED)

    @admin.action(description="Deny selected pending requests")
    def deny_requests(self, request, queryset):
        self._decide(request, queryset, ReviewStatus.DENIED)


class RecurringSlotCreationRequestAdmin(ReviewedRequestAdmin):
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
    list_select_related = ("organization", "requested_by", "reviewed_by", "slot")
    readonly_fields = ReviewedRequestAdmin.readonly_fields + (
        "day",
        "start_time",
        "duration",
        "slot",
    )


class RecurringSlotOwnershipRequestAdmin(ReviewedRequestAdmin):
    list_display = (
        "__str__",
        "organization",
        "slot",
        "previous_organization",
        "requested_by",
        "status",
        "reviewed_by",
        "created_at",
    )
    list_select_related = (
        "organization",
        "slot",
        "previous_organization",
        "requested_by",
        "reviewed_by",
    )
    readonly_fields = ReviewedRequestAdmin.readonly_fields + (
        "slot",
        "previous_organization",
    )


class AirtimeGrantRequestAdmin(ReviewedRequestAdmin):
    list_display = (
        "__str__",
        "organization",
        "starttime",
        "endtime",
        "requested_by",
        "status",
        "created_at",
    )
    list_select_related = ("organization", "requested_by", "reviewed_by", "grant")
    readonly_fields = ReviewedRequestAdmin.readonly_fields + (
        "starttime",
        "endtime",
        "message",
        "grant",
    )


class GrantExemptionRequestAdmin(ReviewedRequestAdmin):
    list_display = ("__str__", "organization", "grant", "requested_by", "status", "created_at")
    list_select_related = ("organization", "requested_by", "reviewed_by", "grant")
    readonly_fields = ReviewedRequestAdmin.readonly_fields + ("grant", "message")


admin.site.register(AirtimeGrantRequest, AirtimeGrantRequestAdmin)
admin.site.register(GrantExemptionRequest, GrantExemptionRequestAdmin)
admin.site.register(RecurringSlotCreationRequest, RecurringSlotCreationRequestAdmin)
admin.site.register(RecurringSlotOwnershipRequest, RecurringSlotOwnershipRequestAdmin)
