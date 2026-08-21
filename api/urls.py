# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
from django.urls import URLPattern, URLResolver, include, path
from django.urls import re_path as url
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework import parsers
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.routers import SimpleRouter
from rest_framework.urlpatterns import format_suffix_patterns

import agenda.tvanytime.views as tvanytime_views
import api.auth.views as auth_views
import api.organization.views as organization_views
import api.schedule.views as schedule_views
import api.series.views as series_views
import api.video.views as video_views
import api.videofile.views as videofile_views
from fkweb.views import CsrfView

from . import views

PK = r"(?P<pk>\d+)"

# Registered under the "api/" prefix that path("api/", include(...)) supplies
# at the bottom of this file, so none of these repeat it.
router = SimpleRouter(trailing_slash=False)
router.register(r"asrun", views.AsRunViewSet, "asrun")
router.register(r"categories", views.CategoryViewSet)
router.register(r"scheduleitems", schedule_views.ScheduleitemViewSet, "api-scheduleitem")
router.register(r"videofiles", videofile_views.VideoFileViewSet, "api-videofile")


class ObtainAuthTokenJsonOnly(ObtainAuthToken):
    """If we don't restrict this to JSON-only, the Python generated
    code tries to both send form-multipart and JSON."""

    parser_classes = (parsers.JSONParser,)


# Annotated because the opening literal is all patterns, which would fix
# the inferred type as list[URLPattern]; the router, the format-suffix
# expansion and include() all contribute resolvers further down.
api_patterns: list[URLPattern | URLResolver] = [
    url(r"^csrf$", CsrfView.as_view(), name="api-csrf-detail"),
    # Auth
    url(r"^user/register$", auth_views.UserCreate.as_view(), name="api-user-create"),
    url(r"^user/login$", auth_views.UserLogin.as_view(), name="api-user-login"),
    url(r"^user/logout$", auth_views.UserLogout.as_view(), name="api-user-logout"),
    url(r"^user$", auth_views.UserDetail.as_view(), name="api-user-detail"),
    url(r"^obtain-token$", ObtainAuthTokenJsonOnly.as_view(), name="api-token-auth"),
    # Video
    url(r"^videos$", video_views.VideoList.as_view(), name="api-video-list"),
    url(
        rf"^videos/{PK}/upload_token$",
        video_views.VideoUploadTokenDetail.as_view(),
        name="api-video-upload-token-detail",
    ),
    url(
        rf"^videos/{PK}/upload_token/verify$",
        video_views.VideoUploadTokenVerification.as_view(),
        name="api-video-upload-token-verification",
    ),
    url(
        rf"^videos/{PK}/ingest$",
        video_views.VideoIngestJobDetail.as_view(),
        name="api-video-ingest-job-detail",
    ),
    url(rf"^videos/{PK}$", video_views.VideoDetail.as_view(), name="api-video-detail"),
    # Series
    url(r"^series$", series_views.SeriesList.as_view(), name="api-series-list"),
    url(rf"^series/{PK}$", series_views.SeriesDetail.as_view(), name="api-series-detail"),
    # Organization
    url(
        r"^organization$",
        organization_views.OrganizationList.as_view(),
        name="api-organization-list",
    ),
    url(
        rf"^organization/{PK}$",
        organization_views.OrganizationDetail.as_view(),
        name="api-organization-detail",
    ),
]
api_patterns += router.urls

# Format suffixes
api_patterns = format_suffix_patterns(api_patterns, allowed=["json", "api", "xml"])

api_patterns += [
    # Registered after the format-suffix expansion on purpose: a
    # `.json`-style twin would only clutter the OpenAPI schema with a
    # colliding {format} operation.
    url(
        r"^scheduling/policy$",
        schedule_views.SchedulingPolicyView.as_view(),
        name="api-scheduling-policy",
    ),
    # Same reasoning, and more so: these only ever render XML, so a
    # `.json` suffix would advertise a representation that cannot exist.
    #
    # The root of the tvanytime path is the human-readable index rather
    # than a feed, which is the shape /xmltv/ already has and the one a
    # distributor arriving at the bare URL expects. The feeds hang below
    # it, so neither has to be guessed at.
    url(
        r"^tvanytime$",
        tvanytime_views.tvanytime_home,
        name="api-tvanytime-home",
    ),
    url(
        r"^tvanytime/upcoming$",
        tvanytime_views.TVAnytimeUpcomingView.as_view(),
        name="api-tvanytime-upcoming",
    ),
    url(
        r"^tvanytime/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})$",
        tvanytime_views.TVAnytimeDateView.as_view(),
        name="api-tvanytime-date",
    ),
]

api_patterns += [
    # drf-spectacular schema and docs UIs, plus DRF's own browsable-API
    # login/logout views - not part of the API surface itself.
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "schema/swagger-ui/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"
    ),
    path("schema/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    url(r"^api-auth/", include("rest_framework.urls", namespace="rest_framework")),
]

urlpatterns: list[URLPattern | URLResolver] = [
    # Bare "/api", with no trailing slash, lives outside the include() below
    # since path("api/", ...) can only ever match paths that have the slash.
    url(r"^api$", views.api_root, name="api-root"),
    path("api/", include(api_patterns)),
]
