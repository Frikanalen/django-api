from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, permissions
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from api.auth.permissions import can_administer_organization
from api.live.serializers import LiveSourceSerializer, PreviewTokenSerializer
from fk import preview_tokens
from fk.models import LiveSource, Organization, Scheduleitem


class OrganizationLiveSourceList(generics.ListAPIView):
    """An organization's live contribution inputs and their coming bookings.

    Only its own members see them: a source's stream ID is half of what an
    encoder needs to publish to it.
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
        # A source's coming bookings: live airings of the videos it feeds.
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
        return sources


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
            }
        )
        return Response(serializer.data, status=201)
