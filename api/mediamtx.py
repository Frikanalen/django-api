"""MediaMTX HTTP authentication; intentionally independent of DRF sessions."""

import json
from uuid import UUID

from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_POST

from fk.models import LiveSource, StreamCredential


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
        or action not in ("publish", "read")
    ):
        return HttpResponse(status=401)
    try:
        key_id = UUID(user)
    except ValueError:
        return HttpResponse(status=401)

    credential = (
        StreamCredential.objects.select_related("source")
        .filter(key_id=key_id, revoked_at__isnull=True)
        .filter(Q(source__enabled=True) | Q(action=StreamCredential.Action.PLAYOUT))
        .first()
    )
    if credential is None:
        return HttpResponse(status=401)
    path = payload.get("path")
    if credential.action == StreamCredential.Action.PLAYOUT:
        # Playout reads only the original contribution of an enabled source,
        # over RTSP from CasparCG: never previews, never publishing.
        allowed = (
            action == "read"
            and payload.get("protocol") == "rtsp"
            and _is_enabled_contribution(path)
        )
        return _grant(credential, password) if allowed else HttpResponse(status=401)
    live_path = credential.source.contribution_path
    preview_path = credential.source.preview_path
    if credential.action == StreamCredential.Action.PUBLISH:
        allowed = action == "publish" and path == live_path
    elif credential.action == StreamCredential.Action.READ:
        allowed = action == "read" and path in (live_path, preview_path)
    elif credential.action == StreamCredential.Action.CONVERT:
        # A worker may read only its original and publish only its preview.
        # It cannot replace the contribution or consume its own output.
        allowed = payload.get("protocol") == "rtsp" and (
            (action == "read" and path == live_path)
            or (action == "publish" and path == preview_path)
        )
    else:
        allowed = False
    if not allowed:
        return HttpResponse(status=401)
    return _grant(credential, password)


@sensitive_variables()
def _grant(credential, password):
    """Check expiry and the secret of a credential already allowed this request."""
    if credential.expires_at is not None and credential.expires_at <= timezone.now():
        return HttpResponse(status=401)
    if not credential.check_secret(password):
        return HttpResponse(status=401)

    # Use the upstream wire format, not the public API's camelCase renderer.
    if credential.expires_at is not None:
        return JsonResponse({"expireAt": credential.expires_at.isoformat()})
    return HttpResponse(status=204)


def _is_enabled_contribution(path):
    """Whether path is exactly the contribution path of an enabled source."""
    if not isinstance(path, str):
        return False
    try:
        stream_id = UUID(path.rsplit("/", 1)[-1])
    except ValueError:
        return False
    source = LiveSource.objects.filter(stream_id=stream_id, enabled=True).first()
    # Compare the whole path, so neither the preview nor a variant spelling
    # of the UUID passes for the contribution.
    return source is not None and source.contribution_path == path
