from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django_filters import rest_framework as filters
from rest_framework import viewsets

from api.auth.permissions import IsStaffOrReadOnly
from api.pagination import FkDefaultPagination

from .models import Bulletin
from .serializers import BulletinSerializer

PLACEMENTS = {"air": "on_air", "infographics": "on_infographics"}


class BulletinFilter(filters.FilterSet):
    placement = filters.ChoiceFilter(
        choices=[(name, name) for name in PLACEMENTS],
        method="filter_placement",
        help_text="Only the bulletins that are showing there now: `air` for the "
        "on-air graphics, `infographics` for the infographics rotation.",
    )

    class Meta:
        model = Bulletin
        fields = ["placement"]

    def filter_placement(self, queryset, name, value):
        return queryset.current().filter(**{PLACEMENTS[value]: True})


# Never from the page cache: a bulletin that is withdrawn or runs out has to
# leave the screen at the next fetch, not ten minutes later.
@method_decorator(never_cache, name="dispatch")
class BulletinViewSet(viewsets.ModelViewSet):
    """
    Messages from the channel, shown between programmes by the on-air
    graphics and in the infographics rotation. Anyone can read the
    published ones; staff see every one and edit them. Highest priority
    first, then newest.
    """

    queryset = Bulletin.objects.all()
    serializer_class = BulletinSerializer
    permission_classes = (IsStaffOrReadOnly,)
    filterset_class = BulletinFilter
    # Bounded like every other collection here. The table only grows, and
    # an unpaginated list would eventually be the whole of it every time.
    pagination_class = FkDefaultPagination
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(is_published=True)
