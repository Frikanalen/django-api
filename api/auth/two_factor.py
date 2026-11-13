"""
Optional two-factor authentication with an authenticator app, on django-otp.

django-otp supplies the devices: a TOTPDevice holds the secret an
authenticator app shares (RFC 6238 codes, with exponential back-off after
failed attempts), and a StaticDevice holds single-use recovery codes for a
lost phone. This module is the API around them:

- setup/confirm: a new TOTPDevice starts unconfirmed; it only protects the
  account once the user has proved their app produces its codes, and only
  then are recovery codes issued.
- every way in asks for a code once the account has a confirmed device:
  the session login, API token issuance (fk-cli), the password reset
  confirmation (a mailbox alone must not get past the second factor) and
  the admin login. See `require_second_factor`.
- turning it off, or replacing the recovery codes, takes a current code.

Staff can remove a lost device in the admin (django-otp registers its
models there), which is the way back for someone who has lost both their
phone and their recovery codes.
"""

import base64
from io import BytesIO

import django_otp
from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.db import transaction
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.qr import write_qrcode_image
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status
from rest_framework.authtoken.serializers import AuthTokenSerializer
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

RECOVERY_CODE_COUNT = 10
RECOVERY_DEVICE_NAME = "Gjenopprettingskoder"
APP_DEVICE_NAME = "Autentiseringsapp"

CODE_REQUIRED = "Skriv inn koden fra autentiseringsappen."
CODE_WRONG = "Koden stemmer ikke. Prøv igjen med en ny kode."
CODE_THROTTLED = "For mange feil koder. Vent litt før du prøver igjen."


def has_two_factor(user) -> bool:
    return TOTPDevice.objects.filter(user=user, confirmed=True).exists()


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
    # A six-digit code is the app's; anything else can only be a recovery code.
    model = TOTPDevice if code.isdigit() and len(code) == 6 else StaticDevice
    with transaction.atomic():
        devices = list(model.objects.select_for_update().filter(user=user, confirmed=True))
        for device in devices:
            allowed, _ = device.verify_is_allowed()
            if not allowed:
                raise ValidationError({"otp": CODE_THROTTLED}, code="throttled")
            if device.verify_token(code):
                return device
    return None


def require_second_factor(user, code: str | None):
    """Pass if `user` has no second factor, or if `code` is right.

    Returns the device that verified, or None for an account without one.
    The error says which: `otp_required` when no code was given, so a
    client knows to ask for one, `invalid` when it was wrong.
    """
    if not has_two_factor(user):
        return None
    if not code:
        raise ValidationError({"otp": CODE_REQUIRED}, code="otp_required")
    device = verify_second_factor(user, code)
    if device is None:
        raise ValidationError({"otp": CODE_WRONG}, code="invalid")
    return device


def mark_verified(request, device) -> None:
    """Record in an already logged-in session that it passed the second factor.

    Before a login, set `user.otp_device` instead: django-otp's
    user_logged_in handler stores it in the new session.
    """
    if device is not None:
        django_otp.login(request, device)


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


# --- API ---------------------------------------------------------------


class TwoFactorStatusSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(read_only=True)
    recovery_codes_left = serializers.IntegerField(read_only=True)


class TwoFactorSetupSerializer(serializers.Serializer):
    secret = serializers.CharField(read_only=True, help_text="Base32, for typing into the app")
    otpauth_url = serializers.CharField(read_only=True)
    qr_code = serializers.CharField(read_only=True, help_text="The otpauth URL as an SVG data URI")


class CodeSerializer(serializers.Serializer):
    code = serializers.CharField(trim_whitespace=True)


class RecoveryCodesSerializer(serializers.Serializer):
    recovery_codes = serializers.ListField(child=serializers.CharField(), read_only=True)


class _TwoFactorView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_confirm"

    def _code(self, request) -> str:
        serializer = CodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["code"]


class TwoFactorStatus(_TwoFactorView):
    """Whether the account has an authenticator app, and recovery codes left."""

    serializer_class = TwoFactorStatusSerializer
    throttle_classes = []

    def get(self, request):
        user = request.user
        left = StaticToken.objects.filter(device__user=user, device__confirmed=True).count()
        return Response(
            {
                "enabled": has_two_factor(user),
                "recovery_codes_left": left if has_two_factor(user) else 0,
            }
        )


class TwoFactorSetup(_TwoFactorView):
    """Start adding an authenticator app: a new secret, as text and QR code.

    It protects nothing until confirmed with a code from the app. Starting
    over replaces an unconfirmed secret; an account that already has a
    confirmed app has to turn it off first.
    """

    serializer_class = TwoFactorSetupSerializer
    throttle_classes = []

    @extend_schema(request=None, responses=TwoFactorSetupSerializer)
    def post(self, request):
        user = request.user
        if has_two_factor(user):
            raise ValidationError(
                {"detail": "Totrinnsbekreftelse er allerede slått på."}, code="already_enabled"
            )
        TOTPDevice.objects.filter(user=user, confirmed=False).delete()
        device = TOTPDevice.objects.create(user=user, name=APP_DEVICE_NAME, confirmed=False)
        url = device.config_url
        return Response(
            {
                "secret": base64.b32encode(device.bin_key).decode(),
                "otpauth_url": url,
                "qr_code": _qr_data_uri(url),
            }
        )


class TwoFactorConfirm(_TwoFactorView):
    """Finish adding the app with a code from it. Returns the recovery codes.

    This is the only time the codes are returned; afterwards the API only
    says how many are left.
    """

    serializer_class = CodeSerializer

    @extend_schema(responses=RecoveryCodesSerializer)
    def post(self, request):
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
            codes = _issue_recovery_codes(user)
        mark_verified(request._request, device)
        return Response({"recovery_codes": codes}, status=status.HTTP_201_CREATED)


class TwoFactorDisable(_TwoFactorView):
    """Turn two-factor off with a current app code or a recovery code."""

    serializer_class = CodeSerializer

    @extend_schema(responses={204: None})
    def post(self, request):
        user = request.user
        if not has_two_factor(user):
            return Response(status=status.HTTP_204_NO_CONTENT)
        require_second_factor(user, self._code(request))
        TOTPDevice.objects.filter(user=user).delete()
        StaticDevice.objects.filter(user=user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TwoFactorRecoveryCodes(_TwoFactorView):
    """Replace the recovery codes, given a current code. Old ones stop working."""

    serializer_class = CodeSerializer

    @extend_schema(responses=RecoveryCodesSerializer)
    def post(self, request):
        user = request.user
        if not has_two_factor(user):
            raise ValidationError(
                {"detail": "Totrinnsbekreftelse er ikke slått på."}, code="not_enabled"
            )
        require_second_factor(user, self._code(request))
        return Response({"recovery_codes": _issue_recovery_codes(user)})


# --- The other ways in -------------------------------------------------


class AuthTokenWithSecondFactorSerializer(AuthTokenSerializer):
    """DRF's token login (username/password), plus `otp` for accounts with 2FA."""

    otp = serializers.CharField(required=False, allow_blank=True, write_only=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        require_second_factor(attrs["user"], attrs.get("otp"))
        return attrs


class AdminAuthenticationWithSecondFactorForm(AdminAuthenticationForm):
    """The admin login, asking for a code only from accounts that have 2FA."""

    otp_token = forms.CharField(
        label="Kode fra autentiseringsapp",
        required=False,
        help_text="Bare hvis totrinnsbekreftelse er slått på for kontoen.",
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}),
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
