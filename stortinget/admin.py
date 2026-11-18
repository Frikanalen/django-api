from django.contrib import admin

from .models import KeeperBooking


@admin.register(KeeperBooking)
class KeeperBookingAdmin(admin.ModelAdmin):
    list_display = ("day", "kind", "mote_id", "grant", "created_at")
    list_filter = ("kind",)
    readonly_fields = ("grant", "kind", "mote_id", "day", "created_at")
