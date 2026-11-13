from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, permissions
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from api.auth.permissions import can_administer_organization
from api.live.serializers import (
    EncoderKeySerializer,
    IssuedEncoderKeySerializer,
    LiveSourceSerializer,
    PreviewTokenSerializer,
    SignalSerializer,
)
from fk import preview_tokens, receiver
from fk.models import LiveSource, Organization, Scheduleitem, StreamCredential
from fk.stream_connections import blackmagic_web_presenter_config, connection_examples
from scheduling.services import may_act_for

# Setting up live transmission is held to the same rule as programming
# airtime: a live source is only good for airing, and what goes out live
# can't be looked at first.
MAY_SET_UP = (
    "Only members of an approved member organization, with a confirmed "
    "identity, can set up live transmission."
)


def _source_for(user, pk) -> LiveSource:
    """A source the user may see; anyone else gets the 404 a missing one would."""
    source = get_object_or_404(LiveSource.objects.select_related("organization"), pk=pk)
    if not can_administer_organization(user, source.organization):
        raise NotFound()
    return source


def _attach_upcoming(sources) -> None:
    """Give each source its coming bookings: live airings of the videos it feeds."""
    upcoming = (
        Scheduleitem.objects.filter(
            is_live=True,
            video__live_transmission__source__in=sources,
            airtime__endswith__gt=timezone.now(),
        )
        .select_related("video__live_transmission")
        .order_by("starttime")
    )
    by_source: dict[int, list[Scheduleitem]] = {}
    for item in upcoming:
        by_source.setdefault(item.video.live_transmission.source_id, []).append(item)
    for source in sources:
        source.upcoming = by_source.get(source.pk, [])


def _require_may_set_up(user, organization) -> None:
    if not may_act_for(user, organization):
        raise PermissionDenied(MAY_SET_UP)


class OrganizationLiveSourceList(generics.ListCreateAPIView):
    """An organization's live contribution inputs and their coming bookings.

    Only its own members see them: a source's stream ID is half of what an
    encoder needs to publish to it. Creating one is for those who may
    program the organization's airtime.
    """

    serializer_class = LiveSourceSerializer
    permission_classes = (permissions.IsAuthenticated,)
    pagination_class = None
    filter_backends = ()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return LiveSource.objects.none()
        organization = get_object_or_404(Organization, pk=self.kwargs["pk"])
        if not can_administer_organization(self.request.user, organization):
            raise PermissionDenied("You must belong to this organization.")
        sources = list(LiveSource.objects.filter(organization=organization).order_by("name", "pk"))
        _attach_upcoming(sources)
        return sources

    def perform_create(self, serializer):
        organization = get_object_or_404(Organization, pk=self.kwargs["pk"])
        if not can_administer_organization(self.request.user, organization):
            raise PermissionDenied("You must belong to this organization.")
        _require_may_set_up(self.request.user, organization)
        serializer.save(organization=organization)


class LiveSourceDetail(generics.RetrieveUpdateAPIView):
    """Rename a source, switch it off, or change where it is pulled from.

    A disabled source refuses every encoder and reader, and can't be given
    to a video. Setting a pull address revokes the source's encoder keys.
    """

    serializer_class = LiveSourceSerializer
    permission_classes = (permissions.IsAuthenticated,)
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        source = _source_for(self.request.user, self.kwargs["pk"])
        _attach_upcoming([source])
        return source

    def perform_update(self, serializer):
        _require_may_set_up(self.request.user, serializer.instance.organization)
        serializer.save()


