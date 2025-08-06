from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.authtoken.models import Token

from fk.models import User, Organization


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
        fields = ("id", "email", "first_name", "last_name", "date_of_birth", "password")

        write_only_fields = ("password",)


class SimpleOrgSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ("id", "name")


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)
    editor_of = SimpleOrgSerializer(source="editor", many=True, read_only=True)
    member_of = SimpleOrgSerializer(source="organization_set", many=True, read_only=True)

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
