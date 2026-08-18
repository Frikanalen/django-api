"""
Frontpage context selection and the CSRF bootstrap endpoint - the last
uncovered views in fkweb.
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from fk.models import Scheduleitem

pytestmark = pytest.mark.django_db

SCHEDULE_KEYS = ("curr_scheditem", "prev_scheditem", "next_scheditem")


def make_item(name: str, starttime) -> Scheduleitem:
    return Scheduleitem.objects.create(
        default_name=name,
        video=None,
        starttime=starttime,
        duration=timedelta(minutes=30),
        schedulereason=Scheduleitem.REASON_ADMIN,
    )


def get_frontpage():
    response = Client().get("/")
    assert response.status_code == 200
    return response


def test_frontpage_shows_previous_current_and_next_programs() -> None:
    now = timezone.now()
    prev = make_item("prev", now - timedelta(hours=2))
    current = make_item("current", now - timedelta(hours=1))
    upcoming = make_item("next", now + timedelta(hours=1))

    response = get_frontpage()

    assert response.context["curr_scheditem"] == current
    assert response.context["prev_scheditem"] == prev
    assert response.context["next_scheditem"] == upcoming


def test_frontpage_survives_an_empty_schedule() -> None:
    response = get_frontpage()

    for key in SCHEDULE_KEYS:
        assert key not in response.context


def test_frontpage_shows_nothing_with_only_one_past_program() -> None:
    """
    Pinned quirk: the view unpacks the two latest past items in one
    statement, so with a single past item the ValueError skips *all*
    schedule context - including the perfectly available current
    program.
    """
    make_item("only", timezone.now() - timedelta(hours=1))
    make_item("upcoming", timezone.now() + timedelta(hours=1))

    response = get_frontpage()

    for key in SCHEDULE_KEYS:
        assert key not in response.context


def test_frontpage_without_upcoming_programs_still_shows_the_current_one() -> None:
    """
    The IndexError for a missing *next* item is raised after
    curr/prev are placed in the context, so those survive.
    """
    now = timezone.now()
    prev = make_item("prev", now - timedelta(hours=2))
    current = make_item("current", now - timedelta(hours=1))

    response = get_frontpage()

    assert response.context["curr_scheditem"] == current
    assert response.context["prev_scheditem"] == prev
    assert "next_scheditem" not in response.context


def test_csrf_endpoint_mints_a_token_and_sets_the_cookie() -> None:
    client = Client()

    response = client.get("/api/csrf")

    assert response.status_code == 200
    assert response.json()["csrfToken"]
    assert "csrftoken" in response.cookies
