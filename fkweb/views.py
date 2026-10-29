from django.middleware.csrf import get_token
from rest_framework import serializers
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


class CsrfSerializer(serializers.Serializer):
    csrfToken = serializers.CharField()


class CsrfView(RetrieveAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []  # no auth required just to mint token
    serializer_class = CsrfSerializer

    def get(self, request, *args, **kwargs):
        # This ensures the csrftoken cookie is set on the response
        serializer = self.get_serializer({"csrfToken": get_token(request)})
        return Response(serializer.data)
