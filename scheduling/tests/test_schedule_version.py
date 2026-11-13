"""The schedule version the frontend polls to know when to refetch."""

import datetime

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from fk.signals import schedule_version

pytestmark = pytest.mark.django_db

REAL_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "schedule-version-tests",
    }
}


@pytest.fixture(autouse=True)
def real_cache(settings):
    settings.CACHES = REAL_CACHE
    cache.clear()
    yield
    cache.clear()


def test_the_version_holds_while_nothing_changes():
    assert schedule_version() == schedule_version()


def test_a_new_grant_moves_the_version(make_grant, django_capture_on_commit_callbacks):
    before = schedule_version()
    with django_capture_on_commit_callbacks(execute=True):
        make_grant(timezone.now() + datetime.timedelta(days=10))
    assert schedule_version() != before


def test_deleting_a_grant_moves_the_version(make_grant, django_capture_on_commit_callbacks):
    grant = make_grant(timezone.now() + datetime.timedelta(days=10))
    before = schedule_version()
    with django_capture_on_commit_callbacks(execute=True):
        grant.delete()
    assert schedule_version() != before


def test_anyone_can_read_it():
    response = APIClient().get(reverse("api-scheduleitem-version"))
    assert response.status_code == 200
    assert response.json() == {"version": schedule_version()}
