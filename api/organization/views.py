from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, permissions, status
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from api.auth.permissions import IsOrganizationEditorOrReadOnly
from api.organization.serializers import BrregEntitySerializer, OrganizationSerializer
from api.pagination import FkDefaultPagination
from fk import brreg
from fk.models import Organization


class OrganizationList(generics.ListCreateAPIView):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    pagination_class = FkDefaultPagination
    permission_classes = (IsOrganizationEditorOrReadOnly,)

    def get_queryset(self):
        # An organization with no ansvarlig redaktor is staff-only until
        # one is appointed; see OrganizationQuerySet. The serializer reads
        # the editor's name, email and phone, so join it rather than fetch
        # it once per organization.
        return Organization.objects.visible_to(self.request.user).select_related("editor")

    def perform_create(self, serializer):
        serializer.save(editor=self.request.user)


class OrganizationDetail(generics.RetrieveUpdateDestroyAPIView):
    """
    Organization details. A suspended organization (`is_rogue`) can still
    be fetched, so its page can show the suspension notice, but only its
    id, name and flag are returned to anyone but staff.
    """

    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = (IsOrganizationEditorOrReadOnly,)

    def get_queryset(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return Organization.objects.presentable_to(self.request.user)
        return Organization.objects.visible_to(self.request.user)


class BrregEntityDeleted(APIException):
    status_code = status.HTTP_410_GONE
    default_detail = "Organisasjonen er slettet fra Enhetsregisteret."
    default_code = "deleted"


class BrregUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Fikk ikke svar fra Brønnøysundregistrene. Fyll inn feltene selv."
    default_code = "brreg_unavailable"


class BrregLookup(APIView):
    """
    Look up an organisasjonsnummer in Enhetsregisteret, so the form for a
    new organization can fill itself in. For logged-in users only: it is
    there to register an organization, not as an open proxy.
    """

    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(
        responses={
            200: BrregEntitySerializer,
            400: OpenApiResponse(description="Not a valid organisasjonsnummer."),
            404: OpenApiResponse(description="No entity has this number."),
            410: OpenApiResponse(description="The entity has been deleted from the register."),
            503: OpenApiResponse(description="Enhetsregisteret did not answer."),
        }
    )
    def get(self, request, orgnr: str):
        orgnr = brreg.normalize_orgnr(orgnr)
        if not brreg.is_valid_orgnr(orgnr):
            raise ValidationError(
                {"orgnr": "Organisasjonsnummeret må ha ni siffer og et gyldig kontrollsiffer."}
            )
        try:
            entity = brreg.lookup(orgnr)
        except brreg.BrregUnavailable as error:
            raise BrregUnavailable() from error
        if entity is None:
            raise NotFound("Fant ingen organisasjon med dette nummeret i Enhetsregisteret.")
        if entity.get("deleted"):
            raise BrregEntityDeleted()
        return Response(BrregEntitySerializer(entity).data)
