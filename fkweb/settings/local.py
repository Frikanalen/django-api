"""Development settings and globals."""

from .env import env, load_env_from

load_env_from(".env.local")
from .base import *  # noqa # pylint: disable=unused-import disable=wildcard-import


FK_UPLOAD_URL = "http://127.0.0.1:5000/upload"

# DEBUG CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = True
CORS_ORIGIN_ALLOW_ALL = False
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = env.str("CORS_ALLOWED_ORIGINS", default="http://localhost:3000").split(",")
