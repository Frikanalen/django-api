from django.apps import AppConfig


class StortingetConfig(AppConfig):
    """Keeps Stortinget's sittings on the schedule while they happen."""

    name = "stortinget"
    default_auto_field = "django.db.models.AutoField"
