from copy import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from api.auth.permissions import can_administer_organization
from api.programming.serializers import RecipeSerializer, build_recipe
from programming.models import Recipe
from programming.services import uploads_recipe
from scheduling import broadcast_time, services
from scheduling.models import (
    RecurringSlot,
    RecurringSlotCreationRequest,
    RecurringSlotOwnershipRequest,
    ReviewStatus,
)

_UNSET = object()


class RecurringSlotSerializer(serializers.ModelSerializer):
    """Recurring airtime. Each occurrence becomes an airtime grant two
    Mondays before it airs; the grant copies the slot's time and recipe.

    Staff create, change and delete slots. A change only affects grants
    issued after it: grants already issued keep their time and owner.
    """

    recipe = RecipeSerializer(
        required=False,
        allow_null=True,
        help_text="How the slot's grants program themselves. Leave out on a new "
        "owner for everything they upload. Null: the grants are set up by hand. "
        "A jukebox recipe makes this the station's airtime, with no organization.",
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
            "recipe",
            "description",
        )
        read_only_fields = ("id", "organization_name", "description")
        extra_kwargs = {"organization": {"required": True, "allow_null": True}}

    def validate(self, attrs):
        # The recipe is looked up or created in save(), so validation
        # writes nothing.
        self._choice = attrs.pop("recipe", _UNSET)
        jukebox = isinstance(self._choice, dict) and self._choice["kind"] == Recipe.Kind.JUKEBOX
        if jukebox:
            if attrs.get("organization") is not None:
                raise serializers.ValidationError(
                    {"organization": "A jukebox slot is the station's airtime: leave this empty."}
                )
            attrs["organization"] = None

        slot = copy(self.instance) if self.instance else RecurringSlot()
        for field, value in attrs.items():
            setattr(slot, field, value)

        organization = slot.organization
        self._owner_changed = self.instance is None or (
            "organization" in attrs and attrs["organization"] != self.instance.organization
        )
        if self._owner_changed and organization is not None and organization.is_rogue:
            raise serializers.ValidationError(
                {"organization": "This organization is suspended and can't be given airtime."}
            )
        if isinstance(self._choice, dict) and not jukebox and organization is None:
            raise serializers.ValidationError(
                {"recipe": "Only the jukebox runs in the station's own airtime."}
            )

        try:
            slot.clean_fields(exclude=["recipe"])
            slot.recipe = None  # checked against the owner when it is built
            slot.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error

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

    def _with_recipe(self, validated_data):
        organization = (
            validated_data["organization"]
            if "organization" in validated_data or self.instance is None
            else self.instance.organization
        )
        if isinstance(self._choice, dict):
            validated_data["recipe"] = build_recipe(
                self._choice, organization=organization, user=self.context["request"].user
            )
        elif self._choice is None:
            validated_data["recipe"] = None
        elif self._owner_changed:
            # The old owner's recipe can't follow the slot: the new owner
            # gets the standard one, the station none.
            validated_data["recipe"] = uploads_recipe(organization) if organization else None
        return validated_data

    def create(self, validated_data):
        with transaction.atomic():
            return super().create(self._with_recipe(validated_data))

    def update(self, instance, validated_data):
        with transaction.atomic():
            return super().update(instance, self._with_recipe(validated_data))


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
