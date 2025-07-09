from django.apps import AppConfig
from django.db.models.signals import post_save

from django.conf import settings

from fkweb.signals import create_auth_token


class FrikanalenAppConfig(AppConfig):
    name = "fkweb"

    def ready(self):
        # register signal receivers
        import fkweb.signals  # noqa: F401

        post_save.connect(
            create_auth_token,
            get_user_model(),
        )
