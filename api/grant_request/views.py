from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, permissions
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from api.grant_request.serializers import (
    AirtimeGrantRequestSerializer,
    DecisionSerializer,
    GrantExemptionRequestSerializer,
    GrantRequestSerializer,
)
from api.pagination import FkDefaultPagination
from api.recurring_slot.serializers import (
    RecurringSlotCreationRequestSerializer,
    RecurringSlotOwnershipRequestSerializer,
)
from fk.models import (
    AirtimeGrantRequest,
    GrantExemptionRequest,
    RecurringSlotCreationRequest,
    RecurringSlotOwnershipRequest,
    ReviewStatus,
)

REQUEST_FILTERS = [
    OpenApiParameter("organization", int, description="Only this organization's requests"),
    OpenApiParameter(
        "status", str, enum=[choice.value for choice in ReviewStatus], description="Only these"
    ),
]


def administered(user):
    return Q(organization__editor=user) | Q(organization__members=user)


def filter_requests(queryset, request):
    """Staff see every request; anyone else their own organizations'.
    Narrowed by ?organization= and ?status=."""
    if not request.user.is_staff:
        queryset = queryset.filter(administered(request.user)).distinct()
    organization = request.query_params.get("organization")
    if organization:
        if not organization.isdigit():
            raise ValidationError({"organization": "Must be an organization id."})
        queryset = queryset.filter(organization_id=int(organization))
    status = request.query_params.get("status")
    if status:
        if status not in ReviewStatus.values:
            raise ValidationError({"status": f"One of {', '.join(ReviewStatus.values)}."})
        queryset = queryset.filter(status=status)
    return queryset


class GrantRequestList(generics.ListAPIView):
    """Airtime and exemption requests together, newest first."""

    queryset = AirtimeGrantRequest.objects.none()  # for schema introspection
    serializer_class = GrantRequestSerializer
    pagination_class = FkDefaultPagination
    permission_classes = (permissions.IsAuthenticated,)
    filter_backends = ()

    @extend_schema(parameters=REQUEST_FILTERS)
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        parties = ("organization", "requested_by", "reviewed_by")
        requests = sorted(
            [
                *filter_requests(AirtimeGrantRequest.objects.select_related(*parties), request),
                *filter_requests(
                    GrantExemptionRequest.objects.select_related(*parties, "grant"), request
                ),
            ],
            key=lambda item: (item.created_at, item.pk),
            reverse=True,
        )
        page = self.paginate_queryset(requests)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        return Response(self.get_serializer(requests, many=True).data)


class AirtimeGrantRequestCreate(generics.CreateAPIView):
    serializer_class = AirtimeGrantRequestSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)


class GrantExemptionRequestCreate(generics.CreateAPIView):
    serializer_class = GrantExemptionRequestSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)


class DecisionView(generics.GenericAPIView):
    """Approve or deny a pending request, once, with a comment for the
    organization. Approval applies the request in the same transaction:
    the grant, slot, transfer or exemption is created then, or the
    decision fails and the request stays pending."""

    permission_classes = (permissions.IsAdminUser,)

    def get_serializer_class(self):
        return DecisionSerializer

    def post(self, request, *args, **kwargs):
        decision = DecisionSerializer(data=request.data)
        decision.is_valid(raise_exception=True)
        reviewed = self.get_object()
        try:
            reviewed.decide(
                admin=request.user,
                status=decision.validated_data["status"],
                comment=decision.validated_data["admin_comment"],
            )
        except DjangoValidationError as error:
            raise ValidationError(error.message_dict) from error
        return Response(self.result_serializer(reviewed, context={"request": request}).data)


def decision_view(model, result_serializer):
    return extend_schema(request=DecisionSerializer, responses=result_serializer)(
        type(
            f"{model.__name__}Decision",
            (DecisionView,),
            {"queryset": model.objects.all(), "result_serializer": result_serializer},
        )
    )


AirtimeGrantRequestDecision = decision_view(AirtimeGrantRequest, AirtimeGrantRequestSerializer)
GrantExemptionRequestDecision = decision_view(
    GrantExemptionRequest, GrantExemptionRequestSerializer
)
RecurringSlotCreationRequestDecision = decision_view(
    RecurringSlotCreationRequest, RecurringSlotCreationRequestSerializer
)
RecurringSlotOwnershipRequestDecision = decision_view(
    RecurringSlotOwnershipRequest, RecurringSlotOwnershipRequestSerializer
)
