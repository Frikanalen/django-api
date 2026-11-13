"""
Optional two-factor authentication, on django-otp.

django-otp supplies the devices, and an account can have any number of
them:

- TOTPDevice: an authenticator app (RFC 6238 codes, with exponential
  back-off after failed attempts).
- WebAuthnCredential (django-otp-webauthn, see api/auth/webauthn.py): a
  passkey or a security key. A passkey can also log in on its own, without
  the password (see webauthn.PasskeyLogin*).
- StaticDevice: single-use recovery codes for a lost phone or key, issued
  when the first app or key is added.

The account has two-factor on while it has at least one confirmed app or
key. Then every way in asks for the second step: the session login, API
token issuance (fk-cli, codes only), the password reset confirmation (a
mailbox alone must not get past the second factor) and the admin login
(codes only). See `require_second_factor`.

Changing the methods (adding, renaming, removing, new recovery codes)
takes a session that has passed the second step, so a stolen session
cookie, or one that was logged in before two-factor was turned on, cannot
remove the protection. Such a session can pass the step later with
`TwoFactorVerify` or a security key (webauthn.SecondFactor*).

Staff can remove a lost device in the admin (django-otp registers its
models there), which is the way back for someone who has lost every method
and their recovery codes.
"""

import base64
import time
from io import BytesIO
from urllib.parse import quote

import django_otp
from django import forms
from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.db import transaction
from django.shortcuts import get_object_or_404
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.models import Device
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.qr import write_qrcode_image
from django_otp_webauthn.models import WebAuthnCredential
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status
from rest_framework.authtoken.serializers import AuthTokenSerializer
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

RECOVERY_CODE_COUNT = 10
# Served by the frontend (public/authenticator-icon.png): the three blades.
AUTHENTICATOR_ICON_URL = f"{settings.SITE_URL}/authenticator-icon.png"
RECOVERY_DEVICE_NAME = "Gjenopprettingskoder"
APP_DEVICE_NAME = "Autentiseringsapp"

CODE_REQUIRED = "Skriv inn koden fra autentiseringsappen."
CODE_WRONG = "Koden stemmer ikke. Prøv igjen med en ny kode."
CODE_THROTTLED = "For mange feil koder. Vent litt før du prøver igjen."
VERIFY_FIRST = "Bekreft med en kode eller sikkerhetsnøkkel først."

# How long a password that was right waits for its second step (a security
# key) in the session, and how long a passed step waits to be used.
PENDING_SECONDS = 5 * 60
PENDING_USER_KEY = "two_factor_pending_user"
PASSED_KEY = "two_factor_passed"

# The kinds of method, as the API names them.
KINDS = {"totp": TOTPDevice, "webauthn": WebAuthnCredential}


def passkeys_enabled() -> bool:
    """Whether passkeys and security keys are switched on here
    (PASSKEYS_ENABLED). Off, keys can be neither added nor used, and any
    already registered do not count as a second factor."""
    return settings.PASSKEYS_ENABLED


def second_factor_devices(user) -> list[Device]:
    """The user's confirmed apps and keys (not the recovery codes)."""
    devices: list[Device] = list(
        TOTPDevice.objects.filter(user=user, confirmed=True).order_by("id")
    )
    if passkeys_enabled():
        devices += WebAuthnCredential.objects.filter(user=user, confirmed=True).order_by("id")
    return devices


def has_two_factor(user) -> bool:
    return TOTPDevice.objects.filter(user=user, confirmed=True).exists() or has_security_key(user)


def has_security_key(user) -> bool:
    return (
        passkeys_enabled() and WebAuthnCredential.objects.filter(user=user, confirmed=True).exists()
    )


def _normalise(code: str) -> str:
    # Apps show "123 456"; recovery codes are printed lower case.
    return "".join(code.split()).lower()


def verify_second_factor(user, code: str):
    """The device that accepts `code` (app code or recovery code), or None.

    Raises a ValidationError on `otp` when the devices are in back-off, so
    the person is told to wait instead of being told the code was wrong.
    """
    code = _normalise(code)
    if not code:
        return None
    # A six-digit code is an app's; anything else can only be a recovery code.
    model = TOTPDevice if code.isdigit() and len(code) == 6 else StaticDevice
    with transaction.atomic():
        devices = list(model.objects.select_for_update().filter(user=user, confirmed=True))
        for device in devices:
            allowed, _ = device.verify_is_allowed()
            if not allowed:
                raise ValidationError({"otp": CODE_THROTTLED}, code="throttled")
        # Each device counts a code it refuses as a failed attempt. With
        # two apps, the right code for one is refused by the other first,
        # which is not an attempt at that one; undo those counts when some
        # device accepts.
        before = [
            (device, device.throttling_failure_count, device.throttling_failure_timestamp)
            for device in devices
        ]
        for device in devices:
            if device.verify_token(code):
                for other, count, timestamp in before:
                    if other is not device and other.throttling_failure_count != count:
                        other.throttling_failure_count = count
                        other.throttling_failure_timestamp = timestamp
                        other.save(
                            update_fields=[
                                "throttling_failure_count",
                                "throttling_failure_timestamp",
                            ]
                        )
                return device
    return None


