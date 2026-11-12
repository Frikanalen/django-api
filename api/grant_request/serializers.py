from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from api.recurring_slot.serializers import RequestPartiesMixin
from fk.models import AirtimeGrantRequest, GrantExemptionRequest, ReviewStatus
from scheduling.models import AirtimeGrant
from scheduling.services import may_act_for

# Longer than a day is a recurring slot or a conversation, not a request.
MAX_AIRTIME = timedelta(hours=24)


REVIEW_FIELDS = (
    "status",
    "reviewed_by",
    "admin_comment",
    "created_at",
    "reviewed_at",
)


def require_may_act_for(serializer, organization):
    user = serializer.context["request"].user
    if not may_act_for(user, organization):
        raise PermissionDenied(
            "You must be a confirmed member of an approved, active member organization."
        )


class AirtimeGrantRequestSerializer(RequestPartiesMixin, serializers.ModelSerializer):
    """A request for one window of airtime. Approval creates the grant."""

    class Meta:
        model = AirtimeGrantRequest
        fields = (
            "id",
            "organization",
            "organization_name",
            "requested_by",
            "requested_by_name",
            "starttime",
            "endtime",
            "message",
            *REVIEW_FIELDS,
            "grant",
        )
        read_only_fields = ("id", "requested_by", *REVIEW_FIELDS, "grant")

    def validate(self, attrs):
        require_may_act_for(self, attrs["organization"])
        start, end = attrs["starttime"], attrs["endtime"]
        if start <= timezone.now():
            raise serializers.ValidationError({"starttime": "Choose a time in the future."})
        if end <= start:
            raise serializers.ValidationError({"endtime": "The end must be after the start."})
        if end - start > MAX_AIRTIME:
            raise serializers.ValidationError(
                {"endtime": "Ask for at most 24 hours at a time, or for a recurring slot."}
            )
        holder = (
            AirtimeGrant.objects.active()
            .filter(starttime__lt=end, endtime__gt=start)
            .select_related("organization")
            .first()
        )
        if holder is not None:
            raise serializers.ValidationError(
                {
                    "starttime": f"{holder.organization} already has airtime "
                    f"{timezone.localtime(holder.starttime):%H:%M}–"
                    f"{timezone.localtime(holder.endtime):%H:%M} that day."
                }
            )
        return attrs


class GrantExemptionRequestSerializer(RequestPartiesMixin, serializers.ModelSerializer):
    """A request to keep changing a grant after its deadline."""

    class Meta:
        model = GrantExemptionRequest
        fields = (
            "id",
            "organization",
            "organization_name",
            "requested_by",
            "requested_by_name",
            "grant",
            "message",
            *REVIEW_FIELDS,
        )
        read_only_fields = ("id", "organization", "requested_by", *REVIEW_FIELDS)

    def validate(self, attrs):
        grant = attrs["grant"]
        require_may_act_for(self, grant.organization)
        if grant.state == AirtimeGrant.State.CANCELLED:
            raise serializers.ValidationError({"grant": "This grant was cancelled."})
        if grant.starttime <= timezone.now():
            raise serializers.ValidationError({"grant": "This grant has already started."})
        if grant.lock_exempt:
            raise serializers.ValidationError({"grant": "This grant can already be changed."})
        if GrantExemptionRequest.objects.filter(grant=grant, status=ReviewStatus.PENDING).exists():
            raise serializers.ValidationError({"grant": "A request for this grant is pending."})
        attrs["organization"] = grant.organization
        return attrs


@extend_schema_serializer(component_name="GrantRequestEnvelope")
class GrantRequestSerializer(serializers.Serializer):
    """Read envelope around the two kinds of grant request."""

    airtime = AirtimeGrantRequestSerializer(read_only=True)
    exemption = GrantExemptionRequestSerializer(read_only=True)

    def to_representation(self, instance):
        if isinstance(instance, AirtimeGrantRequest):
            return {"airtime": AirtimeGrantRequestSerializer(instance, context=self.context).data}
        return {"exemption": GrantExemptionRequestSerializer(instance, context=self.context).data}


DECISION_CHOICES = [
    (ReviewStatus.APPROVED.value, ReviewStatus.APPROVED.label),
    (ReviewStatus.DENIED.value, ReviewStatus.DENIED.label),
]


class DecisionSerializer(serializers.Serializer):
    """A staff decision on any request: final, with a comment."""

    status = serializers.ChoiceField(choices=DECISION_CHOICES)
    admin_comment = serializers.CharField(
        help_text="Shown to the organization. Required, also for approvals."
    )
