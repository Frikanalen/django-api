from django.apps import AppConfig


class RecordingsConfig(AppConfig):
    """Recordings of live sources, made on request and archived as a video's original."""

    name = "recordings"
    default_auto_field = "django.db.models.AutoField"