@method_decorator(never_cache, name="dispatch")
class LiveSourceEncoderKeys(generics.ListCreateAPIView):
    """A source's encoder keys. Issuing one answers with its password and
    the encoder settings built from it, once; the list never shows them.

    Only publish keys: readers, preview workers and playout stay with staff.
    """

    permission_classes = (permissions.IsAuthenticated,)
    pagination_class = None
    filter_backends = ()

    def get_serializer_class(self):
        return IssuedEncoderKeySerializer if self.request.method == "POST" else EncoderKeySerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return StreamCredential.objects.none()
        source = _source_for(self.request.user, self.kwargs["pk"])
        return source.credentials.filter(
            action=StreamCredential.Action.PUBLISH, revoked_at__isnull=True
        ).order_by("-created_at")

    @extend_schema(request=EncoderKeySerializer, responses={201: IssuedEncoderKeySerializer})
    def post(self, request, pk):
        source = _source_for(request.user, pk)
        _require_may_set_up(request.user, source.organization)
        if not source.enabled:
            raise PermissionDenied("The live source is disabled.")
        if source.is_pulled:
            raise PermissionDenied("Frikanalen pulls this source; it takes no encoder keys.")
        serializer = EncoderKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        credential = StreamCredential(
            source=source,
            label=serializer.validated_data["label"],
            action=StreamCredential.Action.PUBLISH,
        )
        password = credential.generate_secret()
        with transaction.atomic():
            credential.save()
        credential.password = password  # type: ignore[attr-defined]
        credential.connections = connection_examples(credential, password)  # type: ignore[attr-defined]
        credential.blackmagic_config = blackmagic_web_presenter_config(credential, password)  # type: ignore[attr-defined]
        response = Response(IssuedEncoderKeySerializer(credential).data, status=201)
        response["Referrer-Policy"] = "no-referrer"
        return response


class EncoderKeyRevoke(generics.GenericAPIView):
    """Revoke an encoder key: an encoder using it is refused from then on."""

    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = EncoderKeySerializer

    @extend_schema(request=None, responses={204: None})
    def delete(self, request, pk, key_pk):
        source = _source_for(request.user, pk)
        _require_may_set_up(request.user, source.organization)
        credential = get_object_or_404(
            source.credentials, pk=key_pk, action=StreamCredential.Action.PUBLISH
        )
        if credential.revoked_at is None:
            credential.revoked_at = timezone.now()
            credential.save(update_fields=["revoked_at"])
        return Response(status=204)


class LiveSourcePreviewToken(generics.GenericAPIView):
    serializer_class = PreviewTokenSerializer
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(
        operation_id="live_sources_preview_token_create",
        summary="Issue a WebRTC preview token",
        description=(
            "A short-lived bearer token that lets the caller's browser read this "
            "source's feed over WHEP, at `/<path>/whep` on the site's own host."
        ),
        request=None,
        responses={
            201: PreviewTokenSerializer,
            404: OpenApiResponse(description="No such enabled source in your organizations."),
        },
    )
    def post(self, request, pk):
        source = get_object_or_404(
            LiveSource.objects.select_related("organization"), pk=pk, enabled=True
        )
        # Indistinguishable from a missing source, so stream IDs stay private.
        if not can_administer_organization(request.user, source.organization):
            raise NotFound()
        serializer = self.get_serializer(
            {
                "token": preview_tokens.issue(source, request.user),
                "expires_at": timezone.now() + preview_tokens.TOKEN_TTL,
                "contribution_path": source.contribution_path,
                "preview_path": source.preview_path,
                "program_path": settings.FK_PROGRAM_RETURN_PATH,
            }
        )
        return Response(serializer.data, status=201)


@method_decorator(never_cache, name="dispatch")
class LiveSourceSignal(generics.GenericAPIView):
    serializer_class = SignalSerializer
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(
        operation_id="live_sources_signal_retrieve",
        summary="The signal a live source is receiving",
        description=(
            "What the receiver sees arriving on this source right now, as the "
            "encoder sends it: format, how long it has been in, and transport "
            "health. Reading it does not start a pulled source."
        ),
        responses={
            200: SignalSerializer,
            404: OpenApiResponse(description="No such source in your organizations."),
        },
    )
    def get(self, request, pk):
        source = _source_for(request.user, pk)
        try:
            signal = receiver.signal(source) if source.enabled else {"receiving": False}
        except receiver.ReceiverUnavailable:
            # An answer rather than an error: the page polls this, and where
            # the receiver's API is not set up it simply has nothing to show.
            signal = {"available": False, "receiving": False}
        return Response(
            self.get_serializer({"available": True, **signal, "measured_at": timezone.now()}).data
        )
