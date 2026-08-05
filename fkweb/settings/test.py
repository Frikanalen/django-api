from .local import *

########## IN-MEMORY TEST DATABASE
## TODO: Migrate to PostgreSQL
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}

########## CACHE
# The site-wide page cache (Update/FetchFromCacheMiddleware) must not
# serve one test's response to another: the local-memory cache outlives
# the per-test database rollback, and anonymous pages that never touch
# request.user share a single cache key. DummyCache keeps the middleware
# in the request path but never stores anything.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
