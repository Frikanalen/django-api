from phonenumber_field.phonenumber import PhoneNumber
from rest_framework import serializers

from api.serializers import logger
from fk.models import Organization


class OrganizationSerializer(serializers.ModelSerializer):
    editor_name = serializers.SerializerMethodField()
    editor_email = serializers.SerializerMethodField()
    editor_msisdn = serializers.SerializerMethodField()
    fkmember = serializers.BooleanField(read_only=True)

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
        )
