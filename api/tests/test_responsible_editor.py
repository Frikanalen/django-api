"""
Accountability: an organization with no ansvarlig redaktor, and
everything belonging to it, is staff-only.

Nothing may be broadcast on an organization's behalf without an editor
answering for it, so an organization that has lost one disappears from
the public API along with its videos, and stops being picked up by the
automatic schedulers. The rule has a single definition
(OrganizationQuerySet.with_responsible_editor); these tests pin its
consequences at every surface that consults it.

The schedule and the XMLTV feed deliberately keep showing such
programmes: they describe what actually airs, and hiding an item that
playout still plays would misrepresent the broadcast record.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from agenda.scheduling.jukebox import fill_agenda_with_jukebox
from fk.models import (
    Category,
    Organization,
    Scheduleitem,
    SlotSourceStrategy,
    SlotSourceType,
    User,
    Video,
    WeeklySlot,
    WeeklySlotSource,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def editor() -> User:
    return User.objects.create(email="responsible-editor@example.test")


@pytest.fixture
def organization(editor: User) -> Organization:
    organization = Organization.objects.create(name="Accountable org", fkmember=True, editor=editor)
    organization.members.add(editor)
    return organization


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    return Video.objects.create(
        name="Accountable video",
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=30),
        proper_import=True,
        publish_on_web=True,
        is_filler=True,
        has_tono_records=False,
    )


@pytest.fixture
def staff_client() -> APIClient:
    client = APIClient()
    client.force_authenticate(
        user=User.objects.create(email="accountability-staff@example.test", is_superuser=True)
    )
    return client


@pytest.fixture
def member_client(editor: User) -> APIClient:
    """The organization's own editor, once their account is disabled."""
    client = APIClient()
    client.force_authenticate(user=editor)
    return client


def vacate(organization: Organization) -> None:
    organization.editor = None
    organization.save()


def disable_editor(organization: Organization) -> None:
    """The other way to end up without one: the editor's account is off."""
    editor = organization.editor
    assert editor is not None, "fixture should have given the organization an editor"
    editor.is_active = False
    editor.save()


LOSS = [
    pytest.param(vacate, id="editor-removed"),
    pytest.param(disable_editor, id="editor-disabled"),
]


@pytest.mark.parametrize("lose_editor", LOSS)
def test_organization_disappears_from_the_public_list(lose_editor, organization) -> None:
    anonymous = APIClient()
    assert [
        org["name"] for org in anonymous.get(reverse("api-organization-list")).json()["results"]
    ] == [organization.name]

    lose_editor(organization)

    assert anonymous.get(reverse("api-organization-list")).json()["results"] == []


@pytest.mark.parametrize("lose_editor", LOSS)
def test_organization_detail_is_hidden(lose_editor, organization) -> None:
    lose_editor(organization)
    url = reverse("api-organization-detail", args=[organization.pk])

    assert APIClient().get(url).status_code == 404


@pytest.mark.parametrize("lose_editor", LOSS)
def test_videos_disappear_from_the_public_list_and_detail(lose_editor, organization, video) -> None:
    anonymous = APIClient()
    detail_url = reverse("api-video-detail", args=[video.pk])

    lose_editor(organization)

    assert anonymous.get(reverse("api-video-list")).json()["results"] == []
    assert anonymous.get(detail_url).status_code == 404
    # Asking for the unfinished videos does not reveal them either: the
    # visibility queryset applies before any filter.
    assert anonymous.get(reverse("api-video-list") + "?proper_import=false").json()["results"] == []


def test_staff_still_see_the_organization_and_its_videos(staff_client, organization, video) -> None:
    vacate(organization)

    assert [
        org["name"] for org in staff_client.get(reverse("api-organization-list")).json()["results"]
    ] == [organization.name]
    assert (
        staff_client.get(reverse("api-organization-detail", args=[organization.pk])).status_code
        == 200
    )
    assert [
        item["name"] for item in staff_client.get(reverse("api-video-list")).json()["results"]
    ] == [video.name]
    assert staff_client.get(reverse("api-video-detail", args=[video.pk])).status_code == 200


