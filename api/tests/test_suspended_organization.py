"""
Suspension: an organization flagged is_rogue is withdrawn from the
website, the listings and scheduling, but its page stays reachable so
that it can say why. Suspension reuses the accountability rule
(OrganizationQuerySet.with_responsible_editor), so everything that rule
hides is hidden here too; these tests pin what is specific to it.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from fk.models import Organization, Series, User, Video
from scheduling.services import may_act_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def editor() -> User:
    return User.objects.create(email="suspended-editor@example.test", identity_confirmed=True)


@pytest.fixture
def organization(editor: User) -> Organization:
    organization = Organization.objects.create(
        name="Suspended org",
        description="Our programmes",
        homepage="https://example.test",
        fkmember=True,
        editor=editor,
        is_rogue=True,
    )
    organization.members.add(editor)
    return organization


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    return Video.objects.create(
        name="Suspended video",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=30),
        proper_import=True,
        publish_on_web=True,
        is_filler=True,
        has_tono_records=False,
    )


@pytest.fixture
def staff() -> User:
    return User.objects.create(email="suspension-staff@example.test", is_superuser=True)


def test_left_out_of_the_public_organization_list(organization) -> None:
    assert APIClient().get(reverse("api-organization-list")).json()["results"] == []


def test_detail_names_the_organization_and_nothing_more(organization) -> None:
    response = APIClient().get(reverse("api-organization-detail", args=[organization.pk]))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == organization.pk
    assert body["name"] == "Suspended org"
    assert body["isRogue"] is True
    assert body["description"] == ""
    assert body["homepage"] == ""
    assert body["editorEmail"] == ""
    assert body["editorId"] is None


def test_staff_see_everything(organization, staff) -> None:
    client = APIClient()
    client.force_authenticate(user=staff)
    body = client.get(reverse("api-organization-detail", args=[organization.pk])).json()

    assert body["description"] == "Our programmes"
    assert body["isRogue"] is True


def test_cannot_be_edited_by_its_own_editor(organization, editor) -> None:
    client = APIClient()
    client.force_authenticate(user=editor)
    url = reverse("api-organization-detail", args=[organization.pk])

    assert client.patch(url, {"description": "Back again"}, format="json").status_code == 404


def test_video_is_withheld_with_the_organization_named(video, organization) -> None:
    anonymous = APIClient()

    assert anonymous.get(reverse("api-video-list")).json()["results"] == []
    response = anonymous.get(reverse("api-video-detail", args=[video.pk]))
    assert response.status_code == 403
    assert response.json() == {
        "detail": "Organisasjonen er suspendert.",
        "code": "organization_suspended",
        "organization": {"id": organization.pk, "name": "Suspended org"},
    }


def test_missing_video_is_still_a_plain_404() -> None:
    assert APIClient().get(reverse("api-video-detail", args=[999999])).status_code == 404


def test_withdrawn_from_jukebox_and_grants(video) -> None:
    assert list(Video.objects.fillers()) == []
    assert list(Video.objects.listable()) == []
    assert list(Video.objects.public()) == []


def test_nobody_programs_airtime_for_it(organization, editor, staff) -> None:
    assert not may_act_for(editor, organization)
    assert not may_act_for(staff, organization)


def test_lifting_the_suspension_restores_everything(organization, video) -> None:
    organization.is_rogue = False
    organization.save()

    assert APIClient().get(reverse("api-video-detail", args=[video.pk])).status_code == 200
    assert list(Video.objects.fillers()) == [video]


def test_series_are_hidden(organization) -> None:
    Series.objects.create(name="Suspended series", organization=organization)

    assert list(Series.objects.visible_to(None)) == []
