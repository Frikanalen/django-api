from django.contrib.auth import get_user_model, update_session_auth_hash
from rest_framework import serializers
from rest_framework.authtoken.models import Token

from fk.models import Organization, User


class TokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = Token
        fields = (
            "created",
            "key",
            "user",
        )


class NewUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    # These need to be explicitly included because
    # they are not required in the database model,
    # but we want new users to have these values set.
    first_name = serializers.CharField()
    last_name = serializers.CharField()

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
    password = serializers.CharField(write_only=True, required=False)
    editor_of = SimpleOrgSerializer(source="editor", many=True, read_only=True)
    member_of = SimpleOrgSerializer(source="organization_set", many=True, read_only=True)

    def update(self, instance, validated_data):
        # The default update() would write the raw password string to the
        # password column; it must go through set_password to be hashed.
        password = validated_data.pop("password", None)
        user = super().update(instance, validated_data)
        if password is not None:
            user.set_password(password)
            user.save(update_fields=["password"])
            request = self.context.get("request")
            if request is not None:
                # Keep the current session alive; other sessions are
                # invalidated by the rotated session auth hash.
                update_session_auth_hash(request, user)
        return user

    class Meta:
        model = User
        read_only_fields = ("id", "email", "is_staff", "date_joined", "editor_of", "member_of")

        fields = (
            *read_only_fields,
            "first_name",
            "last_name",
            "date_of_birth",
            "phone_number",
            "password",
        )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()


class LogoutSerializer(serializers.Serializer):
    """Representation for the logout response: a short human-readable message."""

    detail = serializers.CharField(read_only=True)
