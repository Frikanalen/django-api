"""
fill_next_weeks_agenda places one video per WeeklySlot; it runs from
cron via the management command and had zero coverage.
"""

from datetime import time, timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from agenda.scheduling.weekly_slots import fill_next_weeks_agenda
from fk.models import (
    Organization,
    Scheduleitem,
    SchedulePurpose,
    User,
    Video,
    WeeklySlot,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def organization() -> Organization:
    editor = User.objects.create(email="slot-editor@example.test")
    return Organization.objects.create(name="Slot org", editor=editor)


@pytest.fixture
def video(organization: Organization) -> Video:
    return Video.objects.create(
        name="Slot video",
        creator=organization.editor,
        organization=organization,
        duration=timedelta(minutes=25),
        proper_import=True,
    )


@pytest.fixture
def purpose(organization: Organization) -> SchedulePurpose:
    return SchedulePurpose.objects.create(
        name="Slot purpose",
        type=SchedulePurpose.TYPE.organization,
        strategy="latest",
        organization=organization,
    )


def make_slot(purpose: SchedulePurpose | None, **fields) -> WeeklySlot:
    return WeeklySlot.objects.create(
        purpose=purpose,
        day=fields.pop("day", 0),
        start_time=fields.pop("start_time", time(12, 0)),
        duration=fields.pop("duration", timedelta(hours=1)),
    )


def test_fills_a_slot_with_the_purposes_video(video: Video, purpose: SchedulePurpose) -> None:
    slot = make_slot(purpose)

    fill_next_weeks_agenda()

    item = Scheduleitem.objects.get()
    assert item.video == video
    assert item.schedulereason == Scheduleitem.REASON_AUTO
    assert item.starttime == slot.next_datetime()
    # The item takes the video's duration, not the slot's.
    assert item.duration == video.duration


def test_does_nothing_without_slots(video: Video) -> None:
    fill_next_weeks_agenda()

    assert not Scheduleitem.objects.exists()


def test_skips_slots_without_a_purpose(video: Video) -> None:
    make_slot(None)

    fill_next_weeks_agenda()

    assert not Scheduleitem.objects.exists()


def test_skips_slots_whose_purpose_has_no_eligible_video(
    organization: Organization, purpose: SchedulePurpose
) -> None:
    Video.objects.create(
        name="Too long for the slot",
        creator=organization.editor,
        organization=organization,
        duration=timedelta(hours=2),
        proper_import=True,
    )
    make_slot(purpose, duration=timedelta(hours=1))

    fill_next_weeks_agenda()

    assert not Scheduleitem.objects.exists()


def test_leaves_an_already_occupied_slot_alone(video: Video, purpose: SchedulePurpose) -> None:
    slot = make_slot(purpose)
    existing = Scheduleitem.objects.create(
        video=video,
        schedulereason=Scheduleitem.REASON_ADMIN,
        starttime=slot.next_datetime() + timedelta(minutes=30),
        duration=timedelta(minutes=10),
    )

    fill_next_weeks_agenda()

    assert list(Scheduleitem.objects.all()) == [existing]


def test_management_command_runs_the_filler(video: Video, purpose: SchedulePurpose) -> None:
    make_slot(purpose)

    call_command("fill_next_weeks_agenda")

    assert Scheduleitem.objects.count() == 1


def test_jukebox_management_command_fills_two_days(organization: Organization) -> None:
    organization.fkmember = True
    organization.save()
    Video.objects.create(
        name="Jukebox filler",
        creator=organization.editor,
        organization=organization,
        duration=timedelta(hours=1),
        proper_import=True,
        is_filler=True,
    )

    call_command("fill_agenda_with_jukebox")

    count = Scheduleitem.objects.count()
    # Two days of hour-long slots, minus rounding at the edges.
    assert 44 <= count <= 48
    assert set(Scheduleitem.objects.values_list("schedulereason", flat=True)) == {
        Scheduleitem.REASON_JUKEBOX
    }
    latest = Scheduleitem.objects.order_by("starttime").last()
    assert latest.starttime <= timezone.now() + timedelta(days=2)
