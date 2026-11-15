from django.contrib import admin

from .models import Bulletin


@admin.register(Bulletin)
class BulletinAdmin(admin.ModelAdmin):
    fields = [
        "heading",
        "text",
        "template",
        "is_published",
        ("starts_at", "ends_at"),
        "priority",
        ("on_air", "on_infographics"),
    ]
    list_display = [
        "heading",
        "is_published",
        "starts_at",
        "ends_at",
        "priority",
        "on_air",
        "on_infographics",
        "created",
    ]
    list_filter = ["is_published", "on_air", "on_infographics", "template"]
    search_fields = ["heading", "text"]
