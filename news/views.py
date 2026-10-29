from rest_framework import viewsets

from api.auth.permissions import IsStaffOrReadOnly
from api.pagination import FkDefaultPagination

from .models import Bulletin
from .serializers import BulletinSerializer


class BulletinViewSet(viewsets.ModelViewSet):
    queryset = Bulletin.objects.all().order_by("-created")
    serializer_class = BulletinSerializer
    permission_classes = (IsStaffOrReadOnly,)
    # Bounded like every other collection here. The table only grows, and
    # an unpaginated list would eventually be the whole of it every time.
    pagination_class = FkDefaultPagination

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(is_published=True)
