from django.apps import AppConfig
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save

from fkweb.signals import create_auth_token


class FrikanalenAppConfig(AppConfig):
    name = "fkweb"

    def ready(self):
        # register signal receivers

        post_save.connect(
            create_auth_token,
            get_user_model(),
        )
