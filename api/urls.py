# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
from django.urls import URLPattern, URLResolver, include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework import parsers
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.routers import SimpleRouter

import agenda.tvanytime.views as tvanytime_views
import api.auth.views as auth_views
import api.grant_request.views as grant_request_views
import api.grants.views as grant_views
import api.live.views as live_views
import api.mediamtx as mediamtx_views
import api.organization.views as organization_views
import api.playout.views as playout_views
import api.program_image.views as program_image_views
import api.recurring_slot.views as recurring_slot_views
import api.schedule.views as schedule_views
import api.series.views as series_views
import api.video.views as video_views
import api.videofile.views as videofile_views
from api.auth import password_reset
from fkweb.views import CsrfView

from . import views

# Registered under the "api/" prefix that path("api/", include(...)) supplies
# at the bottom of this file, so none of these repeat it.
router = SimpleRouter(trailing_slash=False)
router.register(r"asrun", views.AsRunViewSet, "asrun")
router.register(r"categories", views.CategoryViewSet)
router.register(r"scheduleitems", schedule_views.ScheduleitemViewSet, "api-scheduleitem")
router.register(r"grants", grant_views.AirtimeGrantViewSet, "api-grant")
router.register(r"videofiles", videofile_views.VideoFileViewSet, "api-videofile")


class ObtainAuthTokenJsonOnly(ObtainAuthToken):
    """If we don't restrict this to JSON-only, the Python generated
    code tries to both send form-multipart and JSON."""

    parser_classes = (parsers.JSONParser,)


