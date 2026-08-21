# vim: ts=4 sts=4 expandtab ai

# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.

"""Common settings and globals."""

import logging
import sys
from os.path import abspath, basename, dirname, join, normpath

from environ import ImproperlyConfigured

from .env import env, load_env_from

logger = logging.getLogger(__name__)

load_env_from(".env")


########## CUSTOM FKBETA CONFIGURATION
# Do not publish any files with the TONO flag set on the web
WEB_NO_TONO = True

# Where all the media files are stored (Must have trailing slash)
FK_MEDIA_URLPREFIX = env.str("FK_MEDIA_URLPREFIX", default="https://frikanalen.no/media/")
# TODO: Is this in use at all?
FK_MEDIA_ROOT = "/tank/new_media/media"

# Handed to the frontend as a video's uploadUrl. tusd is served under /upload
# on the main site, so this differs per environment.
FK_UPLOAD_URL = env.str("FK_UPLOAD_URL")

AUTH_USER_MODEL = "fk.User"

# Channel ID per RFC 2838 (Uniform Resource Identifier for Television Broadcasts)
CHANNEL_ID = "frikanalen.tv"

# Human readable channel name, higher priority first
CHANNEL_DISPLAY_NAMES = ["Frikanalen"]

# URL to the website for references in feeds (No trailing slash)
SITE_URL = "https://frikanalen.no"

########## TV-ANYTIME / NorDig EPG METADATA
# The NorDig EPG/Event metadata exchange format (NorDig TVA Implementation
# Guidelines 1.4) is TV-Anytime, ETSI TS 102 822-3-1. What follows is the
# per-broadcaster part of it: everything a consumer needs to attribute the
# feed to us and to tell our two services apart.

# The CRID authority. Every crid:// we mint is `crid://<authority>/<data>`,
# and it is the domain -- not the data -- that makes the identifier globally
# unique, so this must stay a domain we actually control (Guidelines 2.3.1).
# Deliberately not CHANNEL_ID: "frikanalen.tv" is an RFC 2838 broadcast URI,
# not a registered domain, and minting CRIDs under it would claim a namespace
# that is not ours. Changing this orphans every CRID already published.
TVA_AUTHORITY = "frikanalen.no"

# Attribution, carried in MetadataOriginationInformationTable.
TVA_PUBLISHER = "Foreningen Frikanalen"
TVA_RIGHTS_OWNER = "Foreningen Frikanalen"
TVA_COPYRIGHT_NOTICE = (
    "Sendeskjemaet kan gjengis fritt med kildehenvisning til Frikanalen. "
    "Rettighetene til programmene tilhører den enkelte medlemsorganisasjonen."
)

# Our two TVA services. The ids are `<authority>/<service>`, which is the
# convention the NorDig example files use (e.g. "nrk.no/NRK1"), and they are
# what ScheduleEvent/OnDemandProgram reference by serviceIDRef.
TVA_LINEAR_SERVICE_ID = f"{TVA_AUTHORITY}/frikanalen"
TVA_ONDEMAND_SERVICE_ID = f"{TVA_AUTHORITY}/vod"

# ServiceURL entries, keyed by the `name` attribute that says which carrier
# each one describes. A DVB triplet for our DTT carriage belongs here as
# "DTT": dvb://<original_network_id>.<transport_stream_id>.<service_id>, but
# we do not have ours on file, and a guessed triplet would point receivers at
# somebody else's service -- so the key is simply absent until someone can
# supply it.
TVA_LINEAR_SERVICE_URLS = {"WWW": f"{SITE_URL}/"}
TVA_ONDEMAND_SERVICE_URLS = {"WWW": f"{SITE_URL}/"}

# Spoken language assumed for a video whose own field is blank, and the
# document language of the feed itself.
TVA_DEFAULT_LANGUAGE = "no"

########## PATH CONFIGURATION
# Absolute filesystem path to the Django project directory:
DJANGO_ROOT = dirname(dirname(abspath(__file__)))

# Absolute filesystem path to the top-level project folder:
SITE_ROOT = dirname(DJANGO_ROOT)

# Absolute filesystem path to the top-level project folder:
PROJECT_ROOT = dirname(SITE_ROOT)

# Site name:
SITE_NAME = basename(DJANGO_ROOT)

# Add our project to our pythonpath, this way we don't need to type our project
# name in our dotted import paths:
sys.path.append(DJANGO_ROOT)
########## END PATH CONFIGURATION


########## DEBUG CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = False

########## MANAGER CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#admins
ADMINS = (
    ("Tore Sinding Bekkedal", "toresbe@gmail.com"),
    ("Odin Hørthe Omdal", "odin.omdal@gmail.com"),
)

# See: https://docs.djangoproject.com/en/dev/ref/settings/#managers
MANAGERS = ADMINS + (
    ("Frikanalen styret", "styret@frikanalen.no"),
    ("Olav Hardang", "olav.hardang@p7.no"),
)
########## END MANAGER CONFIGURATION


