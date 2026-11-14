"""/api/posters, posters in grant lists, and how they reach playout."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from api.grants.tests.test_grants_api import client_for, items_url
from fk.models import Organization, Scheduleitem, User, Video
from scheduling import services
from scheduling.models import AirtimeGrant, Poster

pytestmark = pytest.mark.django_db

LIST = reverse("api-poster-list")


def detail(poster: Poster) -> str:
    return reverse("api-poster-detail", args=[poster.pk])


@pytest.fixture
def poster(organization) -> Poster:
    return Poster.objects.create(
        organization=organization,
        heading="Neste: debatt",
        text="Klokka 20.",
        duration=timedelta(seconds=30),
    )


def test_a_member_makes_a_poster(member, organization) -> None:
    response = client_for(member).post(
        LIST,
        {
            "organizationId": organization.pk,
            "template": "notice",
            "heading": "Viktig melding",
            "text": "Møtet er flyttet.",
            "duration": "00:01:00",
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["organization"] == {"id": organization.pk, "name": "Grant org"}
    assert body["template"] == "notice"
    assert body["duration"] == "00:01:00"


def test_only_those_who_act_for_the_organization_make_its_posters(organization) -> None:
    stranger = User.objects.create(email="stranger@example.test", identity_confirmed=True)

    response = client_for(stranger).post(
        LIST, {"organizationId": organization.pk, "heading": "Hei"}, format="json"
    )

    assert response.status_code == 403


def test_a_poster_must_be_short_enough_to_read(member, organization) -> None:
    response = client_for(member).post(
        LIST,
        {"organizationId": organization.pk, "heading": "Hei", "duration": "00:20:00"},
        format="json",
    )

    assert response.status_code == 400


def test_members_see_their_own_posters_only(member, poster) -> None:
    Poster.objects.create(
        organization=Organization.objects.create(name="Other"), heading="Ikke din"
    )

    response = client_for(member).get(LIST)

    assert [p["id"] for p in response.json()["results"]] == [poster.pk]


def test_a_poster_is_listed_in_a_grant_like_a_video(member, open_grant, video, poster) -> None:
    response = client_for(member).put(
        items_url(open_grant),
        {"items": [{"video": video.pk}, {"poster": poster.pk}]},
        format="json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["items"][1]["video"] is None
    assert body["items"][1]["poster"]["heading"] == "Neste: debatt"
    assert body["itemsDuration"] == "00:20:30"


def test_an_item_is_a_video_or_a_poster(member, open_grant, video, poster) -> None:
    response = client_for(member).put(
        items_url(open_grant),
        {"items": [{"video": video.pk, "poster": poster.pk}]},
        format="json",
    )

    assert response.status_code == 400


def test_a_poster_airs_only_in_its_own_organizations_airtime(member, open_grant) -> None:
    theirs = Poster.objects.create(
        organization=Organization.objects.create(name="Other"), heading="X"
    )

    response = client_for(member).put(
        items_url(open_grant), {"items": [{"poster": theirs.pk}]}, format="json"
    )

    assert response.status_code == 400


def compiled(grant: AirtimeGrant, entries) -> AirtimeGrant:
    services._replace_items(grant, entries)
    grant.state = AirtimeGrant.State.LOCKED
    grant.save()
    services.compile_grant(grant)
    return grant


def test_playout_asks_for_posters_and_the_public_schedule_leaves_them_out(
    open_grant, video, poster
) -> None:
    compiled(open_grant, [video, poster])
    date = timezone.localtime(open_grant.starttime).date().isoformat()
    url = reverse("api-scheduleitem-list")

    public = client_for(None).get(url, {"date": date}).json()["results"]
    playout = client_for(None).get(url, {"date": date, "include_posters": "true"}).json()["results"]

    assert [row["video"]["id"] for row in public] == [video.pk]
    assert [row["poster"] and row["poster"]["heading"] for row in playout] == [
        None,
        "Neste: debatt",
    ]
    assert playout[1]["poster"]["template"] == "paper"
    assert playout[1]["poster"]["organization"]["name"] == "Grant org"


def test_correcting_a_posters_text_recompiles_what_lists_it(
    member, open_grant, video, poster
) -> None:
    compiled(open_grant, [video, poster])
    before = Scheduleitem.objects.get(poster=poster).pk

    response = client_for(member).patch(detail(poster), {"text": "Klokka 21."}, format="json")

    assert response.status_code == 200, response.content
    # A fresh row, so playout hears of the change and shows the new text.
    assert Scheduleitem.objects.get(poster=poster).pk != before


def test_a_posters_length_is_fixed_while_a_locked_list_holds_it(member, open_grant, poster) -> None:
    compiled(open_grant, [poster])

    response = client_for(member).patch(detail(poster), {"duration": "00:02:00"}, format="json")

    assert response.status_code == 400
    assert "locked" in str(response.json())


def test_a_longer_poster_must_still_fit_the_open_lists(member, open_grant, video, poster) -> None:
    long = Video.objects.create(
        name="Long",
        creator=member,
        organization=video.organization,
        duration=timedelta(minutes=34),
        proper_import=True,
    )
    services._replace_items(open_grant, [video, poster, long])

    # 0:20 + 30 s, then the next programme at 0:25, ending 0:59. A poster
    # of 5:30 ends at 0:26 and pushes it to 0:30, past the hour.
    response = client_for(member).patch(detail(poster), {"duration": "00:05:30"}, format="json")

    assert response.status_code == 400


def test_a_poster_coming_up_cannot_be_deleted(member, open_grant, poster) -> None:
    services._replace_items(open_grant, [poster])

    assert client_for(member).delete(detail(poster)).status_code == 400

    open_grant.items.all().delete()
    assert client_for(member).delete(detail(poster)).status_code == 204
    assert not Poster.objects.filter(pk=poster.pk).exists()


def test_a_poster_that_has_aired_can_be_deleted(member, organization, poster) -> None:
    start = timezone.now() - timedelta(days=2)
    past = AirtimeGrant.objects.create(
        organization=organization,
        starttime=start,
        endtime=start + timedelta(hours=1),
        locks_at=start,
    )
    compiled(past, [poster])

    assert client_for(member).delete(detail(poster)).status_code == 204
    assert Scheduleitem.objects.filter(grant=past, poster__isnull=True).count() == 1
