"""/api/grants: members fill their grants; staff create, cancel and exempt."""

import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from fk.models import Organization, Series, User, Video
from programming.services import series_recipe, uploads_recipe
from scheduling import broadcast_time
from scheduling.models import AirtimeGrant, GrantItem, RecurringSlot

pytestmark = pytest.mark.django_db

OSLO = ZoneInfo("Europe/Oslo")


def in_days(days: float) -> datetime.datetime:
    moment = timezone.now() + timedelta(days=days)
    return moment.replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def member() -> User:
    return User.objects.create(email="grant-member@example.test", identity_confirmed=True)


@pytest.fixture
def organization(member: User) -> Organization:
    organization = Organization.objects.create(name="Grant org", editor=member, fkmember=True)
    organization.members.add(member)
    return organization


@pytest.fixture
def staff() -> User:
    return User.objects.create(email="grant-staff@example.test", is_superuser=True)


def client_for(user: User | None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


@pytest.fixture
def video(member: User, organization: Organization) -> Video:
    return Video.objects.create(
        name="Programme",
        creator=member,
        organization=organization,
        duration=timedelta(minutes=20),
        proper_import=True,
    )


@pytest.fixture
def open_grant(organization: Organization) -> AirtimeGrant:
    start = in_days(10)
    return AirtimeGrant.objects.create(
        organization=organization,
        starttime=start,
        endtime=start + timedelta(hours=1),
        locks_at=timezone.now() + timedelta(days=3),
    )


def items_url(grant: AirtimeGrant) -> str:
    return reverse("api-grant-items", args=[grant.pk])


def test_members_see_their_organizations_grants_only(member, open_grant) -> None:
    other = Organization.objects.create(name="Other")
    start = in_days(20)
    AirtimeGrant.objects.create(
        organization=other, starttime=start, endtime=start + timedelta(hours=1), locks_at=start
    )

    response = client_for(member).get(reverse("api-grant-list"))

    assert response.status_code == 200
    [grant] = response.json()["results"]
    assert grant["id"] == open_grant.pk
    assert grant["organization"] == {"id": open_grant.organization_id, "name": "Grant org"}
    assert grant["state"] == "open"
    assert grant["canEdit"] is True
    assert grant["items"] == []
    assert grant["itemsDuration"] == "00:00:00"


def test_anonymous_callers_are_refused() -> None:
    assert client_for(None).get(reverse("api-grant-list")).status_code == 401


def test_list_filters(staff, open_grant) -> None:
    client = client_for(staff)
    url = reverse("api-grant-list")

    assert len(client.get(url, {"state": "open"}).json()["results"]) == 1
    assert len(client.get(url, {"state": "locked"}).json()["results"]) == 0
    after = (open_grant.starttime + timedelta(minutes=1)).isoformat()
    assert len(client.get(url, {"starts_after": after}).json()["results"]) == 0
    assert len(client.get(url, {"organization": open_grant.organization_id}).json()["results"]) == 1


def test_put_items_replaces_the_list(member, open_grant, video) -> None:
    second = Video.objects.create(
        name="Second",
        creator=member,
        organization=video.organization,
        duration=timedelta(minutes=10),
        proper_import=False,  # a pending upload with a hand-set duration
    )

    response = client_for(member).put(
        items_url(open_grant),
        {"items": [{"video": second.pk}, {"video": video.pk}]},
        format="json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert [(i["position"], i["video"]["id"]) for i in body["items"]] == [
        (0, second.pk),
        (1, video.pk),
    ]
    assert body["items"][0]["video"]["pendingUpload"] is True
    assert body["items"][0]["video"]["liveSource"] is None
    assert body["itemsDuration"] == "00:30:00"


def test_put_items_refuses_an_overrun(member, open_grant, video) -> None:
    response = client_for(member).put(
        items_url(open_grant), {"items": [{"video": video.pk}] * 4}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["errors"][0]["attr"] == "items"
    assert not GrantItem.objects.exists()


def test_put_items_is_refused_after_the_deadline(member, open_grant, video) -> None:
    AirtimeGrant.objects.filter(pk=open_grant.pk).update(locks_at=timezone.now())

    response = client_for(member).put(
        items_url(open_grant), {"items": [{"video": video.pk}]}, format="json"
    )

    assert response.status_code == 403


def test_outsiders_cannot_see_or_edit_a_grant(open_grant, video) -> None:
    outsider = User.objects.create(email="outsider@example.test", identity_confirmed=True)

    response = client_for(outsider).put(
        items_url(open_grant), {"items": [{"video": video.pk}]}, format="json"
    )

    assert response.status_code == 404


def test_back_to_automatic(member, organization, open_grant, video) -> None:
    recipe = uploads_recipe(organization)
    slot = RecurringSlot.objects.create(
        organization=organization,
        weekdays=[0],
        start_time="12:00",
        duration=timedelta(hours=1),
        starts_on=datetime.date(2026, 1, 1),
        recipe=recipe,
    )
    AirtimeGrant.objects.filter(pk=open_grant.pk).update(origin_slot=slot)

    response = client_for(member).post(reverse("api-grant-automatic", args=[open_grant.pk]))

    assert response.status_code == 200, response.content
    assert response.json()["recipe"]["id"] == recipe.pk
    assert [i["video"]["id"] for i in response.json()["items"]] == [video.pk]


def test_staff_create_a_grant_with_the_default_deadline(staff, organization) -> None:
    start = in_days(12)

    response = client_for(staff).post(
        reverse("api-grant-list"),
        {
            "organization": organization.pk,
            "starttime": start.isoformat(),
            "endtime": (start + timedelta(hours=2)).isoformat(),
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    grant = AirtimeGrant.objects.get()
    assert grant.locks_at == broadcast_time.default_locks_at(start)


def test_airtime_given_after_its_deadline_stays_open_until_air(staff, organization) -> None:
    start = in_days(2)

    response = client_for(staff).post(
        reverse("api-grant-list"),
        {
            "organization": organization.pk,
            "starttime": start.isoformat(),
            "endtime": (start + timedelta(hours=1)).isoformat(),
            "recipe": {"kind": "uploads"},
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["lockExempt"] is True
    assert body["canEdit"] is True
    assert body["recipe"]["kind"] == "uploads"


def test_staff_cannot_give_airtime_in_the_past_or_to_a_suspended_organization(
    staff, organization
) -> None:
    def give(start, to=organization):
        return client_for(staff).post(
            reverse("api-grant-list"),
            {
                "organization": to.pk,
                "starttime": start.isoformat(),
                "endtime": (start + timedelta(hours=1)).isoformat(),
            },
            format="json",
        )

    assert give(in_days(-1)).status_code == 400
    rogue = Organization.objects.create(name="Rogue", fkmember=True, is_rogue=True)
    response = give(in_days(12), rogue)
    assert response.status_code == 400
    assert response.json()["errors"][0]["attr"] == "organization"


def test_staff_cannot_create_an_overlapping_grant(staff, organization, open_grant) -> None:
    response = client_for(staff).post(
        reverse("api-grant-list"),
        {
            "organization": organization.pk,
            "starttime": (open_grant.starttime + timedelta(minutes=30)).isoformat(),
            "endtime": (open_grant.endtime + timedelta(minutes=30)).isoformat(),
        },
        format="json",
    )

    assert response.status_code == 400


def test_members_cannot_create_or_change_grants(member, organization, open_grant) -> None:
    client = client_for(member)
    start = in_days(12)

    created = client.post(
        reverse("api-grant-list"),
        {
            "organization": organization.pk,
            "starttime": start.isoformat(),
            "endtime": (start + timedelta(hours=1)).isoformat(),
        },
        format="json",
    )
    patched = client.patch(
        reverse("api-grant-detail", args=[open_grant.pk]), {"lockExempt": True}, format="json"
    )

    assert (created.status_code, patched.status_code) == (403, 403)


def test_staff_exempt_and_cancel(staff, open_grant) -> None:
    client = client_for(staff)
    url = reverse("api-grant-detail", args=[open_grant.pk])

    exempted = client.patch(url, {"lockExempt": True}, format="json")
    cancelled = client.patch(url, {"state": "cancelled"}, format="json")

    assert exempted.json()["lockExempt"] is True
    assert cancelled.json()["state"] == "cancelled"
    assert cancelled.json()["canEdit"] is False


def test_put_items_automatic_pins_them_and_fills_the_rest(
    member, organization, open_grant, video
) -> None:
    chosen = Video.objects.create(
        name="Chosen",
        creator=member,
        organization=organization,
        duration=timedelta(minutes=10),
        proper_import=True,
    )

    response = client_for(member).put(
        items_url(open_grant),
        {"items": [{"video": chosen.pk}], "automatic": True},
        format="json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert [(i["video"]["id"], i["pinned"]) for i in body["items"]] == [
        (chosen.pk, True),
        (video.pk, False),
    ]
    assert body["recipe"]["kind"] == "uploads"


def test_put_items_chooses_a_series_for_this_grant_or_its_whole_slot(
    member, organization, video
) -> None:
    series = Series.objects.create(organization=organization, name="Ukens program")
    episode = Video.objects.create(
        name="Episode",
        creator=member,
        organization=organization,
        series=series,
        duration=timedelta(minutes=20),
        proper_import=True,
    )
    slot = RecurringSlot.objects.create(
        organization=organization,
        weekdays=[0],
        start_time="18:00",
        duration=timedelta(hours=1),
        starts_on=datetime.date(2026, 1, 1),
    )
    start = in_days(20)
    grant = AirtimeGrant.objects.create(
        organization=organization,
        starttime=start,
        endtime=start + timedelta(hours=1),
        locks_at=in_days(10),
        origin_slot=slot,
    )

    chosen = {"kind": "series", "series": series.pk}

    response = client_for(member).put(
        items_url(grant), {"items": [], "automatic": True, "recipe": chosen}, format="json"
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert (body["recipe"]["series"], body["recipe"]["seriesName"]) == (series.pk, "Ukens program")
    assert [i["video"]["id"] for i in body["items"]] == [episode.pk]  # not `video`
    slot.refresh_from_db()
    assert slot.recipe is None  # only this broadcast

    response = client_for(member).put(
        items_url(grant),
        {"items": [], "automatic": True, "recipe": chosen, "wholeSlot": True},
        format="json",
    )
    assert response.status_code == 200, response.content
    slot.refresh_from_db()
    assert slot.recipe == series_recipe(series)

    # Back to everything they upload, for this broadcast only.
    response = client_for(member).put(
        items_url(grant),
        {"items": [], "automatic": True, "recipe": {"kind": "uploads"}},
        format="json",
    )
    assert response.json()["recipe"]["kind"] == "uploads"
    assert {i["video"]["id"] for i in response.json()["items"]} == {episode.pk, video.pk}


def test_put_items_refuses_another_organizations_series(member, open_grant) -> None:
    other = Organization.objects.create(name="Other", fkmember=True)
    theirs = Series.objects.create(organization=other, name="Theirs")

    response = client_for(member).put(
        items_url(open_grant),
        {"items": [], "automatic": True, "recipe": {"kind": "series", "series": theirs.pk}},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["errors"][0]["attr"] == "series"


def test_members_cannot_turn_their_grant_into_jukebox(member, open_grant) -> None:
    response = client_for(member).put(
        items_url(open_grant),
        {"items": [], "automatic": True, "recipe": {"kind": "jukebox"}},
        format="json",
    )

    assert response.status_code == 400