DEFAULT_AUTO_FIELD = "django.db.models.AutoField"


########## GENERAL CONFIGURATION
TIME_ZONE = "Europe/Oslo"
LANGUAGE_CODE = "en-us"
SITE_ID = 1
USE_I18N = True
USE_L10N = True
USE_TZ = True

TEST_RUNNER = "django.test.runner.DiscoverRunner"
########## END GENERAL CONFIGURATION


########## MEDIA CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#media-root
MEDIA_ROOT = normpath(join(PROJECT_ROOT, "media"))

# See: https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = "/media/"
########## END MEDIA CONFIGURATION


########## STATIC FILE CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#static-root
STATIC_ROOT = normpath(join(PROJECT_ROOT, "collected_staticfiles"))

# See: https://docs.djangoproject.com/en/dev/ref/settings/#static-url
STATIC_URL = "/static/"

# See: https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#staticfiles-finders
STATICFILES_FINDERS = (
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
)
########## END STATIC FILE CONFIGURATION


########## FIXTURE CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#std:setting-FIXTURE_DIRS
FIXTURE_DIRS = (normpath(join(SITE_ROOT, "fixtures")),)
########## END FIXTURE CONFIGURATION


# TEMPLATE CONFIGURATION
# ------------------------------------------------------------------------------
# See: https://docs.djangoproject.com/en/dev/ref/settings/#templates
TEMPLATES = [
    {
        # See: https://docs.djangoproject.com/en/dev/ref/settings/#std:setting-TEMPLATES-BACKEND
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # See: https://docs.djangoproject.com/en/dev/ref/settings/#template-dirs
        "DIRS": [
            normpath(join(SITE_ROOT, "templates")),
        ],
        "OPTIONS": {
            # See: https://docs.djangoproject.com/en/dev/ref/settings/#template-debug
            "debug": DEBUG,
            # See: https://docs.djangoproject.com/en/dev/ref/settings/#template-loaders
            # https://docs.djangoproject.com/en/dev/ref/templates/api/#loader-types
            "loaders": [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
                # deprecated in Django 2
                # 'django.template.loaders.eggs.Loader',
            ],
            # See: https://docs.djangoproject.com/en/dev/ref/settings/#template-context-processors
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.i18n",
                "django.template.context_processors.media",
                "django.template.context_processors.static",
                "django.template.context_processors.tz",
                "django.contrib.messages.context_processors.messages",
            ],
            "builtins": [
                "django.templatetags.i18n",
            ],
        },
    },
]
########## END TEMPLATE CONFIGURATION


########## MIDDLEWARE CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#middleware-classes
MIDDLEWARE = (
    "django.middleware.cache.UpdateCacheMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    # Default Django middleware.
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # "djangorestframework_camel_case.middleware.CamelCaseMiddleWare",
    "fkweb.middleware.AnonymousOnlyFetchFromCacheMiddleware",
    # Must stay *inside* both cache middlewares. Django's cache key includes
    # the active timezone, so with this in between them the pair wrote keys
    # under TIME_ZONE and read them back under UTC, and no /api/ response was
    # ever served from the cache.
    "fkweb.middleware.api_utc_middleware",
)
########## END MIDDLEWARE CONFIGURATION


########## URL CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#root-urlconf
ROOT_URLCONF = f"{SITE_NAME}.urls"
########## END URL CONFIGURATION


########## APP CONFIGURATION
DJANGO_APPS = (
    # Default Django apps:
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    #'django.contrib.sites',
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Useful template tags:
    # 'django.contrib.humanize',
    # Admin panel and documentation:
    "django.contrib.admin",
    # 'django.contrib.admindocs',
    # improved OpenAPI schema generation
    "drf_spectacular",
)

THIRD_PARTY_APPS = (
    "corsheaders",
    "django_filters",
    "rest_framework",
    "rest_framework.authtoken",
    "phonenumber_field",
)

# Apps specific for this project go here.
LOCAL_APPS = (
    "fkweb",
    "agenda",
    "fk",
    "api",
    "news",
)

# See: https://docs.djangoproject.com/en/dev/ref/settings/#installed-apps
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS
########## END APP CONFIGURATION


########## LOGGING CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#logging
# A sample logging configuration. The only tangible logging
# performed by this configuration is to send an email to
# the site admins on every HTTP 500 error when DEBUG=False.
# See http://docs.djangoproject.com/en/dev/topics/logging for
# more details on how to customize your logging configuration.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"require_debug_false": {"()": "django.utils.log.RequireDebugFalse"}},
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
        },
        "mail_admins": {
            "level": "ERROR",
            "filters": ["require_debug_false"],
            "class": "django.utils.log.AdminEmailHandler",
        },
    },
    "loggers": {
        "root": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": True,
        },
        "django": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": True,
        },
        "django.request": {
            "handlers": ["mail_admins"],
            "level": "ERROR",
            "propagate": True,
        },
        "gunicorn": {
            "handlers": ["mail_admins"],
            "level": "ERROR",
            "propagate": True,
        },
    },
}
########## END LOGGING CONFIGURATION


