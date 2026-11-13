"""
Password reset by email, built on django.contrib.auth.

Django already has the moving parts: PasswordResetForm finds the active
accounts behind an address and mails each a link carrying a uid and a
token; default_token_generator makes and checks those tokens without
storing anything (a token is an HMAC over the user's password hash and
last login, so it dies when either changes, or after
PASSWORD_RESET_TIMEOUT); SetPasswordForm runs AUTH_PASSWORD_VALIDATORS
and saves. What Django's own views add on top is HTML pages, and the
pages live in the frontend, so this module only puts two JSON endpoints
in front of the forms.

What it adds:

- The request endpoint answers the same whatever the address, and sends
  the mail off the request thread, so neither the answer nor its timing
  says whether an account exists.
- Rate limits: per client address on both endpoints (DRF throttle
  scopes, see REST_FRAMEWORK in settings), and at most one mail per
  address per RESET_MAIL_INTERVAL, so nobody can fill an inbox.
- After a reset the user is logged in, as Django's
  PasswordResetConfirmView does with post_reset_login, and gets a short
  notice that the password changed.
"""

import hashlib
import logging
import threading
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model, login, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.http import urlsafe_base64_decode
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from api.auth.serializers import UserSerializer
from api.auth.two_factor import require_second_factor

logger = logging.getLogger(__name__)

# One reset mail per address per this many seconds; further requests in
# the window get the same answer and send nothing.
RESET_MAIL_INTERVAL = 5 * 60

REQUEST_ACCEPTED = (
    "Hvis det finnes en bruker med denne e-postadressen, har vi sendt en lenke "
    "for å velge nytt passord. Lenken virker i én time."
)
INVALID_LINK = "Lenken er ugyldig eller utløpt. Be om en ny lenke for å tilbakestille passordet."


def run_in_background(target, *args, **kwargs) -> None:
    """Send mail without holding the request (and its timing) hostage.

    A module-level seam so tests can run it inline.
    """
    threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True).start()


class FrikanalenPasswordResetForm(PasswordResetForm):
    def send_mail(self, *args, **kwargs):
        # SMTP takes long enough to time, so the answer would otherwise
        # tell a registered address from an unknown one.
        run_in_background(self._send_mail_logged, *args, **kwargs)

    def _send_mail_logged(self, *args, **kwargs):
        try:
            super().send_mail(*args, **kwargs)
        except Exception:
            # Django's own form only logs too; the caller has long had
            # its answer.
            logger.exception("Failed to send password reset mail")


def _first_request_in_window(email: str) -> bool:
    # Hashed so the cache never holds addresses; lowercased because the
    # form matches addresses case-insensitively.
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return cache.add(f"password-reset-mail:{digest}", True, RESET_MAIL_INTERVAL)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    # For accounts with two-factor on: the mailbox alone must not get past
    # the second factor. Without it they answer 400 `otp_required` on `otp`.
    otp = serializers.CharField(required=False, allow_blank=True, write_only=True)


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField(read_only=True)


class PasswordResetRequest(generics.GenericAPIView):
    """Mail a password reset link to an address, if it has an account.

    The answer is the same whether or not it does.
    """

    serializer_class = PasswordResetRequestSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    @extend_schema(responses={202: DetailSerializer})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        if _first_request_in_window(email):
            form = FrikanalenPasswordResetForm({"email": email})
            if form.is_valid():
                form.save(
                    # The link points at the frontend, wherever this API
                    # is reached from; no django.contrib.sites here.
                    domain_override=urlsplit(settings.SITE_URL).netloc,
                    use_https=settings.SITE_URL.startswith("https://"),
                    subject_template_name="registration/password_reset_subject.txt",
                    email_template_name="registration/password_reset_email.txt",
                    extra_email_context={
                        "site_url": settings.SITE_URL,
                        "valid_minutes": settings.PASSWORD_RESET_TIMEOUT // 60,
                    },
                )

        return Response({"detail": REQUEST_ACCEPTED}, status=status.HTTP_202_ACCEPTED)


def _user_from_uid(uidb64: str):
    # As django.contrib.auth.views.PasswordResetConfirmView.get_user.
    User = get_user_model()
    try:
        pk = User._meta.pk.to_python(urlsafe_base64_decode(uidb64).decode())
        return User._default_manager.get(pk=pk)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist, DjangoValidationError):
        return None


def _send_password_changed_notice(user) -> None:
    context = {"user": user, "site_url": settings.SITE_URL}
    subject = "".join(
        render_to_string("registration/password_changed_subject.txt", context).splitlines()
    )
    body = render_to_string("registration/password_changed_email.txt", context)
    try:
        send_mail(subject, body, None, [user.email])
    except Exception:
        logger.exception("Failed to send password changed notice")


class PasswordResetConfirm(generics.GenericAPIView):
    """Set a new password from a reset link's uid and token.

    Logs the user in and returns their profile, as the login endpoint does.
    """

    serializer_class = PasswordResetConfirmSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_confirm"

    @extend_schema(responses=UserSerializer)
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = _user_from_uid(data["uid"])
        if (
            user is None
            or not user.is_active
            or not default_token_generator.check_token(user, data["token"])
        ):
            raise ValidationError({"token": INVALID_LINK}, code="invalid")

        # Checked before the password so a wrong code leaves it unchanged.
        device = require_second_factor(user, data.get("otp"), request)

        password = data["new_password"]
        # Django ships Norwegian for its password validators; the site is
        # Norwegian even though the API's own default language is not.
        with translation.override("nb"):
            form = SetPasswordForm(user, {"new_password1": password, "new_password2": password})
            if not form.is_valid():
                messages = [str(m) for errors in form.errors.values() for m in errors]
                raise ValidationError({"new_password": messages})
        form.save()

        # Saving rotated the session auth hash, which ends every other
        # session of this user; this one starts fresh.
        user.otp_device = device
        login(request._request, user)
        run_in_background(_send_password_changed_notice, user)
        return Response(UserSerializer(user).data)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)


class PasswordChange(generics.GenericAPIView):
    """Change your own password, given the current one.

    Django's PasswordChangeForm: the current password must be right and the
    new one must pass AUTH_PASSWORD_VALIDATORS. Other sessions end (the
    session auth hash rotates); this one stays signed in. Someone holding
    only a stolen session cannot use this to take the account over; they
    would have to know the password, or have the mailbox for a reset.
    """

    serializer_class = PasswordChangeSerializer
    permission_classes = [IsAuthenticated]
    # Shares the confirm scope: both guess at a secret.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_confirm"

    @extend_schema(responses={204: None})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = request.user

        with translation.override("nb"):
            form = PasswordChangeForm(
                user,
                {
                    "old_password": data["current_password"],
                    "new_password1": data["new_password"],
                    "new_password2": data["new_password"],
                },
            )
            if not form.is_valid():
                errors = {}
                if "old_password" in form.errors:
                    errors["current_password"] = [str(m) for m in form.errors["old_password"]]
                new = [
                    str(m)
                    for field, messages in form.errors.items()
                    if field != "old_password"
                    for m in messages
                ]
                if new:
                    errors["new_password"] = new
                raise ValidationError(errors)
        form.save()

        update_session_auth_hash(request._request, user)
        run_in_background(_send_password_changed_notice, user)
        return Response(status=status.HTTP_204_NO_CONTENT)
