from django.apps import AppConfig


class SchedulingConfig(AppConfig):
    name = "scheduling"
    default_auto_field = "django.db.models.AutoField"

    def ready(self):
        from . import signals  # noqa: F401  (connects the receivers)
