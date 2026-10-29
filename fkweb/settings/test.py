from .local import *

########## DATABASE
# Deliberately not overridden: tests run against the same PostgreSQL that
# DATABASE_URL points at, so they see the real backend's constraint checking,
# transaction semantics and type coercion rather than SQLite's looser ones.
# Django creates and drops a separate `test_`-prefixed database, so the
# development data in `fkweb` is never touched.

########## CACHE
# The site-wide page cache (Update/FetchFromCacheMiddleware) must not
# serve one test's response to another: the local-memory cache outlives
# the per-test database rollback, and anonymous pages that never touch
# request.user share a single cache key. DummyCache keeps the middleware
# in the request path but never stores anything.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
