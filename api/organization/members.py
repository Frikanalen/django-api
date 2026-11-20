"""
Who works for an organization: its editor and its members.

Members act on the organization's behalf: they upload videos, and once
their identity is confirmed they set up airtime. The editor answers for
the organization and decides who its members are, by inviting them (see
invitations.py) and removing them; staff can do the same.
A member can leave on their own. The editor is not a member to remove:
changing editor goes through Frikanalen, because the new editor must sign
the declaration.
"""

import logging

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from api.auth.permissions import can_administer_organization
from fk.models import Organization, User

logger = logging.getLogger(__name__)


class OrganizationMemberSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    email = serializers.EmailField()
    is_editor = serializers.BooleanField()
    identity_confirmed = serializers.BooleanField(
        help_text="Whether Frikanalen has confirmed who they are: needed to set up airtime."
    )


def _member(user: User, organization: Organization) -> dict:
    return {
        "id": user.pk,
        "name": f"{user.first_name} {user.last_name}".strip(),
        "email": user.email,
        "is_editor": organization.editor_id == user.pk,
        "identity_confirmed": user.identity_confirmed,
    }


def _people(organization: Organization) -> list[dict]:
    """The editor first, then the members by name."""
    members = sorted(
        organization.members.exclude(pk=organization.editor_id),
        key=lambda user: (f"{user.first_name} {user.last_name}".strip().lower(), user.email),
    )
    people = [organization.editor] if organization.editor else []
    return [_member(user, organization) for user in people + members]


def _may_manage(user, organization: Organization) -> bool:
    return user.is_staff or organization.editor_id == user.pk


class OrganizationMembers(APIView):
    """
    The organization's editor and members. Everyone in the organization sees
    the list. People join by accepting an invitation (see invitations.py).
    """

    permission_classes = (permissions.IsAuthenticated,)

    def _organization(self, request, pk: int) -> Organization:
        organization = get_object_or_404(Organization.objects.select_related("editor"), pk=pk)
        if not can_administer_organization(request.user, organization):
            raise PermissionDenied("Du hører ikke til denne organisasjonen.")
        return organization

    @extend_schema(responses=OrganizationMemberSerializer(many=True))
    def get(self, request, pk: int):
        organization = self._organization(request, pk)
        return Response(OrganizationMemberSerializer(_people(organization), many=True).data)


class OrganizationMember(APIView):
    """
    One member. The editor and staff remove members; a member removes
    themselves to leave. The editor cannot be removed here.
    """

    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(responses={204: None})
    def delete(self, request, pk: int, user_id: int):
        organization = get_object_or_404(Organization.objects.all(), pk=pk)
        if not can_administer_organization(request.user, organization):
            raise PermissionDenied("Du hører ikke til denne organisasjonen.")
        leaving = request.user.pk == user_id
        if not leaving and not _may_manage(request.user, organization):
            raise PermissionDenied("Bare redaktøren kan fjerne medlemmer.")
        if organization.editor_id == user_id:
            raise ValidationError(
                {
                    "detail": "Redaktøren kan ikke fjernes. Ta kontakt med Frikanalen for å "
                    "bytte redaktør."
                }
            )
        member = organization.members.filter(pk=user_id).first()
        if member is None:
            raise NotFound("Personen er ikke medlem i organisasjonen.")
        organization.members.remove(member)
        return Response(status=status.HTTP_204_NO_CONTENT)
