# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
from django.urls import URLPattern, URLResolver, include, path
from django.urls import re_path as url
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework import parsers
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.routers import SimpleRouter
from rest_framework.urlpatterns import format_suffix_patterns

import api.auth.views
import api.organization.views
import api.schedule.views as schedule_views
import api.video.views
import api.videofile.views as videofile_views
from fkweb.views import CsrfView

from . import views

router = SimpleRouter(trailing_slash=False)
router.register(r"api/asrun", views.AsRunViewSet, "asrun")
router.register(r"api/categories", views.CategoryViewSet)
router.register(r"api/scheduleitems", schedule_views.ScheduleitemViewSet, "api-scheduleitem")

# I am manually generating these to in order to have the transition to a ViewSet be
# as compatible as at all possible with legacy frontend etc. until we can phase it out.
videofile_list = videofile_views.VideoFileViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
videofile_detail = videofile_views.VideoFileViewSet.as_view(
    {
        "get": "retrieve",
        "put": "update",
        "patch": "partial_update",
        "delete": "destroy",
    }
)


class ObtainAuthTokenJsonOnly(ObtainAuthToken):
    """If we don't restrict this to JSON-only, the Python generated
    code tries to both send form-multipart and JSON."""

    parser_classes = (parsers.JSONParser,)


# Annotated because the opening literal is all patterns, which would fix
# the inferred type as list[URLPattern]; the router, the format-suffix
# expansion and include() all contribute resolvers further down.
urlpatterns: list[URLPattern | URLResolver] = [
    url(r"^api$", views.api_root, name="api-root"),
    url(r"^api/csrf$", CsrfView.as_view(), name="api-csrf-detail"),
    url(r"^api/user/register$", api.auth.views.UserCreate.as_view(), name="api-user-create"),
    url(r"^api/user/login$", api.auth.views.UserLogin.as_view(), name="api-user-login"),
    url(r"^api/user/logout$", api.auth.views.UserLogout.as_view(), name="api-user-logout"),
    url(r"^api/user$", api.auth.views.UserDetail.as_view(), name="api-user-detail"),
    url(r"^api/obtain-token$", ObtainAuthTokenJsonOnly.as_view(), name="api-token-auth"),
    url(r"^api/videos$", api.video.views.VideoList.as_view(), name="api-video-list"),
    url(
        r"^api/videos/(?P<pk>\d+)/upload_token$",
        api.video.views.VideoUploadTokenDetail.as_view(),
        name="api-video-upload-token-detail",
    ),
    url(
        r"^api/videos/(?P<pk>\d+)/upload_token/verify$",
        api.video.views.VideoUploadTokenVerification.as_view(),
        name="api-video-upload-token-verification",
    ),
    url(
        r"^api/videos/(?P<pk>\d+)/ingest$",
        api.video.views.VideoIngestJobDetail.as_view(),
        name="api-video-ingest-job-detail",
    ),
    url(
        r"^api/videos/(?P<pk>\d+)$", api.video.views.VideoDetail.as_view(), name="api-video-detail"
    ),
    url(r"^api/videofiles$", videofile_list, name="api-videofile-list"),
    url(r"^api/videofiles/(?P<pk>\d+)$", videofile_detail, name="api-videofile-detail"),
    url(
        r"^api/organization$",
        api.organization.views.OrganizationList.as_view(),
        name="api-organization-list",
    ),
    url(
        r"^api/organization/(?P<pk>\d+)$",
        api.organization.views.OrganizationDetail.as_view(),
        name="api-organization-detail",
    ),
]
urlpatterns += router.urls

# Format suffixes
urlpatterns = format_suffix_patterns(urlpatterns, allowed=["json", "api", "xml"])

urlpatterns += [
    # Registered after the format-suffix expansion on purpose: a
    # `.json`-style twin would only clutter the OpenAPI schema with a
    # colliding {format} operation.
    path(
        "api/scheduling/policy",
        schedule_views.SchedulingPolicyView.as_view(),
        name="api-scheduling-policy",
    ),
]

# Default login/logout views
urlpatterns += [
    # Spectacular API views
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/schema/swagger-ui/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/schema/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
    url(r"^api/api-auth/", include("rest_framework.urls", namespace="rest_framework")),
]
