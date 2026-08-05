from rest_framework import viewsets

from api.auth.permissions import IsStaffOrReadOnly

from .models import Bulletin
from .serializers import BulletinSerializer


class BulletinViewSet(viewsets.ModelViewSet):
    queryset = Bulletin.objects.all().order_by("-created")
    serializer_class = BulletinSerializer
    permission_classes = (IsStaffOrReadOnly,)
    pagination_class = None
