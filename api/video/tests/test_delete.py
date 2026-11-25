"""DELETE /api/videos/{id}: a video in airtime lists can be deleted."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from api.grants.tests.test_grants_api import client_for, in_days
from fk.models import Organization, User, Video
from scheduling import services
from scheduling.models import AirtimeGrant

pytestmark = pytest.mark.django_db


@pytest.fixture
def member() -> User:
    return User.objects.create(email="delete-member@example.test", identity_confirmed=True)


@pytest.fixture
def organization(member: User) -> Organization:
    organization = Organization.objects.create(name="Delete org", editor=member, fkmember=True)
    organization.members.add(member)
    return organization


def make_video(member: User, organization: Organization, minutes: int = 20) -> Video:
    return Video.objects.create(
        name="Testvideo",
        creator=member,
        organization=organization,
        duration=timedelta(minutes=minutes),
        proper_import=True,
    )


def grant_listing(organization: Organization, start, video: Video) -> AirtimeGrant:
    grant = AirtimeGrant.objects.create(
        organization=organization,
        starttime=start,
        endtime=start + timedelta(hours=1),
        locks_at=start,
        state=AirtimeGrant.State.LOCKED,
    )
    services._replace_items(grant, [video], pinned=1)
    services.compile_grant(grant, start - timedelta(days=1))
    return grant


def test_a_video_in_a_coming_grant_is_deleted_and_the_grant_forgets_it(
    member, organization
) -> None:
    video = make_video(member, organization)
    grant = grant_listing(organization, in_days(5), video)

    response = client_for(member).delete(reverse("api-video-detail", args=[video.pk]))

    assert response.status_code == 204, response.content
    assert not Video.objects.filter(pk=video.pk).exists()
    assert not grant.items.exists()
    assert not grant.schedule_items.exists()


def test_a_video_on_air_is_refused_with_a_reason(member, organization) -> None:
    video = make_video(member, organization)
    start = timezone.now().replace(second=0, microsecond=0) - timedelta(minutes=5)
    grant_listing(organization, start, video)

    response = client_for(member).delete(reverse("api-video-detail", args=[video.pk]))

    assert response.status_code == 400
    assert "på lufta" in str(response.json())
    assert Video.objects.filter(pk=video.pk).exists()


def test_staff_delete_a_listed_video_in_the_admin(client, member, organization) -> None:
    staff = User.objects.create(email="delete-staff@example.test", is_superuser=True)
    video = make_video(member, organization)
    grant = grant_listing(organization, in_days(5), video)
    client.force_login(staff)
    url = reverse("admin:fk_video_delete", args=[video.pk])

    page = client.get(url)
    assert page.status_code == 200
    assert b"protected" not in page.content.lower()

    assert client.post(url, {"post": "yes"}).status_code == 302
    assert not Video.objects.filter(pk=video.pk).exists()
    assert not grant.items.exists()
