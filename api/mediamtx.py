"""MediaMTX HTTP authentication; intentionally independent of DRF sessions."""

import json
from uuid import UUID

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_POST

from fk.models import StreamCredential


@csrf_exempt
@require_POST
@never_cache
@sensitive_variables()
def authenticate(request):
    # MediaMTX supplies the client's credentials in JSON, not a Django
    # session or an Authorization header. A 401 also lets RTSP challenge
    # clients that initially connect without credentials.
    if request.content_type != "application/json":
        return HttpResponse(status=401)
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return HttpResponse(status=401)
    if not isinstance(payload, dict):
        return HttpResponse(status=401)

    user = payload.get("user")
    password = payload.get("password")
    action = payload.get("action")
    if (
        not isinstance(user, str)
        or not isinstance(password, str)
        or not password
        or len(password) > 256
        or action not in StreamCredential.Action.values
    ):
        return HttpResponse(status=401)
    try:
        key_id = UUID(user)
    except ValueError:
        return HttpResponse(status=401)

    credential = (
        StreamCredential.objects.select_related("source")
        .filter(key_id=key_id, action=action, revoked_at__isnull=True, source__enabled=True)
        .first()
    )
    if credential is None:
        return HttpResponse(status=401)
    if payload.get("path") != f"live/{credential.source.stream_id}":
        return HttpResponse(status=401)
    if credential.expires_at is not None and credential.expires_at <= timezone.now():
        return HttpResponse(status=401)
    if not credential.check_secret(password):
        return HttpResponse(status=401)

    # Use the upstream wire format, not the public API's camelCase renderer.
    if credential.expires_at is not None:
        return JsonResponse({"expireAt": credential.expires_at.isoformat()})
    return HttpResponse(status=204)
