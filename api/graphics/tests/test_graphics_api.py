"""Staff graphics (HTML pages such as the infographics) in grant lists,
and how they reach playout."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from api.grants.tests.test_grants_api import client_for, items_url
from scheduling import layout, services
from scheduling.models import AirtimeGrant, Graphic, Showing

pytestmark = pytest.mark.django_db


@pytest.fixture
def graphic() -> Graphic:
    return Graphic.objects.get(name="Infografikk")


def compiled(grant: AirtimeGrant, entries) -> AirtimeGrant:
    services._replace_items(grant, entries)
    grant.state = AirtimeGrant.State.LOCKED
    grant.save()
    services.compile_grant(grant)
    return grant


def test_the_infographics_come_with_the_migration(graphic) -> None:
    assert graphic.url == "/infographics/"


def test_only_staff_list_graphics(member, staff, graphic) -> None:
    url = reverse("api-graphic-list")

    assert client_for(member).get(url).status_code == 403
    response = client_for(staff).get(url)

    assert response.status_code == 200
    assert [g["name"] for g in response.json()] == ["Infografikk"]


def test_staff_put_a_graphic_in_a_list_for_as_long_as_they_like(
    staff, open_grant, video, graphic
) -> None:
    response = client_for(staff).put(
        items_url(open_grant),
        {"items": [{"video": video.pk}, {"graphic": graphic.pk, "duration": "00:25:00"}]},
        format="json",
    )

    assert response.status_code == 200, response.content
    item = response.json()["items"][1]
    assert item["video"] is None
    assert item["graphic"]["name"] == "Infografikk"
    assert item["duration"] == "00:25:00"
    # Not a programme: it follows the video straight away, with no gap.
    assert response.json()["itemsDuration"] == "00:45:00"


def test_a_graphic_needs_its_duration(staff, open_grant, graphic) -> None:
    response = client_for(staff).put(
        items_url(open_grant), {"items": [{"graphic": graphic.pk}]}, format="json"
    )

    assert response.status_code == 400


def test_a_graphic_must_fit_the_window(staff, open_grant, graphic) -> None:
    response = client_for(staff).put(
        items_url(open_grant),
        {"items": [{"graphic": graphic.pk, "duration": "01:00:01"}]},
        format="json",
    )

    assert response.status_code == 400


def test_members_cannot_add_graphics(member, open_grant, graphic) -> None:
    response = client_for(member).put(
        items_url(open_grant),
        {"items": [{"graphic": graphic.pk, "duration": "00:10:00"}]},
        format="json",
    )

    assert response.status_code == 403
    assert not open_grant.items.exists()


def test_members_keep_the_graphics_staff_put_in_their_list(
    member, open_grant, video, graphic
) -> None:
    services._replace_items(open_grant, [Showing(graphic, timedelta(minutes=10))])

    response = client_for(member).put(
        items_url(open_grant),
        {"items": [{"graphic": graphic.pk, "duration": "00:10:00"}, {"video": video.pk}]},
        format="json",
    )

    assert response.status_code == 200, response.content


def test_a_programme_after_a_graphic_still_starts_on_the_grid(graphic, video) -> None:
    start = timezone.now().replace(minute=0, second=0, microsecond=0)
    placed = layout.lay_out(
        start, [Showing(graphic, timedelta(minutes=2, seconds=10)), video], padded=True
    )

    assert placed[1].start == start + timedelta(minutes=5)


def test_playout_gets_graphics_and_the_public_schedule_leaves_them_out(
    open_grant, video, graphic
) -> None:
    compiled(open_grant, [video, Showing(graphic, timedelta(minutes=30))])
    date = timezone.localtime(open_grant.starttime).date().isoformat()
    url = reverse("api-scheduleitem-list")

    public = client_for(None).get(url, {"date": date}).json()["results"]
    playout = client_for(None).get(url, {"date": date, "include_fillers": "true"}).json()["results"]

    assert [row["video"]["id"] for row in public] == [video.pk]
    assert playout[1]["graphic"] == {
        "id": graphic.pk,
        "name": "Infografikk",
        "url": "/infographics/",
    }
    assert playout[1]["duration"] == "00:30:00"
    assert playout[1]["starttime"] == playout[0]["endtime"]
