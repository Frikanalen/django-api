from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from api.auth.permissions import can_administer_organization
from fk.models import (
    Organization,
    SlotSourceType,
    Video,
    WeeklySlot,
    WeeklySlotCreationRequest,
    WeeklySlotOwnershipRequest,
    WeeklySlotRequestStatus,
    WeeklySlotSource,
)


class WeeklySlotSourceSerializer(serializers.ModelSerializer):
    direct_videos = serializers.PrimaryKeyRelatedField(
        queryset=Video.objects.all(),
        many=True,
        required=False,
    )
    organization = serializers.PrimaryKeyRelatedField(queryset=Organization.objects.all())

    class Meta:
        model = WeeklySlotSource
        fields = ("id", "name", "type", "strategy", "organization", "direct_videos")
        read_only_fields = ("id",)

    def validate(self, attrs):
        request = self.context["request"]
        organization = attrs.get("organization")
        if organization is None and self.instance is not None:
            organization = self.instance.organization
        if organization is None:
            raise serializers.ValidationError({"organization": "This field is required."})
        if not can_administer_organization(request.user, organization):
            raise PermissionDenied("You must administer the source's organization.")
        if (
            self.instance is not None
            and organization.pk != self.instance.organization_id
            and not request.user.is_staff
        ):
            raise serializers.ValidationError(
                {"organization": "An existing source cannot be transferred."}
            )

        source_type = attrs.get("type", getattr(self.instance, "type", None))
        direct_videos = attrs.get("direct_videos")
        if source_type == SlotSourceType.ORGANIZATION and direct_videos:
            raise serializers.ValidationError(
                {"direct_videos": "Organization sources cannot select individual videos."}
            )
        if direct_videos is not None:
            foreign_videos = [
                video.pk for video in direct_videos if video.organization_id != organization.pk
            ]
            if foreign_videos:
                raise serializers.ValidationError(
                    {
                        "direct_videos": "Every selected video must belong to the source's organization."
                    }
                )

        if self.instance is not None and organization.pk != self.instance.organization_id:
            mismatched_slots = self.instance.weeklyslot_set.exclude(organization=organization)
            if mismatched_slots.exists():
                raise serializers.ValidationError(
                    {
                        "organization": "Move or remove the source from its slots before transferring it."
                    }
                )
        return attrs


class WeeklySlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = WeeklySlot
        fields = ("id", "organization", "source", "day", "start_time", "duration")
        read_only_fields = ("id", "organization", "day", "start_time", "duration")

    def validate_source(self, source):
        if source is not None and source.organization_id != self.instance.organization_id:
            raise serializers.ValidationError("The source must belong to the slot's organization.")
        return source

    def validate(self, attrs):
        immutable = {"organization", "day", "start_time", "duration"}
        attempted = sorted(immutable.intersection(self.initial_data))
        if attempted:
            raise serializers.ValidationError(
                {
                    field: "This field cannot be changed by an organization member."
                    for field in attempted
                }
            )
        return attrs


def require_administered_organization(serializer, attrs):
    initial_data = getattr(serializer, "initial_data", None)
    if initial_data is None and serializer.parent is not None:
        initial_data = serializer.parent.initial_data.get(serializer.field_name, {})
    if "source" in initial_data:
        raise serializers.ValidationError(
            {"source": "Assign a source after the request has been approved."}
        )
    organization = attrs["organization"]
    if not can_administer_organization(serializer.context["request"].user, organization):
        raise PermissionDenied("You must administer the requested organization.")
    return organization


class WeeklySlotCreationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = WeeklySlotCreationRequest
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
            "weekly_slot",
        )
        read_only_fields = (
            "id",
            "requested_by",
            "status",
            "reviewed_by",
            "admin_comment",
            "created_at",
            "reviewed_at",
            "weekly_slot",
        )

    def validate(self, attrs):
        require_administered_organization(self, attrs)
        return attrs


class WeeklySlotOwnershipRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = WeeklySlotOwnershipRequest
        fields = (
            "id",
            "organization",
            "requested_by",
            "weekly_slot",
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
        weekly_slot = attrs["weekly_slot"]
        if weekly_slot.organization_id == organization.pk:
            raise serializers.ValidationError(
                {"weekly_slot": "Your organization already owns this slot."}
            )
        if WeeklySlotOwnershipRequest.objects.filter(
            organization=organization,
            weekly_slot=weekly_slot,
            status=WeeklySlotRequestStatus.PENDING,
        ).exists():
            raise serializers.ValidationError(
                {"weekly_slot": "A pending ownership request already exists."}
            )
        attrs["previous_organization"] = weekly_slot.organization
        return attrs


class WeeklySlotRequestSerializer(serializers.Serializer):
    """Read envelope around the two deliberately separate request models."""

    creation = WeeklySlotCreationRequestSerializer(read_only=True)
    ownership = WeeklySlotOwnershipRequestSerializer(read_only=True)

    def to_representation(self, instance):
        if isinstance(instance, WeeklySlotCreationRequest):
            return {
                "creation": WeeklySlotCreationRequestSerializer(instance, context=self.context).data
            }
        return {
            "ownership": WeeklySlotOwnershipRequestSerializer(instance, context=self.context).data
        }
