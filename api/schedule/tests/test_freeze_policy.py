"""
The broadcast-week policy at the API boundary (agenda.scheduling.policy):
members may edit only the open week, their picks displace jukebox
fillers, and staff is exempt.

The pinned clock puts the freeze boundary at Mon 2014-12-29; the open
week is 2014-12-29 through 2015-01-04.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from agenda.scheduling.jukebox import fill_agenda_with_jukebox
from fk.models import Organization, Scheduleitem, User, Video, WeeklySlot

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("now_in_the_drafting_week")]

OSLO = ZoneInfo("Europe/Oslo")
IN_THE_OPEN_WEEK = datetime(2015, 1, 1, 10, tzinfo=OSLO)
IN_THE_FROZEN_WEEK = datetime(2014, 12, 25, 10, tzinfo=OSLO)
OPEN_MONDAY = "2014-12-29"


@pytest.fixture
def member(organization: Organization) -> User:
    user = User.objects.create(
        email="freeze-member@example.test",
        identity_confirmed=True,
    )
    organization.members.add(user)
    return user


@pytest.fixture
def member_client(member: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=member)
    return client


@pytest.fixture
def staff_client() -> APIClient:
    """The freeze exemption follows is_staff (a superuser alias on this
    User model), like IsInOrganizationOrReadOnly's."""
    client = APIClient()
    client.force_authenticate(
        user=User.objects.create(email="freeze-staff@example.test", is_superuser=True)
    )
    return client


@pytest.fixture
def other_organization() -> Organization:
    """fkmember, so its fillers are eligible when the jukebox refills."""
    editor = User.objects.create(email="freeze-other-editor@example.test")
    return Organization.objects.create(name="Other org", editor=editor, fkmember=True)


def jukebox_filler_at(
    organization: Organization, starttime: datetime, minutes: int = 60
) -> Scheduleitem:
    editor = organization.editor
    assert editor is not None, "fixture should have given the organization an editor"
    filler = Video.objects.create(
        creator=editor,
        name="Jukebox filler",
        organization=organization,
        duration=timedelta(minutes=minutes),
        proper_import=True,
        is_filler=True,
    )
    return Scheduleitem.objects.create(
        video=filler,
        starttime=starttime,
        duration=filler.duration,
        schedulereason=Scheduleitem.REASON_JUKEBOX,
    )


def post_item(client: APIClient, video: Video, starttime: datetime):
    return client.post(
        reverse("api-scheduleitem-list"),
        {
            "video": video.pk,
            "starttime": starttime.isoformat(),
            "duration": "01:00:00",
            "schedulereason": Scheduleitem.REASON_USER,
        },
        format="json",
    )


# --- the freeze -------------------------------------------------------------


def test_members_cannot_schedule_into_the_frozen_weeks(
    member_client: APIClient, video: Video
) -> None:
    response = post_item(member_client, video, IN_THE_FROZEN_WEEK)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert OPEN_MONDAY in str(response.data)
    assert not Scheduleitem.objects.exists()