def test_members_do_not_get_to_see_it_either(member_client, organization, video) -> None:
    """
    Deliberately staff-only: appointing a new editor is not something
    the organization can do for itself, so hiding it from members too
    is what forces the question to staff.
    """
    vacate(organization)

    assert member_client.get(reverse("api-organization-list")).json()["results"] == []
    assert member_client.get(reverse("api-video-detail", args=[video.pk])).status_code == 404


@pytest.mark.parametrize("lose_editor", LOSS)
def test_videos_stop_being_offered_to_the_jukebox(lose_editor, organization, video) -> None:
    assert list(Video.objects.fillers()) == [video]

    lose_editor(organization)

    assert list(Video.objects.fillers()) == []


@pytest.mark.parametrize("lose_editor", LOSS)
def test_videos_stop_being_scheduled_by_the_agenda_filler(lose_editor, organization, video) -> None:
    """
    The other consumer of `Video.objects.fillers()`: the nightly filler
    that writes jukebox entries into the schedule.  An accountable video
    from a second organization keeps the pool non-empty, so what is
    asserted is the choice between the two, not merely an idle run.
    """
    accountable_editor = User.objects.create(email="accountable-editor@example.test")
    accountable_org = Organization.objects.create(
        name="Still accountable org", fkmember=True, editor=accountable_editor
    )
    accountable_video = Video.objects.create(
        name="Still accountable video",
        creator=accountable_editor,
        organization=accountable_org,
        duration=timedelta(minutes=30),
        proper_import=True,
        is_filler=True,
        has_tono_records=False,
    )

    lose_editor(organization)
    fill_agenda_with_jukebox(datetime(2019, 6, 30, 12, tzinfo=UTC), days=1)

    assert set(Scheduleitem.objects.values_list("video_id", flat=True)) == {accountable_video.id}


@pytest.mark.parametrize("lose_editor", LOSS)
def test_videos_stop_being_picked_for_weekly_slots(lose_editor, organization, video) -> None:
    source = WeeklySlotSource.objects.create(
        name="Accountability source",
        type=SlotSourceType.ORGANIZATION,
        strategy=SlotSourceStrategy.RANDOM,
        organization=organization,
    )
    WeeklySlot.objects.create(day=0, start_time="12:00", duration=timedelta(hours=1), source=source)
    assert source.single_video() == video

    lose_editor(organization)

    assert list(source.videos_queryset()) == []
    assert source.single_video() is None


@pytest.mark.parametrize("lose_editor", LOSS)
def test_public_video_counts_ignore_unaccountable_videos(lose_editor, organization, video) -> None:
    category = Category.objects.create(id=1, name="Accountability")
    video.categories.add(category)
    assert Video.objects.public().count() == 1

    lose_editor(organization)

    assert Video.objects.public().count() == 0
    listed = APIClient().get(reverse("category-list")).json()["results"]
    # One word in the serializer, so camelCasing leaves it alone.
    assert [entry["videocount"] for entry in listed] == [0]


def test_the_schedule_still_shows_what_will_air(organization, video) -> None:
    """
    The counterpart to the rule: the schedule and EPG describe what
    playout actually broadcasts, so an already-scheduled programme stays
    listed even once its organization loses its editor. Pulling it off
    air is a playout decision, not a visibility one.
    """
    starttime = datetime(2015, 6, 1, 12, tzinfo=UTC)
    Scheduleitem.objects.create(
        video=video,
        starttime=starttime,
        duration=timedelta(minutes=30),
        schedulereason=Scheduleitem.REASON_ADMIN,
    )

    vacate(organization)

    listed = APIClient().get(reverse("api-scheduleitem-list"), {"date": "2015-06-01"}).json()
    assert [item["video"]["name"] for item in listed["results"]] == [video.name]