def _fresh(entry, user) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("user") == user.pk
        and time.time() - entry.get("at", 0) < PENDING_SECONDS
    )


def require_second_factor(user, code: str | None, request=None):
    """Pass if `user` has no second factor, or if the second step is done.

    The step is done with `code` (an app code or a recovery code), or, given
    the `request`, by a security key used in this session since the
    password was found right (see webauthn.SecondFactorComplete).

    Returns the device that verified, or None for an account without one.
    The error says which: `otp_required` when the step is missing, so a
    client knows to ask for it, `invalid` when the code was wrong. With a
    `request`, `otp_required` also lets this session use a security key for
    the next few minutes.
    """
    if not has_two_factor(user):
        return None

    session = getattr(request, "session", None)
    if session is not None:
        passed = session.pop(PASSED_KEY, None)
        if _fresh(passed, user):
            device = Device.from_persistent_id(passed["device"])
            if device is not None and device.user_id == user.pk and device.confirmed:
                session.pop(PENDING_USER_KEY, None)
                return device

    if not code:
        if session is not None:
            session[PENDING_USER_KEY] = {"user": user.pk, "at": time.time()}
        raise ValidationError({"otp": CODE_REQUIRED}, code="otp_required")
    device = verify_second_factor(user, code)
    if device is None:
        raise ValidationError({"otp": CODE_WRONG}, code="invalid")
    if session is not None:
        session.pop(PENDING_USER_KEY, None)
    return device


def pending_user(request):
    """The user whose password was right in this session and who still owes
    the second step, or None."""
    from django.contrib.auth import get_user_model

    entry = request.session.get(PENDING_USER_KEY)
    if not isinstance(entry, dict) or time.time() - entry.get("at", 0) >= PENDING_SECONDS:
        return None
    return get_user_model()._default_manager.filter(pk=entry.get("user"), is_active=True).first()


def record_passed(request, user, device) -> None:
    """Let the next `require_second_factor` for `user` in this session pass."""
    request.session[PASSED_KEY] = {
        "user": user.pk,
        "device": device.persistent_id,
        "at": time.time(),
    }


def mark_verified(request, device) -> None:
    """Record in an already logged-in session that it passed the second factor.

    Before a login, set `user.otp_device` instead: django-otp's
    user_logged_in handler stores it in the new session.
    """
    if device is not None:
        django_otp.login(request, device)


def session_is_verified(request) -> bool:
    """Whether this session passed the second step with one of the user's
    devices that still exists."""
    session = getattr(request, "session", None)
    persistent_id = session.get(DEVICE_ID_SESSION_KEY) if session is not None else None
    if not persistent_id:
        return False
    device = Device.from_persistent_id(persistent_id)
    return device is not None and device.user_id == request.user.pk


def require_verified_session(request) -> None:
    """Changing the methods of an account with two-factor on takes a session
    that has passed the second step."""
    if has_two_factor(request.user) and not session_is_verified(request):
        raise PermissionDenied(VERIFY_FIRST, code="verify_required")


def _qr_data_uri(text: str) -> str:
    out = BytesIO()
    write_qrcode_image(text, out)
    return "data:image/svg+xml;base64," + base64.b64encode(out.getvalue()).decode()


def _issue_recovery_codes(user) -> list[str]:
    device, _ = StaticDevice.objects.get_or_create(
        user=user, name=RECOVERY_DEVICE_NAME, defaults={"confirmed": True}
    )
    device.token_set.all().delete()
    codes = [StaticToken.random_token() for _ in range(RECOVERY_CODE_COUNT)]
    StaticToken.objects.bulk_create(StaticToken(device=device, token=code) for code in codes)
    return codes