def test_members_cannot_move_an_item_into_the_frozen_weeks(
    member_client: APIClient, schedule_item_factory
) -> None:
    item = schedule_item_factory(starttime=IN_THE_OPEN_WEEK)

    response = member_client.patch(
        reverse("api-scheduleitem-detail", args=[item.pk]),
        {"starttime": IN_THE_FROZEN_WEEK.isoformat()},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    item.refresh_from_db()
    assert item.starttime == IN_THE_OPEN_WEEK


def test_members_cannot_touch_an_item_already_in_the_frozen_weeks(
    member_client: APIClient, schedule_item_factory
) -> None:
    """Even an edit that keeps the airtime (or no airtime change at all)
    is refused: the published week is fixed in content, not just shape."""
    item = schedule_item_factory(starttime=IN_THE_FROZEN_WEEK)

    patch_response = member_client.patch(
        reverse("api-scheduleitem-detail", args=[item.pk]),
        {"duration": "00:30:00"},
        format="json",
    )
    delete_response = member_client.delete(reverse("api-scheduleitem-detail", args=[item.pk]))

    assert patch_response.status_code == status.HTTP_400_BAD_REQUEST
    assert delete_response.status_code == status.HTTP_403_FORBIDDEN
    item.refresh_from_db()
    assert item.duration == timedelta(hours=1)


def test_members_can_edit_the_open_week(member_client: APIClient, video: Video) -> None:
    response = post_item(member_client, video, IN_THE_OPEN_WEEK)

    assert response.status_code == status.HTTP_201_CREATED
    assert Scheduleitem.objects.get().starttime == IN_THE_OPEN_WEEK


def test_staff_may_change_the_frozen_weeks(staff_client: APIClient, video: Video) -> None:
    create_response = post_item(staff_client, video, IN_THE_FROZEN_WEEK)
    item_pk = create_response.data["id"]
    delete_response = staff_client.delete(reverse("api-scheduleitem-detail", args=[item_pk]))

    assert create_response.status_code == status.HTTP_201_CREATED
    assert delete_response.status_code == status.HTTP_204_NO_CONTENT


def test_a_member_edit_strips_slot_provenance(member_client: APIClient, video: Video) -> None:
    """An edited slot placement becomes deliberate programming: with
    provenance kept, the nightly re-pick could overwrite the change the
    member just made on purpose."""
    slot = WeeklySlot.objects.create(
        day=0, start_time=IN_THE_OPEN_WEEK.time(), duration=timedelta(hours=1)
    )
    item = Scheduleitem.objects.create(
        video=video,
        schedulereason=Scheduleitem.REASON_AUTO,
        starttime=IN_THE_OPEN_WEEK,
        duration=timedelta(hours=1),
        weekly_slot=slot,
    )

    response = member_client.patch(
        reverse("api-scheduleitem-detail", args=[item.pk]),
        {"duration": "00:30:00"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    item.refresh_from_db()
    assert item.weekly_slot is None
    assert item.duration == timedelta(minutes=30)


# --- displacement of jukebox fillers ----------------------------------------


def test_a_member_pick_displaces_jukebox_fillers(
    member_client: APIClient, video: Video, other_organization: Organization
) -> None:
    """Fillers belong to some other organization's video, so members
    could never delete them directly; a pick overlapping only fillers
    replaces them in one step."""
    fully_covered = jukebox_filler_at(other_organization, IN_THE_OPEN_WEEK)
    straddling = jukebox_filler_at(
        other_organization, IN_THE_OPEN_WEEK + timedelta(minutes=30), minutes=60
    )

    response = post_item(member_client, video, IN_THE_OPEN_WEEK)

    assert response.status_code == status.HTTP_201_CREATED
    remaining = Scheduleitem.objects.get()
    assert remaining.video == video
    assert not Scheduleitem.objects.filter(pk__in=[fully_covered.pk, straddling.pk]).exists()


def test_displacement_also_applies_when_moving_an_item(
    member_client: APIClient, schedule_item_factory, other_organization: Organization
) -> None:
    item = schedule_item_factory(starttime=IN_THE_OPEN_WEEK)
    target = IN_THE_OPEN_WEEK + timedelta(hours=3)
    filler = jukebox_filler_at(other_organization, target)

    response = member_client.patch(
        reverse("api-scheduleitem-detail", args=[item.pk]),
        {"starttime": target.isoformat()},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert not Scheduleitem.objects.filter(pk=filler.pk).exists()
    item.refresh_from_db()
    assert item.starttime == target


def test_the_nightly_jukebox_repacks_what_a_displacement_orphans(
    member_client: APIClient, video: Video, other_organization: Organization
) -> None:
    """A pick over the last 5 minutes of a 50-minute filler deletes the
    whole filler, orphaning the ~45 minutes in front of the pick. The
    nightly jukebox walks the entire window again, so the next run
    repacks that stretch -- here with the same 50-minute video, twice,
    leaving only a sub-minimum sliver before the pick.
    """
    filler_item = jukebox_filler_at(other_organization, IN_THE_OPEN_WEEK, minutes=50)
    pick_start = IN_THE_OPEN_WEEK + timedelta(minutes=45)

    response = post_item(member_client, video, pick_start)
    assert response.status_code == status.HTTP_201_CREATED
    assert not Scheduleitem.objects.filter(pk=filler_item.pk).exists()

    fill_agenda_with_jukebox(start=IN_THE_OPEN_WEEK - timedelta(hours=1), days=0.25)

    refills = Scheduleitem.objects.filter(
        schedulereason=Scheduleitem.REASON_JUKEBOX, starttime__lt=pick_start
    ).order_by("starttime")
    # 09:01 and 09:52: packed from the whole minute after the window
    # opens up to the pick, ending 10:42 -- a 3-minute leftover, below
    # the jukebox's 5-minute minimum.
    assert [item.starttime for item in refills] == [
        IN_THE_OPEN_WEEK - timedelta(minutes=59),
        IN_THE_OPEN_WEEK - timedelta(minutes=8),
    ]
    last_refill, first_later = (
        refills.last(),
        Scheduleitem.objects.filter(starttime__gte=pick_start).order_by("starttime").first(),
    )
    assert last_refill is not None and first_later is not None
    assert last_refill.endtime() <= pick_start
    assert first_later.starttime == pick_start  # the pick survived intact


def test_a_conflict_with_deliberate_programming_still_refuses(
    member_client: APIClient, video: Video, schedule_item_factory, other_organization: Organization
) -> None:
    """Only REASON_JUKEBOX gives way. A manual item on the same airtime
    is a conflict, and the fillers beside it survive the refusal."""
    filler = jukebox_filler_at(other_organization, IN_THE_OPEN_WEEK)
    deliberate = schedule_item_factory(starttime=IN_THE_OPEN_WEEK + timedelta(minutes=30))

    response = post_item(member_client, video, IN_THE_OPEN_WEEK)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Conflict" in str(response.data)
    assert Scheduleitem.objects.filter(pk__in=[filler.pk, deliberate.pk]).count() == 2
