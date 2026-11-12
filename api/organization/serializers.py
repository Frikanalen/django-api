from phonenumber_field.phonenumber import PhoneNumber
from rest_framework import serializers

from api.serializers import logger
from fk.models import Organization


class OrganizationSerializer(serializers.ModelSerializer):
    editor_name = serializers.SerializerMethodField()
    editor_email = serializers.SerializerMethodField()
    editor_msisdn = serializers.SerializerMethodField()
    fkmember = serializers.BooleanField(read_only=True)
    is_rogue = serializers.BooleanField(read_only=True)

    # Shown for a suspended organization to anyone but staff: enough to
    # name it on the suspension notice, nothing to promote it with.
    SUSPENDED_FIELDS = ("id", "name", "fkmember", "is_rogue")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if instance.is_rogue and not getattr(user, "is_staff", False):
            for field in data:
                if field not in self.SUSPENDED_FIELDS:
                    # Blank rather than drop, so the response keeps its shape.
                    data[field] = "" if isinstance(data[field], str) else None
        return data

    def get_editor_email(self, obj: Organization) -> str | None:
        if obj.editor:
            return obj.editor.email
        return None

    def get_editor_msisdn(self, obj: Organization) -> str | None:
        """The editor's number in international format, or None if there isn't one."""
        if not obj.editor:
            return None
        number = obj.editor.phone_number
        # phone_number is blank=True, so a blank value stays a plain str, and an
        # unparseable one formats as the literal string "None".
        if not isinstance(number, PhoneNumber) or not number.is_valid():
            return None
        return number.as_international

    def get_editor_name(self, obj: Organization) -> str:
        if obj.editor:
            return obj.editor.first_name + " " + obj.editor.last_name
        logger.warning("Organization %d has no editor assigned", obj.id)
        return "Ingen redaktør!"

    class Meta:
        model = Organization
        fields = (
            "id",
            "name",
            "homepage",
            "description",
            "postal_address",
            "street_address",
            "editor_id",
            "editor_name",
            "editor_email",
            "editor_msisdn",
            "fkmember",
            "is_rogue",
        )
