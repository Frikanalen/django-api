from rest_framework import serializers

from api.serializers import logger
from fk.brreg import is_valid_orgnr, normalize_orgnr
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

    ORGNR_REQUIRED = "Organisasjonsnummer fra Brønnøysundregistrene må fylles ut."

    def validate_orgnr(self, value: str) -> str:
        """A real organisasjonsnummer, stored without spaces."""
        value = normalize_orgnr(value)
        if not value:
            raise serializers.ValidationError(self.ORGNR_REQUIRED)
        if not is_valid_orgnr(value):
            raise serializers.ValidationError(
                "Organisasjonsnummeret må ha ni siffer og et gyldig kontrollsiffer."
            )
        return value

    def get_fields(self):
        fields = super().get_fields()
        # Every new organization is registered in Enhetsregisteret. Older
        # ones may lack the number, so an update that leaves it out is
        # let through; one that blanks it is not (see validate_orgnr).
        if self.instance is None:
            fields["orgnr"].required = True
            fields["orgnr"].error_messages["required"] = self.ORGNR_REQUIRED
        return fields

    def get_editor_email(self, obj: Organization) -> str | None:
        if obj.editor:
            return obj.editor.email
        return None

    def get_editor_msisdn(self, obj: Organization) -> str | None:
        """Always None: no phone number is published for an organization or
        its editor. Kept so clients built against the old schema still parse."""
        return None

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
            "orgnr",
            "postal_address",
            "street_address",
            "editor_id",
            "editor_name",
            "editor_email",
            "editor_msisdn",
            "fkmember",
            "is_rogue",
        )


class BrregEntitySerializer(serializers.Serializer):
    """An entity in Enhetsregisteret, as the fields of a new organization."""

    orgnr = serializers.CharField()
    name = serializers.CharField()
    organization_form = serializers.CharField(help_text="E.g. Forening/lag/innretning")
    homepage = serializers.CharField(allow_blank=True)
    purpose = serializers.CharField(
        allow_blank=True, help_text="Vedtektsfestet formål: what the organization is for."
    )
    street_address = serializers.CharField(allow_blank=True)
    postal_address = serializers.CharField(allow_blank=True)
    bankrupt = serializers.BooleanField()
    being_dissolved = serializers.BooleanField()
