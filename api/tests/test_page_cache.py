"""
What the site-wide page cache is allowed to serve.

The suite runs on DummyCache so that one test's response can never be
handed to another, which also means nothing here would exercise the cache
at all. These tests opt back in to a real (local-memory) cache so the
Update/FetchFromCache pair actually stores and looks things up.

Two properties matter, and they pull against each other:

  * anonymous responses must be served from the cache, or the middleware
    is dead weight -- which it silently was, because the timezone
    override sat between the two cache middlewares and made them write
    and read different keys;
  * authenticated responses must never enter it, because the cache key
    knows nothing about a token or a session, while the responses very
    much depend on who asked.
"""

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from fk.models import Organization, User, Video

pytestmark = pytest.mark.django_db

REAL_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "page-cache-tests",
    }
}


@pytest.fixture(autouse=True)
def page_cache(settings):
    """Swap DummyCache for a real one, and keep it to a single test.

    The local-memory cache outlives the per-test database rollback, so it
    has to be emptied on the way in as well as on the way out.
    """
    settings.CACHES = REAL_CACHE
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def catalogue(page_cache):
    """One editor, one organization, one published video."""
    editor = User.objects.create(email="editor@fake.com")
    other = User.objects.create(email="other@fake.com")
    organization = Organization.objects.create(name="Cache org", editor=editor)
    Video.objects.create(
        name="first video",
        organization=organization,
        creator=editor,
        publish_on_web=True,
        proper_import=True,
    )
    return {"editor": editor, "other": other, "organization": organization}


def add_video(catalogue, name: str) -> None:
    Video.objects.create(
        name=name,
        organization=catalogue["organization"],
        creator=catalogue["editor"],
        publish_on_web=True,
        proper_import=True,
    )


def names(response) -> list[str]:
    return [video["name"] for video in response.json()["results"]]


def authorized(user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.get_or_create(user=user)[0].key}")
    return client


def cached_pages() -> list[str]:
    """Keys holding a stored page, ignoring the Vary bookkeeping entry."""
    return [key for key in getattr(cache, "_cache", {}) if "cache_page" in key]


def test_anonymous_responses_are_served_from_the_cache(catalogue) -> None:
    """The regression: a second anonymous request must not reach the database.

    A video created between the two requests is the tell -- if it shows up
    in the second response, the lookup missed and the cache is useless.
    """
    url = reverse("api-video-list")
    client = APIClient()

    first = names(client.get(url))
    add_video(catalogue, "second video")
    second = names(client.get(url))

    assert first == ["first video"]
    assert second == first


def test_a_token_request_is_neither_served_nor_stored(catalogue) -> None:
    url = reverse("api-video-list")

    authorized(catalogue["editor"]).get(url)
    assert not cached_pages(), "an authenticated response was written to the shared cache"

    # A response cached for anonymous callers must not be replayed to a
    # logged-in one either: they are entitled to see more, not less.
    APIClient().get(url)
    add_video(catalogue, "second video")

    assert "second video" in names(authorized(catalogue["editor"]).get(url))


def test_two_token_users_never_share_a_response(catalogue) -> None:
    """The cache key covers neither token, so identity must not come from it."""
    url = reverse("api-user-detail")

    mine = authorized(catalogue["editor"]).get(url)
    theirs = authorized(catalogue["other"]).get(url)

    assert mine.json()["email"] == "editor@fake.com"
    assert theirs.json()["email"] == "other@fake.com"


def test_a_staff_response_is_never_replayed_to_the_public(catalogue) -> None:
    """The leak with teeth.

    A video whose organization has no active editor is staff-only, so the
    catalogue genuinely differs by caller. Cache a staff view of it and
    the next anonymous visitor would be handed videos nobody may answer
    for -- exactly the kind of thing `visible_to` exists to withhold.
    """
    orphaned = Organization.objects.create(
        name="No responsible editor",
        editor=User.objects.create(email="gone@fake.com", is_active=False),
    )
    Video.objects.create(
        name="staff only video",
        organization=orphaned,
        creator=catalogue["editor"],
        publish_on_web=True,
        proper_import=True,
    )
    # is_staff is a read-only property over is_superuser on this model.
    staff = User.objects.create(email="staff@fake.com", is_superuser=True)
    url = reverse("api-video-list")

    assert "staff only video" in names(authorized(staff).get(url))

    assert "staff only video" not in names(APIClient().get(url))


def test_a_session_request_is_neither_served_nor_stored(catalogue) -> None:
    url = reverse("api-video-list")
    client = APIClient()
    client.force_login(catalogue["editor"])

    client.get(url)
    assert not cached_pages(), "a session response was written to the shared cache"

    add_video(catalogue, "second video")

    assert "second video" in names(client.get(url))


def test_the_cache_key_survives_the_api_timezone_override(catalogue, settings) -> None:
    """Guards the middleware ordering itself.

    `api_utc_middleware` swaps the active timezone for /api/ paths, and
    Django folds the active timezone into the cache key. If it is ever
    moved back between the two cache middlewares, the write and the read
    land on different keys and nothing is served -- so assert the stored
    key carries the timezone the fetch side will look under.
    """
    APIClient().get(reverse("api-video-list"))

    pages = cached_pages()
    assert len(pages) == 1, pages
    assert pages[0].endswith(f".{settings.TIME_ZONE}")
