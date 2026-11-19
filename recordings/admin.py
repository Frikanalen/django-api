from django.contrib import admin

from .models import RecordingRequest


@admin.register(RecordingRequest)
class RecordingRequestAdmin(admin.ModelAdmin):
    list_display = ("pk", "source", "video", "starttime", "endtime", "state", "import_attempts")
    list_filter = ("state",)
    raw_id_fields = ("video", "requested_by")
    readonly_fields = ("import_attempts", "import_requested_at", "error", "created_at")
