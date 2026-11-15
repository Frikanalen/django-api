from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.response import Response

from api.grants.views import _drf
from api.pagination import FkDefaultPagination
from api.posters.serializers import PosterSerializer, PosterUpdateSerializer
from scheduling import services
from scheduling.models import Poster


class PosterFilter(filters.FilterSet):
    class Meta:
        model = Poster
        fields = ["organization"]


class PosterViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Text posters: a heading and a few lines of text on one of the on-air
    graphics templates, which an organization lists in its airtime like a
    video. Members see and edit their organizations' posters; staff see
    every one.
    """

    queryset = Poster.objects.none()
    serializer_class = PosterSerializer
    pagination_class = FkDefaultPagination
    filterset_class = PosterFilter
    permission_classes = (permissions.IsAuthenticated,)
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = Poster.objects.select_related("organization")
        if getattr(self, "swagger_fake_view", False):
            return Poster.objects.none()
        user = self.request.user
        if user.is_staff:
            return queryset
        return queryset.filter(
            Q(organization__editor=user) | Q(organization__members=user)
        ).distinct()

    def get_serializer_class(self):
        if self.action == "partial_update":
            return PosterUpdateSerializer
        return PosterSerializer

    def create(self, request, *args, **kwargs):
        serializer = PosterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        try:
            poster = services.create_poster(user=request.user, **data)
        except DjangoValidationError as error:
            raise _drf(error) from error
        return Response(PosterSerializer(poster).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=PosterUpdateSerializer, responses=PosterSerializer)
    def partial_update(self, request, *args, **kwargs):
        poster = self.get_object()
        serializer = PosterUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            poster = services.update_poster(poster, user=request.user, **serializer.validated_data)
        except DjangoValidationError as error:
            raise _drf(error) from error
        return Response(PosterSerializer(poster).data)

    def destroy(self, request, *args, **kwargs):
        poster = self.get_object()
        try:
            services.delete_poster(poster, user=request.user)
        except DjangoValidationError as error:
            raise _drf(error) from error
        return Response(status=status.HTTP_204_NO_CONTENT)