# Annotated because the opening literal is all patterns, which would fix
# the inferred type as list[URLPattern]; the router and include() both
# contribute resolvers further down.
api_patterns: list[URLPattern | URLResolver] = [
    path("mediamtx/auth", mediamtx_views.authenticate, name="api-mediamtx-auth"),
    path("mediamtx/pull/<uuid:stream_id>", mediamtx_views.pull_url, name="api-mediamtx-pull"),
    path("playout/events", playout_views.events, name="api-playout-events"),
    path("csrf", CsrfView.as_view(), name="api-csrf-detail"),
    # Auth
    path("user/register", auth_views.UserCreate.as_view(), name="api-user-create"),
    path("user/login", auth_views.UserLogin.as_view(), name="api-user-login"),
    path("user/logout", auth_views.UserLogout.as_view(), name="api-user-logout"),
    path(
        "user/password-reset",
        password_reset.PasswordResetRequest.as_view(),
        name="api-user-password-reset",
    ),
    path(
        "user/password-reset/confirm",
        password_reset.PasswordResetConfirm.as_view(),
        name="api-user-password-reset-confirm",
    ),
    path("user", auth_views.UserDetail.as_view(), name="api-user-detail"),
    path("obtain-token", ObtainAuthTokenJsonOnly.as_view(), name="api-token-auth"),
    # Video
    path("videos", video_views.VideoList.as_view(), name="api-video-list"),
    path(
        "videos/<int:video_id>/images",
        program_image_views.ProgramImageViewSet.as_view({"get": "list", "post": "create"}),
        name="api-program-image-list",
    ),
    path(
        "videos/<int:video_id>/images/<int:pk>",
        program_image_views.ProgramImageViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="api-program-image-detail",
    ),
    path(
        "videos/<int:pk>/upload_token",
        video_views.VideoUploadTokenDetail.as_view(),
        name="api-video-upload-token-detail",
    ),
    path(
        "videos/<int:pk>/upload_token/verify",
        video_views.VideoUploadTokenVerification.as_view(),
        name="api-video-upload-token-verification",
    ),
    path(
        "videos/<int:pk>/ingest",
        video_views.VideoIngestJobDetail.as_view(),
        name="api-video-ingest-job-detail",
    ),
    path("videos/<int:pk>", video_views.VideoDetail.as_view(), name="api-video-detail"),
    # Ingest. Not under videos/ like the reporting endpoint above: a claim
    # asks for whichever video needs work next, so there is no id to hang
    # it off.
    path("ingest/claim", video_views.IngestClaim.as_view(), name="api-ingest-claim"),
    # Series
    path("series", series_views.SeriesList.as_view(), name="api-series-list"),
    path("series/<int:pk>", series_views.SeriesDetail.as_view(), name="api-series-detail"),
    # Organization
    path(
        "organization",
        organization_views.OrganizationList.as_view(),
        name="api-organization-list",
    ),
    path(
        "organization/<int:pk>",
        organization_views.OrganizationDetail.as_view(),
        name="api-organization-detail",
    ),
    # Live contribution: an organization sets up and checks its own feed.
    path(
        "organization/<int:pk>/live-sources",
        live_views.OrganizationLiveSourceList.as_view(),
        name="api-organization-live-source-list",
    ),
    path(
        "live-sources/<int:pk>",
        live_views.LiveSourceDetail.as_view(),
        name="api-live-source-detail",
    ),
    path(
        "live-sources/<int:pk>/encoder-keys",
        live_views.LiveSourceEncoderKeys.as_view(),
        name="api-live-source-encoder-keys",
    ),
    path(
        "live-sources/<int:pk>/encoder-keys/<int:key_pk>/replace",
        live_views.EncoderKeyReplace.as_view(),
        name="api-live-source-encoder-key-replace",
    ),
    path(
        "live-sources/<int:pk>/encoder-keys/<int:key_pk>",
        live_views.EncoderKeyRevoke.as_view(),
        name="api-live-source-encoder-key-revoke",
    ),
    path(
        "live-sources/<int:pk>/signal/history",
        live_views.LiveSourceSignalHistory.as_view(),
        name="api-live-source-signal-history",
    ),
    path(
        "live-sources/<int:pk>/signal",
        live_views.LiveSourceSignal.as_view(),
        name="api-live-source-signal",
    ),
    path(
        "live-sources/<int:pk>/preview-token",
        live_views.LiveSourcePreviewToken.as_view(),
        name="api-live-source-preview-token",
    ),
    # Recurring airtime. Each occurrence becomes an airtime grant; changes
    # to a slot go through an audited request.
    path(
        "recurring-slots",
        recurring_slot_views.RecurringSlotList.as_view(),
        name="api-recurring-slot-list",
    ),
    path(
        "recurring-slots/<int:pk>",
        recurring_slot_views.RecurringSlotDetail.as_view(),
        name="api-recurring-slot-detail",
    ),
    path(
        "recurring-slot-requests/creation/<int:pk>/decision",
        grant_request_views.RecurringSlotCreationRequestDecision.as_view(),
        name="api-recurring-slot-creation-request-decision",
    ),
    path(
        "recurring-slot-requests/ownership/<int:pk>/decision",
        grant_request_views.RecurringSlotOwnershipRequestDecision.as_view(),
        name="api-recurring-slot-ownership-request-decision",
    ),
    path(
        "grant-requests",
        grant_request_views.GrantRequestList.as_view(),
        name="api-grant-request-list",
    ),
    path(
        "grant-requests/airtime",
        grant_request_views.AirtimeGrantRequestCreate.as_view(),
        name="api-airtime-request-create",
    ),
    path(
        "grant-requests/airtime/<int:pk>/decision",
        grant_request_views.AirtimeGrantRequestDecision.as_view(),
        name="api-airtime-request-decision",
    ),
    path(
        "grant-requests/exemption",
        grant_request_views.GrantExemptionRequestCreate.as_view(),
        name="api-exemption-request-create",
    ),
    path(
        "grant-requests/exemption/<int:pk>/decision",
        grant_request_views.GrantExemptionRequestDecision.as_view(),
        name="api-exemption-request-decision",
    ),
    path(
        "fill-rules",
        recurring_slot_views.FillRuleList.as_view(),
        name="api-fill-rule-list",
    ),
    path(
        "recurring-slot-requests",
        recurring_slot_views.RecurringSlotRequestList.as_view(),
        name="api-recurring-slot-request-list",
    ),
    path(
        "recurring-slot-requests/creation",
        recurring_slot_views.RecurringSlotCreationRequestCreate.as_view(),
        name="api-recurring-slot-creation-request-create",
    ),
    path(
        "recurring-slot-requests/ownership",
        recurring_slot_views.RecurringSlotOwnershipRequestCreate.as_view(),
        name="api-recurring-slot-ownership-request-create",
    ),
]
api_patterns += router.urls

api_patterns += [
    # The root of the tvanytime path is the human-readable index rather
    # than a feed, which is the shape /xmltv/ already has and the one a
    # distributor arriving at the bare URL expects. The feeds hang below
    # it, so neither has to be guessed at.
    path(
        "tvanytime",
        tvanytime_views.tvanytime_home,
        name="api-tvanytime-home",
    ),
    path(
        "tvanytime/upcoming",
        tvanytime_views.TVAnytimeUpcomingView.as_view(),
        name="api-tvanytime-upcoming",
    ),
    re_path(
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
    path("api-auth/", include("rest_framework.urls", namespace="rest_framework")),
]

urlpatterns: list[URLPattern | URLResolver] = [
    # Bare "/api", with no trailing slash, lives outside the include() below
    # since path("api/", ...) can only ever match paths that have the slash.
    path("api", views.api_root, name="api-root"),
    path("api/", include(api_patterns)),
]
