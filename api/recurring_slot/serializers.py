from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from api.auth.permissions import can_administer_organization
from fk.models import (
    RecurringSlotCreationRequest,
    RecurringSlotOwnershipRequest,
    ReviewStatus,
)
from scheduling.models import FillRule, RecurringSlot


class FillRuleSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = FillRule
        fields = ("id", "name", "pool", "ordering", "mode")
        read_only_fields = fields


class RecurringSlotSerializer(serializers.ModelSerializer):
    """Recurring airtime. Each occurrence becomes an airtime grant two
    Mondays before it airs; the grant copies the slot's time."""

    default_fill_rule = FillRuleSummarySerializer(read_only=True, allow_null=True)
    description = serializers.CharField(source="describe", read_only=True)

    class Meta:
        model = RecurringSlot
        fields = (
            "id",
            "organization",
            "frequency",
            "interval",
            "weekdays",
            "month_weeks",
            "start_time",
            "duration",
            "starts_on",
            "ends_on",
            "default_fill_rule",
            "description",
        )
        read_only_fields = fields


def require_administered_organization(serializer, attrs):
    organization = attrs["organization"]
    if not can_administer_organization(serializer.context["request"].user, organization):
        raise PermissionDenied("You must administer the requested organization.")
    return organization


class RecurringSlotCreationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecurringSlotCreationRequest
        fields = (
            "id",
            "organization",
            "requested_by",
            "day",
            "start_time",
            "duration",
            "status",
            "reviewed_by",
            "admin_comment",
            "created_at",
            "reviewed_at",
            "slot",
        )
        read_only_fields = (
            "id",
            "requested_by",
            "status",
            "reviewed_by",
            "admin_comment",
            "created_at",
            "reviewed_at",
            "slot",
        )

    def validate(self, attrs):
        require_administered_organization(self, attrs)
        return attrs


class RecurringSlotOwnershipRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecurringSlotOwnershipRequest
        fields = (
            "id",
            "organization",
            "requested_by",
            "slot",
            "previous_organization",
            "status",
            "reviewed_by",
            "admin_comment",
            "created_at",
            "reviewed_at",
        )
        read_only_fields = (
            "id",
            "requested_by",
            "previous_organization",
            "status",
            "reviewed_by",
            "admin_comment",
            "created_at",
            "reviewed_at",
        )

    def validate(self, attrs):
        organization = require_administered_organization(self, attrs)
        slot = attrs["slot"]
        if slot.organization_id == organization.pk:
            raise serializers.ValidationError({"slot": "Your organization already owns this slot."})
        if RecurringSlotOwnershipRequest.objects.filter(
            organization=organization, slot=slot, status=ReviewStatus.PENDING
        ).exists():
            raise serializers.ValidationError(
                {"slot": "A pending ownership request already exists."}
            )
        attrs["previous_organization"] = slot.organization
        return attrs


@extend_schema_serializer(component_name="RecurringSlotRequestEnvelope")
class RecurringSlotRequestSerializer(serializers.Serializer):
    """Read envelope around the two deliberately separate request models."""

    creation = RecurringSlotCreationRequestSerializer(read_only=True)
    ownership = RecurringSlotOwnershipRequestSerializer(read_only=True)

    def to_representation(self, instance):
        if isinstance(instance, RecurringSlotCreationRequest):
            data = RecurringSlotCreationRequestSerializer(instance, context=self.context).data
            return {"creation": data}
        data = RecurringSlotOwnershipRequestSerializer(instance, context=self.context).data
        return {"ownership": data}
