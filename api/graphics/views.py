from rest_framework import permissions, viewsets

from api.graphics.serializers import GraphicSerializer
from scheduling.models import Graphic


class GraphicViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Graphics staff can put in a grant's list for as long as they like:
    HTML pages such as the infographics, aired with filler music under
    them. Staff only; they are added in the Django admin.
    """

    queryset = Graphic.objects.all()
    serializer_class = GraphicSerializer
    permission_classes = (permissions.IsAdminUser,)
    pagination_class = None
