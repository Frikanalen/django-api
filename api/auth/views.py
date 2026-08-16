from django.contrib.auth import authenticate, login, logout
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import generics
from rest_framework.authentication import BasicAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from api.auth.serializers import LoginSerializer, NewUserSerializer, TokenSerializer, UserSerializer


class XBasicAuth(BasicAuthentication):
    def authenticate_header(self, request):
        return "XXXBasic"


@method_decorator(never_cache, name="get")
class ObtainAuthToken(generics.RetrieveAPIView):
    """
    Get a token you can use as a header instead of basic auth.

    Use the header with HTTP like:
        Authorization: Token 000000000000...
    """

    queryset = Token.objects.all()
    serializer_class = TokenSerializer
    authentication_classes = [XBasicAuth]
    permission_classes = (IsAuthenticated,)

    def get_object(self, queryset=None):
        return get_object_or_404(Token, user=self.request.user)


class UserCreate(generics.CreateAPIView):
    throttle_classes = [AnonRateThrottle]
    permission_classes = [AllowAny]

    serializer_class = NewUserSerializer

    def perform_create(self, serializer):
        """Log user in on successful registration"""
        new_user = serializer.save()
        login(self.request, new_user)


@method_decorator(never_cache, name="dispatch")
class UserDetail(generics.RetrieveUpdateDestroyAPIView):
    """
    User details - used to manage your own user
    """

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self, queryset=None):
        return self.request.user

    def perform_destroy(self, instance):
        # Deleting the row would raise ProtectedError for anyone who has
        # uploaded a video; see User.anonymize for why the account is
        # scrubbed instead. Still a 204 - from the caller's side the
        # account is gone.
        Token.objects.filter(user=instance).delete()
        instance.anonymize()


class UserLogin(CreateAPIView):
    """Sets a session cookie for the user"""

    serializer_class = LoginSerializer
    permission_classes = (AllowAny,)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        user = authenticate(username=data["email"], password=data["password"])

        # ModelBackend refuses inactive users, so they land here too and
        # are indistinguishable from a wrong password - deliberately: a
        # dedicated "disabled" answer would reveal the account exists.
        if not user:
            raise AuthenticationFailed("Incorrect email or password.")

        login(request._request, user)
        return Response(UserSerializer(user).data)


@extend_schema(exclude=True)
class UserLogout(APIView):
    def post(self, request):
        logout(request)
        return Response()
