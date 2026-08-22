# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.

import logging

from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.viewsets import ModelViewSet

from api.auth.permissions import IsStaffOrReadOnly
from api.pagination import FkDefaultPagination
from api.schedule.serializers import AsRunSerializer
from api.serializers import CategorySerializer
from fk.models import AsRun, Category

logger = logging.getLogger(__name__)


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
def api_root(request):
    """
    The root of the FK API / web services
    """
    return Response(
        {
            # Documentation: the schema itself, and the two UIs over it.
            "schema": reverse("schema", request=request),
            "schema/swagger-ui": reverse("swagger-ui", request=request),
            "schema/redoc": reverse("redoc", request=request),
            # Authentication and session bookkeeping.
            "csrf": reverse("api-csrf-detail", request=request),
            "obtain-token": reverse("api-token-auth", request=request),
            "user": reverse("api-user-detail", request=request),
            "user/login": reverse("api-user-login", request=request),
            "user/logout": reverse("api-user-logout", request=request),
            "user/register": reverse("api-user-create", request=request),
            # The API proper.
            "asrun": reverse("asrun-list", request=request),
            "categories": reverse("category-list", request=request),
            "organization": reverse("api-organization-list", request=request),
            "scheduleitems": reverse("api-scheduleitem-list", request=request),
            "scheduling/policy": reverse("api-scheduling-policy", request=request),
            "series": reverse("api-series-list", request=request),
            "videofiles": reverse("api-videofile-list", request=request),
            "videos": reverse("api-video-list", request=request),
            # XML feeds for distributors.
            "tvanytime": reverse("api-tvanytime-home", request=request),
        }
    )


# This class generates an invalid WWW-Authentication header, so that the
# browser does not prompt the user in case of a 401 trying to log in on
# the front-end.


class AsRunViewSet(ModelViewSet):
    """A historic log over what was sent through playout."""

    queryset = AsRun.objects.all()
    serializer_class = AsRunSerializer
    permission_classes = (IsStaffOrReadOnly,)
    pagination_class = FkDefaultPagination


class CategoryViewSet(ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = (IsStaffOrReadOnly,)
    pagination_class = FkDefaultPagination
