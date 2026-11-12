from copy import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from api.auth.permissions import can_administer_organization
from fk.models import (
    RecurringSlotCreationRequest,
    RecurringSlotOwnershipRequest,
    ReviewStatus,
)
from scheduling import broadcast_time, services
from scheduling.models import FillRule, RecurringSlot


class FillRuleSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = FillRule
        fields = ("id", "name", "pool", "ordering", "mode")
        read_only_fields = fields


class RecurringSlotSerializer(serializers.ModelSerializer):
    """Recurring airtime. Each occurrence becomes an airtime grant two
    Mondays before it airs; the grant copies the slot's time.

    Staff create, change and delete slots. A change only affects grants
    issued after it: grants already issued keep their time and owner.
    """

    default_fill_rule = FillRuleSummarySerializer(read_only=True, allow_null=True)
    default_fill_rule_id = serializers.PrimaryKeyRelatedField(
        source="default_fill_rule",
        queryset=FillRule.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
        help_text="Leave out to use the owner's 'newest uploads, fill the window' "
        "rule, created if needed. Null means grants don't fill themselves.",
    )
    organization_name = serializers.CharField(
        source="organization.name", read_only=True, allow_null=True, default=None
    )
    description = serializers.CharField(source="describe", read_only=True)

    class Meta:
        model = RecurringSlot
        fields = (
            "id",
            "organization",
            "organization_name",
            "frequency",
            "interval",
            "weekdays",
            "month_weeks",
            "start_time",
            "duration",
            "starts_on",
            "ends_on",
            "default_fill_rule",
            "default_fill_rule_id",
            "description",
        )
        read_only_fields = ("id", "organization_name", "default_fill_rule", "description")
        extra_kwargs = {"organization": {"required": True, "allow_null": True}}

    def validate(self, attrs):
        slot = copy(self.instance) if self.instance else RecurringSlot()
        for field, value in attrs.items():
            setattr(slot, field, value)

        organization = slot.organization
        owner_changed = self.instance is None or (
            "organization" in attrs and attrs["organization"] != self.instance.organization
        )
        if owner_changed and organization is not None and organization.is_rogue:
            raise serializers.ValidationError(
                {"organization": "This organization is suspended and can't be given airtime."}
            )
        # Leaving the rule out on a new owner means the owner's standard
        # rule (the old owner's can't follow the slot). It is looked up or
        # created in save(), so validation writes nothing.
        self._wants_standard_rule = (
            "default_fill_rule" not in attrs and owner_changed and organization is not None
        )
        if "default_fill_rule" not in attrs and owner_changed:
            attrs["default_fill_rule"] = slot.default_fill_rule = None

        try:
            slot.clean()
        except DjangoValidationError as error:
            errors = error.message_dict
            if "default_fill_rule" in errors:  # the field it is written through
                errors["default_fill_rule_id"] = errors.pop("default_fill_rule")
            raise serializers.ValidationError(errors) from error

        conflicts = services.slot_conflicts(slot)
        if conflicts:
            raise serializers.ValidationError(
                {
                    "non_field_errors": [
                        f"Overlaps {other.organization or 'the station'}: {other.describe()} "
                        f"{other.start_time:%H:%M}, first on {hit.astimezone(broadcast_time.TZ):%Y-%m-%d %H:%M}."
                        for other, hit in conflicts
                    ]
                }
            )
        return attrs

    def _with_rule(self, validated_data):
        if self._wants_standard_rule:
            validated_data["default_fill_rule"] = services.standard_fill_rule(
                validated_data.get("organization") or self.instance.organization
            )
        return validated_data

    def create(self, validated_data):
        with transaction.atomic():
            return super().create(self._with_rule(validated_data))

    def update(self, instance, validated_data):
        with transaction.atomic():
            return super().update(instance, self._with_rule(validated_data))


def require_administered_organization(serializer, attrs):
    organization = attrs["organization"]
    if not can_administer_organization(serializer.context["request"].user, organization):
        raise PermissionDenied("You must administer the requested organization.")
    if organization.is_rogue:
        raise PermissionDenied("This organization is suspended.")
    return organization


class RequestPartiesMixin(serializers.Serializer):
    """Names for the organization and requester, so lists need no lookups."""

    organization_name = serializers.CharField(source="organization.name", read_only=True)
    requested_by_name = serializers.SerializerMethodField()

    def get_requested_by_name(self, obj) -> str:
        user = obj.requested_by
        return f"{user.first_name} {user.last_name}".strip() or user.email


class RecurringSlotCreationRequestSerializer(RequestPartiesMixin, serializers.ModelSerializer):
    class Meta:
        model = RecurringSlotCreationRequest
        fields = (
            "id",
            "organization",
            "organization_name",
            "requested_by",
            "requested_by_name",
            "message",
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


class RecurringSlotOwnershipRequestSerializer(RequestPartiesMixin, serializers.ModelSerializer):
    slot_description = serializers.SerializerMethodField()

    def get_slot_description(self, obj) -> str:
        return f"{obj.slot.describe()} {obj.slot.start_time:%H:%M}"

    class Meta:
        model = RecurringSlotOwnershipRequest
        fields = (
            "id",
            "organization",
            "organization_name",
            "requested_by",
            "requested_by_name",
            "message",
            "slot",
            "slot_description",
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
