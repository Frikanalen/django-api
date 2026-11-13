"""The scheduling admin pages render, and edits go through the services."""

from datetime import timedelta

import pytest
from django.urls import reverse

from fk.models import Scheduleitem, User
from scheduling.models import AirtimeGrant

from .conftest import NOW

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client):
    staff = User.objects.create(email="admin@example.test", is_superuser=True)
    client.force_login(staff)
    return client


@pytest.mark.parametrize("model", ["airtimegrant", "recurringslot"])
def test_changelist_and_add_pages_render(admin_client, model) -> None:
    assert admin_client.get(reverse(f"admin:scheduling_{model}_changelist")).status_code == 200
    assert admin_client.get(reverse(f"admin:scheduling_{model}_add")).status_code == 200


def test_editing_a_compiled_grant_in_the_admin_recompiles_it(
    admin_client, make_grant, make_video
) -> None:
    start = NOW + timedelta(days=10)
    grant = make_grant(start, state=AirtimeGrant.State.COMPILED)
    video = make_video(20)
    url = reverse("admin:scheduling_airtimegrant_change", args=[grant.pk])

    response = admin_client.post(
        url,
        {
            "locks_at_0": grant.locks_at.date().isoformat(),
            "locks_at_1": grant.locks_at.time().isoformat(),
            "recipe": "",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-position": "0",
            "items-0-video": str(video.pk),
        },
    )

    assert response.status_code == 302, response.content.decode()[:2000]
    assert list(Scheduleitem.objects.values_list("video_id", "starttime")) == [(video.pk, start)]


def test_the_admin_refuses_items_longer_than_the_window(
    admin_client, make_grant, make_video
) -> None:
    grant = make_grant(NOW + timedelta(days=10), minutes=30)
    video = make_video(40)

    response = admin_client.post(
        reverse("admin:scheduling_airtimegrant_change", args=[grant.pk]),
        {
            "locks_at_0": grant.locks_at.date().isoformat(),
            "locks_at_1": grant.locks_at.time().isoformat(),
            "recipe": "",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-position": "0",
            "items-0-video": str(video.pk),
        },
    )

    assert response.status_code == 200
    assert b"longer than the window" in response.content
    assert not grant.items.exists()
