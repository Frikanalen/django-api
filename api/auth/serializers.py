from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import translation
from rest_framework import serializers

from fk.models import Organization, User


def check_password_strength(password: str, user) -> None:
    """Run AUTH_PASSWORD_VALIDATORS, reporting failures on `password`.

    `user` may be unsaved; the similarity check only reads its attributes.
    Django ships Norwegian for most of its validator messages, and the site
    is Norwegian even though the API's default language is not.
    """
    with translation.override("nb"):
        try:
            validate_password(password, user=user)
        except DjangoValidationError as error:
            raise serializers.ValidationError({"password": [str(m) for m in error.messages]})


class NewUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    # These need to be explicitly included because
    # they are not required in the database model,
    # but we want new users to have these values set.
    first_name = serializers.CharField()
    last_name = serializers.CharField()

    def validate(self, attrs):
        candidate = User(
            email=attrs.get("email", ""),
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        check_password_strength(attrs["password"], candidate)
        return attrs

    def create(self, validated_data):
        user = get_user_model().objects.create(
            email=validated_data["email"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
        )

        user.set_password(validated_data["password"])
        user.save()

        return user

    class Meta:
        model = User
        # No date_of_birth: policy is not to ask for it at registration.
        fields = ("id", "email", "first_name", "last_name", "password")

        write_only_fields = ("password",)


class SimpleOrgSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ("id", "name")


class UserSerializer(serializers.ModelSerializer):
    # No password here: changing it takes the current one, which is
    # POST /api/user/password (api/auth/password_reset.py).
    editor_of = SimpleOrgSerializer(source="editor", many=True, read_only=True)
    member_of = SimpleOrgSerializer(source="organization_set", many=True, read_only=True)

    class Meta:
        model = User
        read_only_fields = (
            "id",
            "email",
            "is_staff",
            "identity_confirmed",
            "date_joined",
            "editor_of",
            "member_of",
        )

        fields = (
            *read_only_fields,
            "first_name",
            "last_name",
            "date_of_birth",
            "phone_number",
        )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()
    # Only for accounts with two-factor on: a code from the authenticator
    # app, or a recovery code. Without it such a login answers 400 with
    # code `otp_required` on `otp`.
    otp = serializers.CharField(required=False, allow_blank=True, write_only=True)


class LogoutSerializer(serializers.Serializer):
    """Representation for the logout response: a short human-readable message."""

    detail = serializers.CharField(read_only=True)
