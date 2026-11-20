"""
Invitations to become a member of an organization.

The editor (or staff) invites an e-mail address. Whether or not the address
belongs to an account, the editor gets the same answer and the address gets
the same e-mail, so inviting cannot be used to find out who has an account,
or their name. The invitation shows in the organization by its address only,
until someone accepts it.

The e-mailed link carries a token, and only the token can accept: account
e-mail addresses are not verified, so matching on the address would let
whoever registered with it first take the place. A person who is logged in
accepts with the token; a person without an account creates one from the
invitation, with the invited address, and is logged in.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from api.auth.password_reset import run_in_background
from api.auth.permissions import can_administer_organization
from api.auth.serializers import check_password_strength
from api.organization.affidavit import _send
from api.organization.members import OrganizationMemberSerializer, _may_manage, _member
from fk.models import Organization, OrganizationInvitation, User
from fk.models.invitation import hash_token

logger = logging.getLogger(__name__)

NO_SUCH_INVITATION = "Invitasjonen finnes ikke, er brukt eller har gått ut. Be redaktøren om en ny."


class InvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationInvitation
        fields = ("id", "email", "created_at", "expires_at")


class InviteSerializer(serializers.Serializer):
    email = serializers.EmailField()


class InvitationPreviewSerializer(serializers.Serializer):
    organization_name = serializers.CharField()
    invited_by = serializers.CharField()
    email = serializers.EmailField(help_text="The address the invitation was sent to.")
    expires_at = serializers.DateTimeField()


class AcceptInvitationSerializer(serializers.Serializer):
    """Empty when logged in. Without a session: the new account's details."""

    first_name = serializers.CharField(required=False)
    last_name = serializers.CharField(required=False)
    password = serializers.CharField(required=False, write_only=True)


def _name(user: User | None) -> str:
    if user is None:
        return "Frikanalen"
    return f"{user.first_name} {user.last_name}".strip() or user.email


def send_invitation(invitation: OrganizationInvitation, token: str) -> None:
    context = {
        "organization": invitation.organization,
        "invited_by": _name(invitation.invited_by),
        "link": f"{settings.SITE_URL}/invitasjon/{token}",
        "expires_at": invitation.expires_at,
    }
    run_in_background(
        _send,
        "organization/invitation_subject.txt",
        "organization/invitation_email.txt",
        context,
        [invitation.email],
    )


def _manageable(request, pk: int) -> Organization:
    organization = get_object_or_404(Organization.objects.all(), pk=pk)
    if not can_administer_organization(request.user, organization):
        raise PermissionDenied("Du hører ikke til denne organisasjonen.")
    if not _may_manage(request.user, organization):
        raise PermissionDenied("Bare redaktøren kan invitere og fjerne medlemmer.")
    return organization


class OrganizationInvitations(APIView):
    """
    The organization's open invitations, by address. The editor and staff
    see them and invite more.
    """

    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(responses=InvitationSerializer(many=True))
    def get(self, request, pk: int):
        organization = _manageable(request, pk)
        return Response(InvitationSerializer(organization.invitations.open(), many=True).data)

    @extend_schema(request=InviteSerializer, responses={201: InvitationSerializer})
    def post(self, request, pk: int):
        organization = _manageable(request, pk)
        payload = InviteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        email = payload.validated_data["email"].strip()
        # The editor already sees the members' addresses in the list, so
        # saying one of them is in tells nothing new.
        people = organization.members.all() | User.objects.filter(pk=organization.editor_id)
        if people.filter(email__iexact=email).exists():
            raise ValidationError({"email": "Personen er allerede med i organisasjonen."})
        # Inviting the same address again sends a fresh link and replaces
        # the old one.
        invitation = organization.invitations.open().filter(email__iexact=email).first()
        if invitation is None:
            invitation = OrganizationInvitation(organization=organization, email=email)
        invitation.invited_by = request.user
        token = invitation.issue_token()
        invitation.save()
        send_invitation(invitation, token)
        return Response(InvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)


class OrganizationInvitationDetail(APIView):
    """The editor and staff withdraw an invitation."""

    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(responses={204: None})
    def delete(self, request, pk: int, invitation_id: int):
        organization = _manageable(request, pk)
        invitation = organization.invitations.open().filter(pk=invitation_id).first()
        if invitation is None:
            raise NotFound("Invitasjonen finnes ikke lenger.")
        invitation.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _open_invitation(token: str) -> OrganizationInvitation:
    invitation = (
        OrganizationInvitation.objects.open()
        .select_related("organization", "invited_by")
        .filter(token_hash=hash_token(token))
        .first()
    )
    if invitation is None:
        raise NotFound(NO_SUCH_INVITATION)
    return invitation


class InvitationByToken(APIView):
    """
    What the e-mailed link shows: which organization, who invited, and to
    which address. Anyone holding the link can read it.
    """

    permission_classes = (permissions.AllowAny,)
    throttle_classes = (AnonRateThrottle,)

    @extend_schema(
        responses={
            200: InvitationPreviewSerializer,
            404: OpenApiResponse(description="No open invitation."),
        }
    )
    def get(self, request, token: str):
        invitation = _open_invitation(token)
        return Response(
            InvitationPreviewSerializer(
                {
                    "organization_name": invitation.organization.name,
                    "invited_by": _name(invitation.invited_by),
                    "email": invitation.email,
                    "expires_at": invitation.expires_at,
                }
            ).data
        )


class AcceptInvitation(APIView):
    """
    Accept an invitation. Logged in: join as yourself. Not logged in: give a
    name and a password to create an account with the invited address, join,
    and be logged in. If the address already has an account, log in first.
    """

    permission_classes = (permissions.AllowAny,)
    throttle_classes = (AnonRateThrottle,)

    @extend_schema(
        request=AcceptInvitationSerializer,
        responses={
            200: OrganizationMemberSerializer,
            404: OpenApiResponse(description="No open invitation."),
        },
    )
    def post(self, request, token: str):
        invitation = _open_invitation(token)
        organization = invitation.organization
        payload = AcceptInvitationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        with transaction.atomic():
            if request.user.is_authenticated:
                user = request.user
            else:
                user = self._create_account(invitation, payload.validated_data)
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            if organization.editor_id != user.pk:
                organization.members.add(user)
            invitation.accepted_at = timezone.now()
            invitation.accepted_by = user
            invitation.save(update_fields=["accepted_at", "accepted_by"])
        return Response(OrganizationMemberSerializer(_member(user, organization)).data)

    @staticmethod
    def _create_account(invitation: OrganizationInvitation, data: dict) -> User:
        # The token holder reads the invited address's mailbox, so telling
        # them that it has an account tells them nothing they could not learn.
        if get_user_model().objects.filter(email__iexact=invitation.email).exists():
            raise ValidationError(
                {
                    "detail": "Denne e-postadressen har allerede en bruker. Logg inn først, og åpne lenken igjen."
                }
            )
        missing = {
            field: "Fyll inn feltet."
            for field in ("first_name", "last_name", "password")
            if not data.get(field, "").strip()
        }
        if missing:
            raise ValidationError(missing)
        user = User(
            email=invitation.email,
            first_name=data["first_name"].strip(),
            last_name=data["last_name"].strip(),
        )
        check_password_strength(data["password"], user)
        user.set_password(data["password"])
        user.save()
        return user
