from django.apps import AppConfig


class FkConfig(AppConfig):
    name = "fk"

    def ready(self):
        from . import signals  # noqa: F401  (connects the receivers)
