"""
Passkeys and security keys (WebAuthn / FIDO2), on django-otp-webauthn.

django-otp-webauthn supplies the WebAuthnCredential model (a django-otp
device, so it counts as a second factor like an authenticator app) and a
helper around py_webauthn that builds the ceremony options and checks the
browser's answers. Its own views are plain Django views with their own
error shape; these are DRF views around the same helper, so the frontend
gets the API's usual errors and schema.

Three ceremonies, each a begin (options for navigator.credentials) and a
complete (the browser's answer):

- Register: a logged-in user adds a passkey or security key. The helper
  asks for a discoverable credential where the authenticator can store
  one, which is what makes it a passkey that can log in on its own.
- Second factor: after the password (login or reset link), or to let a
  logged-in session change its methods. Any of the user's keys will do,
  and the password already was the first factor, so the key need not ask
  for a PIN or fingerprint.
- Passkey login: no email, no password. The browser offers the passkeys it
  holds for this site; the key must verify the user (PIN, fingerprint,
  face), which makes it two factors in one.
"""

from django.contrib.auth import login
from django_otp_webauthn import exceptions as webauthn_errors
from django_otp_webauthn.helpers import WebAuthnHelper
from django_otp_webauthn.models import WebAuthnCredential
from django_otp_webauthn.utils import rewrite_exceptions
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from webauthn.helpers.structs import ResidentKeyRequirement

from api.auth import two_factor
from api.auth.serializers import UserSerializer

KEY_DEVICE_NAME = "Sikkerhetsnøkkel"

REGISTER_STATE = "webauthn_register_state"
SECOND_FACTOR_STATE = "webauthn_second_factor_state"
PASSKEY_STATE = "webauthn_passkey_state"

# What the person sees when a ceremony fails, by django-otp-webauthn's code.
MESSAGES = {
    "invalid_state": "Økten er utløpt. Prøv igjen.",
    "credential_not_found": "Denne nøkkelen er ikke registrert hos Frikanalen.",
    "credential_user_mismatch": "Denne nøkkelen hører til en annen bruker.",
    "credential_disabled": "Denne nøkkelen er slått av.",
    "user_disabled": "Brukeren er deaktivert.",
}
NOT_VERIFIED = "Nøkkelen kunne ikke bekreftes. Prøv igjen."


class Helper(WebAuthnHelper):
    def get_discoverable_credentials_preference(self) -> ResidentKeyRequirement:
        # A passkey where the authenticator can hold one; a security key
        # with no room left still works as a second factor.
        return ResidentKeyRequirement.PREFERRED


def _helper(request) -> Helper:
    return WebAuthnCredential.get_webauthn_helper(request=request._request)


def _pop_state(request, key: str) -> dict:
    # Popped and saved before anything else, so a state is never used twice.
    state = request.session.pop(key, None)
    request.session.save()
    if not state:
        raise ValidationError({"credential": MESSAGES["invalid_state"]}, code="invalid_state")
    return state


def _run(fn, *args, **kwargs):
    """Run a helper step, turning its errors into the API's."""
    try:
        with rewrite_exceptions():
            return fn(*args, **kwargs)
    except webauthn_errors.OTPWebAuthnApiError as error:
        message = MESSAGES.get(error.code, NOT_VERIFIED)
        if error.status_code == status.HTTP_403_FORBIDDEN:
            raise PermissionDenied(message, code=error.code) from None
        raise ValidationError({"credential": message}, code=error.code) from None


def _usable(device) -> None:
    if not device.confirmed:
        raise PermissionDenied(MESSAGES["credential_disabled"], code="credential_disabled")
    if not device.user.is_active:
        raise PermissionDenied(MESSAGES["user_disabled"], code="user_disabled")


class CredentialSerializer(serializers.Serializer):
    credential = serializers.JSONField(
        help_text="The browser's PublicKeyCredential, as JSON (toJSON(), or @simplewebauthn/browser)."
    )


class RegisterCompleteSerializer(CredentialSerializer):
    name = serializers.CharField(max_length=64, required=False, allow_blank=True)