def first_method_added(user) -> list[str] | None:
    """Recovery codes when `user` has just got their first app or key.

    Call after confirming the new device. Codes that already exist are kept:
    a second method is no reason to replace them.
    """
    if len(second_factor_devices(user)) != 1:
        return None
    if StaticToken.objects.filter(device__user=user, device__confirmed=True).exists():
        return None
    return _issue_recovery_codes(user)


def method_id(device: Device) -> str:
    kind = "webauthn" if isinstance(device, WebAuthnCredential) else "totp"
    return f"{kind}-{device.pk}"


def device_for_method(user, method: str) -> Device:
    kind, _, pk = method.partition("-")
    model = KINDS.get(kind)
    if model is WebAuthnCredential and not passkeys_enabled():
        model = None
    if model is None or not pk.isdigit():
        raise ValidationError({"id": "Ukjent metode."}, code="invalid")
    return get_object_or_404(model, user=user, pk=int(pk), confirmed=True)


# --- API ---------------------------------------------------------------


class MethodSerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True, help_text="`totp-<n>` or `webauthn-<n>`")
    kind = serializers.ChoiceField(choices=["totp", "webauthn"], read_only=True)
    name = serializers.CharField(read_only=True)
    passkey = serializers.BooleanField(
        read_only=True,
        allow_null=True,
        help_text="A key that can log in on its own. Null when the browser did not say.",
    )
    created_at = serializers.DateTimeField(read_only=True, allow_null=True)
    last_used_at = serializers.DateTimeField(read_only=True, allow_null=True)


def _method(device: Device) -> dict:
    webauthn = isinstance(device, WebAuthnCredential)
    return {
        "id": method_id(device),
        "kind": "webauthn" if webauthn else "totp",
        "name": device.name,
        "passkey": device.discoverable if webauthn else None,
        "created_at": device.created_at,
        "last_used_at": device.last_used_at,
    }


class TwoFactorStatusSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(read_only=True)
    recovery_codes_left = serializers.IntegerField(read_only=True)
    verified = serializers.BooleanField(
        read_only=True,
        help_text="This session has passed the second step, so it may change the methods.",
    )
    methods = MethodSerializer(many=True, read_only=True)
    passkeys = serializers.BooleanField(
        read_only=True, help_text="Passkeys and security keys can be added here."
    )


class AppNameSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64, required=False, allow_blank=True)


class TwoFactorSetupSerializer(serializers.Serializer):
    secret = serializers.CharField(read_only=True, help_text="Base32, for typing into the app")
    otpauth_url = serializers.CharField(read_only=True)
    qr_code = serializers.CharField(read_only=True, help_text="The otpauth URL as an SVG data URI")


class CodeSerializer(serializers.Serializer):
    code = serializers.CharField(trim_whitespace=True)


class RecoveryCodesSerializer(serializers.Serializer):
    recovery_codes = serializers.ListField(child=serializers.CharField(), read_only=True)


class MethodAddedSerializer(serializers.Serializer):
    method = MethodSerializer(read_only=True)
    recovery_codes = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
        allow_null=True,
        help_text="Only with the account's first method; shown once.",
    )


class RenameSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64)


class _TwoFactorView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "two_factor"

    def _code(self, request) -> str:
        serializer = CodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["code"]


class TwoFactorStatus(_TwoFactorView):
    """The account's apps and keys, recovery codes left, and whether this
    session may change them."""

    serializer_class = TwoFactorStatusSerializer
    throttle_classes = []

    def get(self, request):
        user = request.user
        devices = second_factor_devices(user)
        left = StaticToken.objects.filter(device__user=user, device__confirmed=True).count()
        return Response(
            {
                "enabled": bool(devices),
                "recovery_codes_left": left if devices else 0,
                "verified": session_is_verified(request),
                "methods": [_method(d) for d in devices],
                "passkeys": passkeys_enabled(),
            }
        )


