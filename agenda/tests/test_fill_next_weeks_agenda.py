"""
fill_next_weeks_agenda places one video per WeeklySlot occurrence, for
every occurrence between now and the scheduling horizon (the end of the
open broadcast week); it runs nightly from cron before the jukebox.

The fixed clock is a Thursday, so a Monday slot has exactly two
occurrences before the horizon: one in the frozen next week (Mon 7-1)
and one in the open week (Mon 7-8, the freeze boundary's own Monday).
The tests lean on that pair to pin the displacement rules.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.utils import timezone

from agenda.scheduling import weekly_slots
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

OSLO = ZoneInfo("Europe/Oslo")
# A Thursday; broadcast week Mon 2019-06-24. Freeze boundary Mon 07-08,
# horizon Mon 07-15.
NOW = datetime(2019, 6, 27, 12, tzinfo=OSLO)
FROZEN_OCCURRENCE = datetime(2019, 7, 1, 12, tzinfo=OSLO)
OPEN_OCCURRENCE = datetime(2019, 7, 8, 12, tzinfo=OSLO)


@pytest.fixture
def editor() -> User:
    return User.objects.create(email="slot-editor@example.test")


@pytest.fixture
def organization(editor: User) -> Organization:
    return Organization.objects.create(name="Slot org", editor=editor)


@pytest.fixture
def video(editor: User, organization: Organization) -> Video:
    return Video.objects.create(
        name="Slot video",
        creator=editor,
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


def occupy(video: Video, starttime: datetime, duration: timedelta, reason: int) -> Scheduleitem:
    return Scheduleitem.objects.create(
        video=video, schedulereason=reason, starttime=starttime, duration=duration
    )


def slot_items() -> list[Scheduleitem]:
    return list(
        Scheduleitem.objects.filter(schedulereason=Scheduleitem.REASON_AUTO).order_by("starttime")
    )


def test_fills_every_occurrence_up_to_the_horizon(video: Video, purpose: SchedulePurpose) -> None:
    make_slot(purpose)

    fill_next_weeks_agenda(now=NOW)

    items = slot_items()
    assert [item.starttime for item in items] == [FROZEN_OCCURRENCE, OPEN_OCCURRENCE]
    assert {item.video for item in items} == {video}
    # The item takes the video's duration, not the slot's.
    assert {item.duration for item in items} == {video.duration}


def test_todays_occurrence_counts_only_while_still_ahead(
    video: Video, purpose: SchedulePurpose
) -> None:
    """NOW is Thursday noon; a Thursday slot at 11:00 has passed and
    starts next week, one at 13:00 is still ahead and starts today."""
    passed = make_slot(purpose, day=3, start_time=time(11, 0))
    ahead = make_slot(purpose, day=3, start_time=time(13, 0))

    fill_next_weeks_agenda(now=NOW)

    starts = [item.starttime for item in slot_items()]
    assert datetime(2019, 6, 27, 13, tzinfo=OSLO) in starts
    assert datetime(2019, 6, 27, 11, tzinfo=OSLO) not in starts
    assert datetime(2019, 7, 4, 11, tzinfo=OSLO) in starts
    assert passed and ahead  # fixtures used


def test_does_nothing_without_slots(video: Video) -> None:
    fill_next_weeks_agenda(now=NOW)

    assert not Scheduleitem.objects.exists()


def test_skips_slots_without_a_purpose(video: Video) -> None:
    make_slot(None)

    fill_next_weeks_agenda(now=NOW)

    assert not Scheduleitem.objects.exists()


def test_skips_slots_whose_purpose_has_no_eligible_video(
    editor: User, organization: Organization, purpose: SchedulePurpose
) -> None:
    Video.objects.create(
        name="Too long for the slot",
        creator=editor,
        organization=organization,
        duration=timedelta(hours=2),
        proper_import=True,
    )
    make_slot(purpose, duration=timedelta(hours=1))

    fill_next_weeks_agenda(now=NOW)

    assert not Scheduleitem.objects.exists()


# --- what already-occupied airtime does ------------------------------------


def test_an_occupied_occurrence_is_skipped_but_the_others_still_fill(
    video: Video, purpose: SchedulePurpose
) -> None:
    make_slot(purpose)
    occupy(
        video,
        FROZEN_OCCURRENCE + timedelta(minutes=30),
        timedelta(minutes=10),
        Scheduleitem.REASON_ADMIN,
    )

    fill_next_weeks_agenda(now=NOW)

    assert [item.starttime for item in slot_items()] == [OPEN_OCCURRENCE]


def test_an_earlier_item_overrunning_into_the_slot_occupies_it(
    video: Video, purpose: SchedulePurpose
) -> None:
    """An item that begins before the slot and runs past its start
    occupies the airtime just as surely as one that begins inside it."""
    make_slot(purpose)
    occupy(
        video,
        OPEN_OCCURRENCE - timedelta(minutes=5),
        timedelta(minutes=10),
        Scheduleitem.REASON_USER,
    )

    fill_next_weeks_agenda(now=NOW)

    assert [item.starttime for item in slot_items()] == [FROZEN_OCCURRENCE]


def test_fills_a_slot_that_an_earlier_item_stops_short_of(
    video: Video, purpose: SchedulePurpose
) -> None:
    """Back-to-back is not a conflict: the bounds are half-open."""
    make_slot(purpose)
    occupy(
        video,
        OPEN_OCCURRENCE - timedelta(minutes=10),
        timedelta(minutes=10),
        Scheduleitem.REASON_USER,
    )

    fill_next_weeks_agenda(now=NOW)

    assert OPEN_OCCURRENCE in [item.starttime for item in slot_items()]


def test_displaces_jukebox_fillers_in_the_open_week(video: Video, purpose: SchedulePurpose) -> None:
    """A newly defined slot must not wait for airtime the jukebox got to
    first: outside the freeze boundary its fillers are deleted."""
    make_slot(purpose)
    displaced = occupy(
        video,
        OPEN_OCCURRENCE + timedelta(minutes=10),
        timedelta(minutes=10),
        Scheduleitem.REASON_JUKEBOX,
    )

    fill_next_weeks_agenda(now=NOW)

    assert OPEN_OCCURRENCE in [item.starttime for item in slot_items()]
    assert not Scheduleitem.objects.filter(pk=displaced.pk).exists()


def test_does_not_displace_jukebox_fillers_in_the_frozen_weeks(
    video: Video, purpose: SchedulePurpose
) -> None:
    """Inside the freeze boundary only genuinely empty airtime may be
    filled; the published week keeps the fillers it was published with."""
    make_slot(purpose)
    published = occupy(
        video,
        FROZEN_OCCURRENCE + timedelta(minutes=10),
        timedelta(minutes=10),
        Scheduleitem.REASON_JUKEBOX,
    )

    fill_next_weeks_agenda(now=NOW)

    assert [item.starttime for item in slot_items()] == [OPEN_OCCURRENCE]
    assert Scheduleitem.objects.filter(pk=published.pk).exists()


# --- provenance and the nightly re-pick -------------------------------------


def make_upload(editor: User, organization: Organization, name: str, minutes: int = 25) -> Video:
    """A fresh upload the `latest` strategy must prefer: it is created
    after the slot-video fixture, and `latest` goes by created_time."""
    return Video.objects.create(
        name=name,
        creator=editor,
        organization=organization,
        duration=timedelta(minutes=minutes),
        proper_import=True,
        uploaded_time=NOW,
    )


def test_placements_carry_their_slot(video: Video, purpose: SchedulePurpose) -> None:
    slot = make_slot(purpose)

    fill_next_weeks_agenda(now=NOW)

    assert {item.weekly_slot for item in slot_items()} == {slot}


def test_a_rerun_re_picks_the_open_week_when_the_purpose_answer_changed(
    editor: User, organization: Organization, video: Video, purpose: SchedulePurpose
) -> None:
    """The open week is a draft: a `latest` slot drafted early swaps to
    a video uploaded afterwards. The frozen week keeps what it has."""
    make_slot(purpose)
    fill_next_weeks_agenda(now=NOW)
    newer = make_upload(editor, organization, "Uploaded after drafting")

    fill_next_weeks_agenda(now=NOW)

    by_start = {item.starttime: item.video for item in slot_items()}
    assert by_start[FROZEN_OCCURRENCE] == video
    assert by_start[OPEN_OCCURRENCE] == newer


def test_a_rerun_with_an_unchanged_answer_leaves_the_placement_alone(
    video: Video, purpose: SchedulePurpose
) -> None:
    """Same pick, same row: no delete-and-recreate churn night to night."""
    make_slot(purpose)
    fill_next_weeks_agenda(now=NOW)
    original_pks = {item.pk for item in slot_items()}

    fill_next_weeks_agenda(now=NOW)

    assert {item.pk for item in slot_items()} == original_pks


def test_a_random_slot_is_not_re_rolled_night_after_night(
    editor: User, organization: Organization, video: Video
) -> None:
    """Only `latest` chases a better answer. `random` answers
    differently every call, so 'answer changed' would churn the draft
    nightly; a standing pick only falls when it becomes ineligible."""
    make_upload(editor, organization, "Another candidate")
    random_purpose = SchedulePurpose.objects.create(
        name="Random purpose",
        type=SchedulePurpose.TYPE.organization,
        strategy="random",
        organization=organization,
    )
    make_slot(random_purpose)
    fill_next_weeks_agenda(now=NOW)
    drafted = {(item.pk, item.video_id) for item in slot_items()}

    fill_next_weeks_agenda(now=NOW)

    assert {(item.pk, item.video_id) for item in slot_items()} == drafted


def test_a_drafted_video_that_became_ineligible_is_replaced(
    editor: User, organization: Organization, video: Video, purpose: SchedulePurpose
) -> None:
    """A pick the purpose can no longer stand by -- here the video lost
    its proper import -- gives way to a fresh choice."""
    make_slot(purpose)
    fill_next_weeks_agenda(now=NOW)
    replacement = make_upload(editor, organization, "Still eligible")
    video.proper_import = False
    video.save()

    fill_next_weeks_agenda(now=NOW)

    open_item = Scheduleitem.objects.get(starttime=OPEN_OCCURRENCE)
    assert open_item.video == replacement
    # The frozen week keeps the now-ineligible video: frozen is frozen.
    assert Scheduleitem.objects.get(starttime=FROZEN_OCCURRENCE).video == video


def test_a_longer_re_pick_displaces_fillers_that_packed_in_behind_the_draft(
    editor: User, organization: Organization, video: Video, purpose: SchedulePurpose
) -> None:
    make_slot(purpose, duration=timedelta(hours=1))
    fill_next_weeks_agenda(now=NOW)
    filler = occupy(
        video,
        OPEN_OCCURRENCE + timedelta(minutes=30),
        timedelta(minutes=10),
        Scheduleitem.REASON_JUKEBOX,
    )
    longer = make_upload(editor, organization, "Longer newer upload", minutes=55)

    fill_next_weeks_agenda(now=NOW)

    refreshed = Scheduleitem.objects.get(starttime=OPEN_OCCURRENCE)
    assert refreshed.video == longer
    assert not Scheduleitem.objects.filter(pk=filler.pk).exists()


def test_a_pre_provenance_item_is_deliberate_programming(
    video: Video, purpose: SchedulePurpose
) -> None:
    """Legacy REASON_AUTO rows have no slot recorded; the filler must
    treat them as untouchable, not adopt them."""
    make_slot(purpose)
    legacy = occupy(video, OPEN_OCCURRENCE, timedelta(minutes=25), Scheduleitem.REASON_AUTO)

    fill_next_weeks_agenda(now=NOW)

    legacy.refresh_from_db()
    assert legacy.weekly_slot is None
    assert Scheduleitem.objects.filter(starttime=OPEN_OCCURRENCE).count() == 1


def test_deleting_a_slot_strands_its_placements_as_deliberate_programming(
    video: Video, purpose: SchedulePurpose
) -> None:
    """SET_NULL: drafted items survive their slot's deletion, and a new
    slot on the same airtime treats them as foreign."""
    slot = make_slot(purpose)
    fill_next_weeks_agenda(now=NOW)
    slot.delete()

    survivors = slot_items()
    assert [item.starttime for item in survivors] == [FROZEN_OCCURRENCE, OPEN_OCCURRENCE]
    assert {item.weekly_slot for item in survivors} == {None}


# --- the entry points the cron actually calls -------------------------------


def test_management_command_runs_the_filler(
    monkeypatch: pytest.MonkeyPatch, video: Video, purpose: SchedulePurpose
) -> None:
    monkeypatch.setattr(weekly_slots.timezone, "now", lambda: NOW)
    make_slot(purpose)

    call_command("fill_next_weeks_agenda")

    assert len(slot_items()) == 2


def test_jukebox_management_command_fills_to_the_horizon(
    monkeypatch: pytest.MonkeyPatch, editor: User, organization: Organization
) -> None:
    monkeypatch.setattr(timezone, "now", lambda: NOW)
    organization.fkmember = True
    organization.save()
    Video.objects.create(
        name="Jukebox filler",
        creator=editor,
        organization=organization,
        duration=timedelta(hours=1),
        proper_import=True,
        is_filler=True,
    )

    call_command("fill_agenda_with_jukebox")

    items = Scheduleitem.objects.order_by("starttime")
    assert set(items.values_list("schedulereason", flat=True)) == {Scheduleitem.REASON_JUKEBOX}
    first, last = items.first(), items.last()
    assert first is not None and last is not None
    horizon = datetime(2019, 7, 15, tzinfo=OSLO)
    assert first.starttime >= NOW
    assert last.starttime < horizon
    # The horizon week is complete: the last filler ends within the last
    # hour-and-a-rounding-minute before it.
    assert last.airtime.upper > horizon - timedelta(minutes=62)