########## WSGI CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#wsgi-application
WSGI_APPLICATION = f"{SITE_NAME}.wsgi.application"
########## END WSGI CONFIGURATION

########## REST FRAMEWORK CONFIGURATION
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": (
        "djangorestframework_camel_case.render.CamelCaseJSONRenderer",
        "djangorestframework_camel_case.render.CamelCaseBrowsableAPIRenderer",
    ),
    "DEFAULT_PARSER_CLASSES": ("djangorestframework_camel_case.parser.CamelCaseJSONParser",),
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticatedOrReadOnly",),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_standardized_errors.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "drf_standardized_errors.handler.exception_handler",
}

########## OpenAPI CONFIGURATION
SPECTACULAR_SETTINGS = {
    "TITLE": "Frikanalen Django API",
    "DESCRIPTION": "API for Frikanalen",
    "VERSION": "1.1.0",
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.contrib.djangorestframework_camel_case.camelize_serializer_fields",
        "drf_standardized_errors.openapi_hooks.postprocess_schema_enums",
    ],
    "APPEND_COMPONENTS": {
        "parameters": {
            "X-CSRFToken": {
                "name": "X-CSRFToken",
                "in": "header",
                "required": False,  # true for session-authenticated writes
                "schema": {"type": "string"},
                "description": "CSRF token echoed from csrftoken cookie when using SessionAuthentication.",
            }
        }
    },
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,
        "withCredentials": True,
    },
    "COMPONENT_SPLIT_REQUEST": True,
    "CAMELIZE": False,
    "ENUM_GENERATION": True,
    # from drf-standardized-errors doc, quote:
    # add the following to drf_spectacular setting ENUM_NAME_OVERRIDES.
    # This will avoid multiple warnings raised by drf-spectacular due to
    # the same set of error codes appearing in multiple operations.
    "ENUM_NAME_OVERRIDES": {
        # Named after what it describes rather than after the field it
        # happens to sit on: the generated name would be `StateEnum`,
        # which the next model with a `state` field would collide with.
        "IngestStateEnum": "fk.models.ingest.IngestState.choices",
        # Same reasoning, and the two serializers that expose a variant
        # must share one component rather than generate one apiece.
        "VideoFileVariantEnum": "fk.models.video_file.VideoFileVariant.choices",
        "ValidationErrorEnum": "drf_standardized_errors.openapi_serializers.ValidationErrorEnum.choices",
        "ClientErrorEnum": "drf_standardized_errors.openapi_serializers.ClientErrorEnum.choices",
        "ServerErrorEnum": "drf_standardized_errors.openapi_serializers.ServerErrorEnum.choices",
        "ErrorCode401Enum": "drf_standardized_errors.openapi_serializers.ErrorCode401Enum.choices",
        "ErrorCode403Enum": "drf_standardized_errors.openapi_serializers.ErrorCode403Enum.choices",
        "ErrorCode404Enum": "drf_standardized_errors.openapi_serializers.ErrorCode404Enum.choices",
        "ErrorCode405Enum": "drf_standardized_errors.openapi_serializers.ErrorCode405Enum.choices",
        "ErrorCode406Enum": "drf_standardized_errors.openapi_serializers.ErrorCode406Enum.choices",
        "ErrorCode415Enum": "drf_standardized_errors.openapi_serializers.ErrorCode415Enum.choices",
        "ErrorCode429Enum": "drf_standardized_errors.openapi_serializers.ErrorCode429Enum.choices",
        "ErrorCode500Enum": "drf_standardized_errors.openapi_serializers.ErrorCode500Enum.choices",
    },
}

# Everything with the API should be okay, since we don't share
# the login cookie it's all safe.
CORS_ALLOW_ALL_ORIGINS = True
CORS_URLS_REGEX = r"^/api/.*$"  # anyway, only enable CORS for the API

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "Frikanalen <noreply@frikanalen.no>"
SECRET_KEY = env.str("SECRET_KEY")
ALLOWED_HOSTS = env.str("ALLOWED_HOSTS").split(",")
DATABASES = {"default": env.db()}
CSRF_TRUSTED_ORIGINS = env.str("CSRF_TRUSTED_ORIGINS").split(",")
try:
    cache_from_env_or_memory = env.cache()
except ImproperlyConfigured:
    logger.warning("CACHE_URL not set, falling back to in-memory cache.")
    cache_from_env_or_memory = {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}

CACHES = {"default": cache_from_env_or_memory}

# How stale an anonymous page may get. This is Django's own default, but it
# only started to mean anything once the page cache actually began serving
# responses, so state it here where the cost is visible: a video published
# now can take this long to appear for logged-out visitors. Authenticated
# callers bypass the cache entirely and always see current data.
CACHE_MIDDLEWARE_SECONDS = 600