class TwoFactorVerify(_TwoFactorView):
    """Pass the second step in this session with an app or recovery code,
    so it may change the methods."""

    serializer_class = CodeSerializer

    @extend_schema(responses={204: None})
    def post(self, request):
        code = self._code(request)
        if not has_two_factor(request.user):
            raise ValidationError(
                {"detail": "Totrinnsbekreftelse er ikke slått på."}, code="not_enabled"
            )
        try:
            device = verify_second_factor(request.user, code)
        except ValidationError:
            raise ValidationError({"code": CODE_THROTTLED}, code="throttled") from None
        if device is None:
            raise ValidationError({"code": CODE_WRONG}, code="invalid")
        mark_verified(request._request, device)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TotpSetup(_TwoFactorView):
    """Start adding an authenticator app: a new secret, as text and QR code.

    It protects nothing until confirmed with a code from the app. Starting
    over replaces an unconfirmed secret.
    """

    serializer_class = AppNameSerializer

    @extend_schema(request=AppNameSerializer, responses=TwoFactorSetupSerializer)
    def post(self, request):
        require_verified_session(request)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data.get("name", "").strip() or APP_DEVICE_NAME
        user = request.user
        TOTPDevice.objects.filter(user=user, confirmed=False).delete()
        device = TOTPDevice.objects.create(user=user, name=name, confirmed=False)
        # Apps that show an icon per account (FreeOTP, 2FAS, Aegis, Ente
        # Auth, ...) take it from `image`; the others ignore it.
        url = f"{device.config_url}&image={quote(AUTHENTICATOR_ICON_URL, safe='')}"
        return Response(
            {
                "secret": base64.b32encode(device.bin_key).decode(),
                "otpauth_url": url,
                "qr_code": _qr_data_uri(url),
            }
        )


class TotpConfirm(_TwoFactorView):
    """Finish adding the app with a code from it.

    With the account's first method, this also returns the recovery codes,
    the only time they are returned.
    """

    serializer_class = CodeSerializer

    @extend_schema(responses={201: MethodAddedSerializer})
    def post(self, request):
        require_verified_session(request)
        user = request.user
        code = _normalise(self._code(request))
        device = TOTPDevice.objects.filter(user=user, confirmed=False).last()
        if device is None:
            raise ValidationError({"code": "Start oppsettet på nytt."}, code="no_pending_setup")
        if not device.verify_token(code):
            raise ValidationError({"code": CODE_WRONG}, code="invalid")
        with transaction.atomic():
            device.confirmed = True
            device.save(update_fields=["confirmed"])
            codes = first_method_added(user)
        if not session_is_verified(request):
            mark_verified(request._request, device)
        return Response(
            MethodAddedSerializer({"method": _method(device), "recovery_codes": codes}).data,
            status=status.HTTP_201_CREATED,
        )


class TwoFactorMethod(_TwoFactorView):
    """Rename or remove one app or key.

    Removing the last one turns two-factor off and deletes the recovery
    codes.
    """

    serializer_class = RenameSerializer
    http_method_names = ["patch", "delete"]

    @extend_schema(responses=MethodSerializer)
    def patch(self, request, method):
        require_verified_session(request)
        device = device_for_method(request.user, method)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device.name = serializer.validated_data["name"].strip()
        device.save(update_fields=["name"])
        return Response(_method(device))

    @extend_schema(responses={204: None})
    def delete(self, request, method):
        require_verified_session(request)
        user = request.user
        device = device_for_method(user, method)
        with transaction.atomic():
            device.delete()
            if not has_two_factor(user):
                StaticDevice.objects.filter(user=user).delete()
                TOTPDevice.objects.filter(user=user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TwoFactorRecoveryCodes(_TwoFactorView):
    """Replace the recovery codes. Old ones stop working."""

    serializer_class = RecoveryCodesSerializer

    @extend_schema(request=None, responses=RecoveryCodesSerializer)
    def post(self, request):
        user = request.user
        if not has_two_factor(user):
            raise ValidationError(
                {"detail": "Totrinnsbekreftelse er ikke slått på."}, code="not_enabled"
            )
        require_verified_session(request)
        return Response({"recovery_codes": _issue_recovery_codes(user)})


# --- The other ways in -------------------------------------------------


class AuthTokenWithSecondFactorSerializer(AuthTokenSerializer):
    """DRF's token login (username/password), plus `otp` for accounts with 2FA.

    Codes only: an account with nothing but security keys uses a recovery
    code here.
    """

    otp = serializers.CharField(required=False, allow_blank=True, write_only=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        require_second_factor(attrs["user"], attrs.get("otp"))
        return attrs


class AdminAuthenticationWithSecondFactorForm(AdminAuthenticationForm):
    """The admin login, asking for a code only from accounts that have 2FA."""

    otp_token = forms.CharField(
        label="Kode fra autentiseringsapp eller gjenopprettingskode",
        required=False,
        help_text="Bare hvis totrinnsbekreftelse er slått på for kontoen.",
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code"}),
    )

    def clean(self):
        cleaned = super().clean()
        user = self.get_user()
        if user is not None and has_two_factor(user):
            try:
                device = require_second_factor(user, cleaned.get("otp_token"))
            except ValidationError as error:
                raise forms.ValidationError(str(error.detail["otp"][0])) from None
            user.otp_device = device
        return cleaned
