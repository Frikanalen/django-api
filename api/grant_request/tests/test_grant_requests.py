from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from fk.models import (
    AirtimeGrantRequest,
    GrantExemptionRequest,
    Organization,
    RecurringSlotCreationRequest,
    ReviewStatus,
    User,
)
from scheduling import broadcast_time
from scheduling.models import AirtimeGrant, RecurringSlot

pytestmark = pytest.mark.django_db


@pytest.fixture
def member() -> User:
    return User.objects.create(email="requester@example.test", identity_confirmed=True)


@pytest.fixture
def organization(member: User) -> Organization:
    editor = User.objects.create(email="request-editor@example.test")
    organization = Organization.objects.create(name="Requesters", editor=editor, fkmember=True)
    organization.members.add(member)
    return organization


@pytest.fixture
def member_client(member: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(member)
    return client


@pytest.fixture
def staff_client() -> APIClient:
    client = APIClient()
    client.force_authenticate(User.objects.create(email="boss@example.test", is_superuser=True))
    return client


def in_days(days: int, hour: int = 19) -> timezone.datetime:
    day = broadcast_time.local_date(timezone.now()) + timedelta(days=days)
    return broadcast_time.local_instant(day, timezone.datetime.min.time().replace(hour=hour))


def airtime(organization: Organization, start, hours: int = 1, **extra) -> dict:
    return {
        "organization": organization.pk,
        "starttime": start.isoformat(),
        "endtime": (start + timedelta(hours=hours)).isoformat(),
        "message": "Valgvake",
        **extra,
    }


def attrs(response) -> set[str]:
    return {error["attr"] for error in response.json()["errors"]}


def decide(client: APIClient, name: str, pk: int, status: str = "approved", comment="OK"):
    return client.post(
        reverse(name, args=[pk]), {"status": status, "adminComment": comment}, format="json"
    )


def test_member_requests_airtime_and_staff_approval_creates_the_grant(
    member_client: APIClient, staff_client: APIClient, organization: Organization
):
    start = in_days(20)
    response = member_client.post(
        reverse("api-airtime-request-create"), airtime(organization, start), format="json"
    )
    assert response.status_code == 201, response.json()
    assert response.json()["organizationName"] == "Requesters"
    assert response.json()["status"] == "pending"

    decided = decide(staff_client, "api-airtime-request-decision", response.json()["id"])
    assert decided.status_code == 200, decided.json()
    request = AirtimeGrantRequest.objects.get()
    assert request.status == ReviewStatus.APPROVED
    assert request.grant.organization == organization
    assert request.grant.starttime == start
    assert request.grant.fill_rule is not None
    assert not request.grant.lock_exempt


def test_airtime_approved_after_the_deadline_stays_editable(
    member_client: APIClient, staff_client: APIClient, organization: Organization
):
    response = member_client.post(
        reverse("api-airtime-request-create"), airtime(organization, in_days(1)), format="json"
    )
    decide(staff_client, "api-airtime-request-decision", response.json()["id"])
    assert AirtimeGrantRequest.objects.get().grant.lock_exempt


def test_approval_fails_when_the_time_was_taken_meanwhile(
    member_client: APIClient, staff_client: APIClient, organization: Organization
):
    start = in_days(20)
    response = member_client.post(
        reverse("api-airtime-request-create"), airtime(organization, start), format="json"
    )
    other = Organization.objects.create(name="Faster", fkmember=True)
    AirtimeGrant.objects.create(
        organization=other,
        starttime=start - timedelta(minutes=30),
        endtime=start + timedelta(minutes=30),
        locks_at=start - timedelta(days=7),
    )
    decided = decide(staff_client, "api-airtime-request-decision", response.json()["id"])
    assert decided.status_code == 400
    assert AirtimeGrantRequest.objects.get().status == ReviewStatus.PENDING


def test_airtime_someone_has_is_refused_up_front(
    member_client: APIClient, organization: Organization
):
    start = in_days(20)
    AirtimeGrant.objects.create(
        organization=Organization.objects.create(name="Holder", fkmember=True),
        starttime=start,
        endtime=start + timedelta(hours=1),
        locks_at=start - timedelta(days=7),
    )
    response = member_client.post(
        reverse("api-airtime-request-create"), airtime(organization, start), format="json"
    )
    assert response.status_code == 400
    assert "starttime" in attrs(response)


@pytest.mark.parametrize(
    ("start_days", "hours", "field"), [(-1, 1, "starttime"), (20, 25, "endtime")]
)
def test_airtime_must_be_ahead_and_short(
    member_client: APIClient, organization: Organization, start_days, hours, field
):
    response = member_client.post(
        reverse("api-airtime-request-create"),
        airtime(organization, in_days(start_days), hours=hours),
        format="json",
    )
    assert response.status_code == 400
    assert field in attrs(response)


def test_suspended_or_unconfirmed_members_cannot_request(
    member: User, member_client: APIClient, organization: Organization
):
    organization.is_rogue = True
    organization.save()
    response = member_client.post(
        reverse("api-airtime-request-create"), airtime(organization, in_days(20)), format="json"
    )
    assert response.status_code == 403

    organization.is_rogue = False
    organization.save()
    member.identity_confirmed = False
    member.save()
    response = member_client.post(
        reverse("api-airtime-request-create"), airtime(organization, in_days(20)), format="json"
    )
    assert response.status_code == 403


def test_outsiders_cannot_request_for_an_organization(organization: Organization):
    client = APIClient()
    client.force_authenticate(
        User.objects.create(email="out@example.test", identity_confirmed=True)
    )
    response = client.post(
        reverse("api-airtime-request-create"), airtime(organization, in_days(20)), format="json"
    )
    assert response.status_code == 403


def locked_grant(organization: Organization) -> AirtimeGrant:
    start = in_days(3)
    return AirtimeGrant.objects.create(
        organization=organization,
        starttime=start,
        endtime=start + timedelta(hours=1),
        locks_at=timezone.now() - timedelta(hours=1),
        state=AirtimeGrant.State.LOCKED,
    )


def test_exemption_request_and_approval(
    member_client: APIClient, staff_client: APIClient, organization: Organization
):
    grant = locked_grant(organization)
    response = member_client.post(
        reverse("api-exemption-request-create"),
        {"grant": grant.pk, "message": "Gjesten kom sent"},
        format="json",
    )
    assert response.status_code == 201, response.json()
    assert response.json()["organization"] == organization.pk

    again = member_client.post(
        reverse("api-exemption-request-create"), {"grant": grant.pk}, format="json"
    )
    assert again.status_code == 400

    decided = decide(staff_client, "api-exemption-request-decision", response.json()["id"])
    assert decided.status_code == 200
    grant.refresh_from_db()
    assert grant.lock_exempt


def test_denial_leaves_the_grant_and_keeps_the_comment(
    member_client: APIClient, staff_client: APIClient, organization: Organization
):
    grant = locked_grant(organization)
    response = member_client.post(
        reverse("api-exemption-request-create"), {"grant": grant.pk}, format="json"
    )
    decide(staff_client, "api-exemption-request-decision", response.json()["id"], "denied", "Nei")
    grant.refresh_from_db()
    assert not grant.lock_exempt
    request = GrantExemptionRequest.objects.get()
    assert (request.status, request.admin_comment) == (ReviewStatus.DENIED, "Nei")


def test_members_cannot_decide(member_client: APIClient, organization: Organization):
    grant = locked_grant(organization)
    response = member_client.post(
        reverse("api-exemption-request-create"), {"grant": grant.pk}, format="json"
    )
    decided = decide(member_client, "api-exemption-request-decision", response.json()["id"])
    assert decided.status_code == 403


def test_a_decision_needs_a_comment_and_happens_once(
    member_client: APIClient, staff_client: APIClient, organization: Organization
):
    grant = locked_grant(organization)
    pk = member_client.post(
        reverse("api-exemption-request-create"), {"grant": grant.pk}, format="json"
    ).json()["id"]
    assert decide(staff_client, "api-exemption-request-decision", pk, comment="").status_code == 400
    assert decide(staff_client, "api-exemption-request-decision", pk).status_code == 200
    assert decide(staff_client, "api-exemption-request-decision", pk).status_code == 400


def test_list_shows_members_their_own_and_filters(
    member_client: APIClient, staff_client: APIClient, organization: Organization
):
    member_client.post(
        reverse("api-airtime-request-create"), airtime(organization, in_days(20)), format="json"
    )
    member_client.post(
        reverse("api-exemption-request-create"),
        {"grant": locked_grant(organization).pk},
        format="json",
    )
    other = Organization.objects.create(name="Others", fkmember=True)
    AirtimeGrantRequest.objects.create(
        organization=other,
        requested_by=User.objects.create(email="o@example.test"),
        starttime=in_days(25),
        endtime=in_days(25) + timedelta(hours=1),
    )
    url = reverse("api-grant-request-list")
    mine = member_client.get(url).json()["results"]
    assert sorted(next(iter(item)) for item in mine) == ["airtime", "exemption"]
    assert staff_client.get(url).json()["count"] == 3
    assert staff_client.get(url, {"organization": other.pk}).json()["count"] == 1
    assert staff_client.get(url, {"status": "approved"}).json()["count"] == 0


def test_slot_creation_decision_endpoint_creates_the_slot(
    member: User, staff_client: APIClient, organization: Organization
):
    slot_request = RecurringSlotCreationRequest.objects.create(
        organization=organization,
        requested_by=member,
        day=2,
        start_time="18:00",
        duration=timedelta(hours=1),
    )
    decided = decide(staff_client, "api-recurring-slot-creation-request-decision", slot_request.pk)
    assert decided.status_code == 200, decided.json()
    slot_request.refresh_from_db()
    assert slot_request.slot.weekdays == [2]


def test_slot_creation_is_not_approved_over_another_slot(
    member: User, staff_client: APIClient, organization: Organization
):
    RecurringSlot.objects.create(
        organization=Organization.objects.create(name="Incumbent", fkmember=True),
        weekdays=[2],
        start_time="18:30",
        duration=timedelta(hours=1),
        starts_on=date(2026, 1, 1),
    )
    slot_request = RecurringSlotCreationRequest.objects.create(
        organization=organization,
        requested_by=member,
        day=2,
        start_time="18:00",
        duration=timedelta(hours=1),
    )
    decided = decide(staff_client, "api-recurring-slot-creation-request-decision", slot_request.pk)
    assert decided.status_code == 400
    assert "Incumbent" in decided.json()["errors"][0]["detail"]
