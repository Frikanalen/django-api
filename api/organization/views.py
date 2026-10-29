from rest_framework import generics

from api.auth.permissions import IsOrganizationEditorOrReadOnly
from api.organization.serializers import OrganizationSerializer
from api.pagination import FkDefaultPagination
from fk.models import Organization


class OrganizationList(generics.ListCreateAPIView):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    pagination_class = FkDefaultPagination
    permission_classes = (IsOrganizationEditorOrReadOnly,)

    def get_queryset(self):
        # An organization with no ansvarlig redaktor is staff-only until
        # one is appointed; see OrganizationQuerySet.
        return Organization.objects.visible_to(self.request.user)

    def perform_create(self, serializer):
        serializer.save(editor=self.request.user)


class OrganizationDetail(generics.RetrieveUpdateDestroyAPIView):
    """
    Video file details
    """

    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = (IsOrganizationEditorOrReadOnly,)

    def get_queryset(self):
        return Organization.objects.visible_to(self.request.user)
