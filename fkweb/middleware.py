import datetime

from django.conf import settings
from django.middleware.cache import FetchFromCacheMiddleware
from django.utils import timezone


def api_utc_middleware(get_response):
    def middleware(request):
        is_api = request.path.startswith("/api/")
        with timezone.override(datetime.UTC if is_api else None):
            return get_response(request)

    return middleware


def _carries_credentials(request) -> bool:
    """Whether the request presents anything that could identify a user.

    Deliberately looks at the raw request rather than `request.user`. The
    page cache runs before DRF authenticates, so `request.user` is still
    anonymous for a token-authenticated call and would wave it straight
    through into the shared cache.
    """
    return bool(
        request.META.get("HTTP_AUTHORIZATION") or request.COOKIES.get(settings.SESSION_COOKIE_NAME)
    )


class AnonymousOnlyFetchFromCacheMiddleware(FetchFromCacheMiddleware):
    """FetchFromCacheMiddleware that ignores the cache for logged-in callers.

    The site-wide page cache keys on the URL plus the response's Vary
    headers, and nothing in either identifies the caller: a token lives in
    the Authorization header, which no response here varies on. Responses
    are user-dependent all the same -- VideoList filters through
    `Video.objects.visible_to(request.user)` -- so a shared entry would
    hand one user another's view of the catalogue, including videos that
    are meant to stay staff-only.

    Rather than teach every response to vary on its credentials, keep the
    cache for the anonymous public traffic it was meant for and let
    authenticated requests through untouched. Clearing the flag here also
    stops UpdateCacheMiddleware from *storing* the response, because that
    is what it keys its own decision off.
    """

    def process_request(self, request):
        if _carries_credentials(request):
            request._cache_update_cache = False
            return None
        return super().process_request(request)