class _View(generics.GenericAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "two_factor"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # Switched off (PASSKEYS_ENABLED), the ceremonies do not exist.
        if not two_factor.passkeys_enabled():
            raise NotFound()

    # The browser's credential is WebAuthn's own JSON (clientDataJSON,
    # authenticatorData, ...); the API's camelCase parser would rename its
    # keys. This view's own fields are single words, so a plain parser does.
    parser_classes = [JSONParser]


# --- Register ------------------------------------------------------------


class _OptionsView(_View):
    # The options are WebAuthn's JSON already and go to the browser as they
    # are.
    renderer_classes = [JSONRenderer]


class RegisterBegin(_OptionsView):
    """Start adding a passkey or security key: options for
    navigator.credentials.create()."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        two_factor.require_verified_session(request)
        options, state = _helper(request).register_begin(user=request.user)
        request.session[REGISTER_STATE] = state
        return Response(options)


class RegisterComplete(_View):
    """Finish adding the key with the browser's answer.

    With the account's first method, this also returns the recovery codes,
    the only time they are returned.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = RegisterCompleteSerializer

    @extend_schema(responses={201: two_factor.MethodAddedSerializer})
    def post(self, request):
        two_factor.require_verified_session(request)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        state = _pop_state(request, REGISTER_STATE)
        user = request.user

        device = _run(
            _helper(request).register_complete,
            user=user,
            state=state,
            data=serializer.validated_data["credential"],
        )
        device.name = serializer.validated_data.get("name", "").strip() or KEY_DEVICE_NAME
        device.save(update_fields=["name"])
        codes = two_factor.first_method_added(user)
        if not two_factor.session_is_verified(request):
            two_factor.mark_verified(request._request, device)
        return Response(
            two_factor.MethodAddedSerializer(
                {"method": two_factor._method(device), "recovery_codes": codes}
            ).data,
            status=status.HTTP_201_CREATED,
        )


# --- Second factor -------------------------------------------------------


def _second_factor_user(request):
    if request.user.is_authenticated:
        return request.user
    return two_factor.pending_user(request)


class SecondFactorBegin(_OptionsView):
    """Start the second step with a security key: options for
    navigator.credentials.get().

    For a session whose password was just found right (the login or a reset
    link answered `otp_required`), or a logged-in session that wants to
    change its methods. 400 `no_security_key` when the account has none,
    so the client asks for a code instead.
    """

    permission_classes = [AllowAny]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        user = _second_factor_user(request)
        if user is None:
            raise ValidationError(
                {"detail": "Logg inn med passordet først."}, code="no_pending_login"
            )
        if not two_factor.has_security_key(user):
            raise ValidationError(
                {"detail": "Brukeren har ingen sikkerhetsnøkkel."}, code="no_security_key"
            )
        options, state = _helper(request).authenticate_begin(
            user=user, require_user_verification=False
        )
        request.session[SECOND_FACTOR_STATE] = {**state, "user": user.pk}
        return Response(options)


class SecondFactorComplete(_View):
    """Finish the second step with the browser's answer.

    After a password, send the login (or reset) again: it passes without a
    code. A logged-in session may now change its methods.
    """

    permission_classes = [AllowAny]
    serializer_class = CredentialSerializer

    @extend_schema(responses={204: None})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        state = _pop_state(request, SECOND_FACTOR_STATE)
        user = _second_factor_user(request)
        if user is None or state.pop("user", None) != user.pk:
            raise ValidationError({"credential": MESSAGES["invalid_state"]}, code="invalid_state")

        device = _run(
            _helper(request).authenticate_complete,
            user=user,
            state=state,
            data=serializer.validated_data["credential"],
        )
        _usable(device)
        if request.user.is_authenticated:
            two_factor.mark_verified(request._request, device)
        else:
            two_factor.record_passed(request, user, device)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Passkey login -------------------------------------------------------


class PasskeysAvailableSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(read_only=True)


class PasskeysAvailable(generics.GenericAPIView):
    """Whether passkeys are switched on here, so the login page knows
    whether to offer them."""

    permission_classes = [AllowAny]
    serializer_class = PasskeysAvailableSerializer

    def get(self, request):
        return Response({"enabled": two_factor.passkeys_enabled()})


class PasskeyLoginBegin(_OptionsView):
    """Start logging in with a passkey: options for
    navigator.credentials.get(), with no account named."""

    permission_classes = [AllowAny]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        options, state = _helper(request).authenticate_begin(
            user=None, require_user_verification=True
        )
        request.session[PASSKEY_STATE] = state
        return Response(options)


class PasskeyLoginComplete(_View):
    """Log in with the browser's answer. Returns the profile, as the
    password login does."""

    permission_classes = [AllowAny]
    serializer_class = CredentialSerializer

    @extend_schema(responses=UserSerializer)
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        state = _pop_state(request, PASSKEY_STATE)
        # The options demanded user verification; the helper checks the
        # answer against the state, which says so.
        device = _run(
            _helper(request).authenticate_complete,
            user=None,
            state=state,
            data=serializer.validated_data["credential"],
        )
        _usable(device)
        user = device.user
        # django-otp's user_logged_in handler records it in the new session.
        user.otp_device = device
        login(request._request, user, backend="django.contrib.auth.backends.ModelBackend")
        return Response(UserSerializer(user).data)
