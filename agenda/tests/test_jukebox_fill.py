"""
The jukebox filler: fill_agenda_with_jukebox and its gap arithmetic.

An hour-long video placed on a whole minute occupies 61 minutes of
schedule (its end is ceiled to the next whole minute), which is where
the odd-looking expected counts (23 per day) come from.

Every boundary here is a whole-minute rule, and the rules disagree with
each other by design: filling starts on the minute *after* the window
opens, a gap ends on the last whole minute *before* an existing item,
and a gap of exactly MINIMUM_GAP_SECONDS is too short to use.  The tests
below pin each of those edges separately, because a refactor that swaps
one ceil for a floor changes what actually goes to air.
"""

import datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command

from agenda import views as agenda_views
from fk.models import Organization, Scheduleitem, User, Video

pytestmark = pytest.mark.django_db

OSLO = ZoneInfo("Europe/Oslo")
START_DATE = datetime.datetime(2019, 6, 30, 12, tzinfo=OSLO)

# A half-hour window, small enough to keep the minute-by-minute cases cheap.
HALF_HOUR = 1 / 48


@pytest.fixture
def member_organization() -> Organization:
    editor = User.objects.create(email="jukebox-fill-editor@example.test")
    return Organization.objects.create(name="Jukebox fill org", fkmember=True, editor=editor)


def make_filler(
    organization: Organization,
    name: str = "Filler video",
    minutes: float = 60,
    creator: User | None = None,
    **overrides,
) -> Video:
    fields = {
        "name": name,
        "creator": creator or organization.editor,
        "organization": organization,
        "duration": datetime.timedelta(minutes=minutes),
        "proper_import": True,
        "is_filler": True,
        "has_tono_records": False,
        **overrides,
    }
    return Video.objects.create(**fields)


@pytest.fixture
def filler_video(member_organization: Organization) -> Video:
    return make_filler(member_organization)


@pytest.fixture
def short_filler(member_organization: Organization) -> Video:
    """A one-minute filler, so a mis-rounded boundary has room to show itself."""
    return make_filler(member_organization, name="Short filler", minutes=1)


def unsaved_video(video_id: int, minutes: int = 60, **kwargs) -> Video:
    """An unsaved Video: _items_for_gap only reads id and duration."""
    if "duration" not in kwargs:
        kwargs["duration"] = datetime.timedelta(minutes=minutes)
    return Video(
        id=video_id,
        name=f"id:{video_id}",
        proper_import=True,
        is_filler=True,
        **kwargs,
    )


def occupy(video: Video, starttime: datetime.datetime, duration: datetime.timedelta) -> None:
    Scheduleitem.objects.create(
        video=video,
        starttime=starttime,
        duration=duration,
        schedulereason=Scheduleitem.REASON_AUTO,
    )


def overlapping_pairs() -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Every adjacent pair in the schedule whose first item runs into the second."""
    items = list(Scheduleitem.objects.order_by("starttime"))
    return [(a.starttime, b.starttime) for a, b in pairwise(items) if a.endtime() > b.starttime]


def test_fills_a_whole_day(filler_video: Video) -> None:
    agenda_views.fill_agenda_with_jukebox(START_DATE, days=1)

    assert Scheduleitem.objects.count() == 23
    assert set(Scheduleitem.objects.values_list("schedulereason", flat=True)) == {
        Scheduleitem.REASON_JUKEBOX
    }


def test_fills_in_only_where_it_can(filler_video: Video) -> None:
    occupy(
        filler_video,
        START_DATE - datetime.timedelta(minutes=10),
        datetime.timedelta(minutes=1),
    )
    occupy(
        filler_video,
        START_DATE + datetime.timedelta(hours=6),
        datetime.timedelta(minutes=60),
    )
    occupy(
        filler_video,
        START_DATE + datetime.timedelta(hours=24, minutes=10),
        datetime.timedelta(minutes=1),
    )
    pre_count = Scheduleitem.objects.count()

    agenda_views.fill_agenda_with_jukebox(START_DATE, days=0.5)

    assert Scheduleitem.objects.count() == pre_count + 9


def test_two_videos_alternate_to_fill_the_time(db) -> None:
    videos = [unsaved_video(1, minutes=2), unsaved_video(2, minutes=3)]
    end = START_DATE + datetime.timedelta(minutes=15)

    res = agenda_views._items_for_gap(START_DATE, end, videos)

    assert [r["id"] for r in res] == [1, 2, 1, 2]


def test_short_gap_before_scheduled_item_is_left_empty(filler_video: Video) -> None:
    """
    Covers the rounding and minimum-gap rules in `_items_for_gap`.

    Filling starts at 12:00:13 and an item occupies 12:02:27 to 12:03:27,
    leaving 12:01:00 to 12:02:00 free.  That gap is under MINIMUM_GAP_SECONDS,
    so nothing is placed in it; filling resumes on the whole minute after the
    scheduled item ends.
    """
    videos = [
        unsaved_video(1, duration=datetime.timedelta(minutes=1, seconds=1)),
        unsaved_video(2, duration=datetime.timedelta(hours=1)),
        unsaved_video(3, duration=datetime.timedelta(seconds=50)),
    ]
    occupy(
        filler_video,
        START_DATE + datetime.timedelta(minutes=2, seconds=27),
        datetime.timedelta(minutes=1),
    )
    start = START_DATE + datetime.timedelta(seconds=13)
    end = START_DATE + datetime.timedelta(minutes=10, seconds=3)

    res = agenda_views._items_for_gap(start, end, videos)

    assert [r["id"] for r in res] == [1, 3, 1, 3]
    assert [r["starttime"] for r in res] == [
        START_DATE + datetime.timedelta(minutes=m) for m in (4, 6, 7, 9)
    ]


# --- what actually reaches the database -----------------------------------


def test_saved_items_carry_the_start_and_duration_that_were_planned(filler_video: Video) -> None:
    """
    The whole-day count above says nothing about *where* the items land.
    An hour-long video starting on a whole minute is followed 61 minutes
    later by the next one, and the first lands on the minute after the
    window opens -- not on the window's own minute.
    """
    planned = agenda_views.fill_agenda_with_jukebox(START_DATE, days=1)

    items = list(Scheduleitem.objects.order_by("starttime"))
    assert [item.starttime for item in items] == [
        START_DATE + datetime.timedelta(minutes=1 + 61 * n) for n in range(23)
    ]
    assert {item.duration for item in items} == {filler_video.duration}
    assert [entry["starttime"] for entry in planned] == [item.starttime for item in items]


def test_filling_starts_on_the_minute_after_an_off_minute_start(filler_video: Video) -> None:
    """`ceil_minute`, not `floor_minute`: a start mid-minute rounds forward."""
    planned = agenda_views.fill_agenda_with_jukebox(
        START_DATE + datetime.timedelta(seconds=37), days=1
    )

    assert planned[0]["starttime"] == START_DATE + datetime.timedelta(minutes=1)


def test_every_filler_in_the_pool_gets_used(member_organization: Organization) -> None:
    """
    The pool is drawn in random order (`order_by("?")`), so the sequence
    is not pinned -- only that both videos are drawn and that neither is
    played twice in a row.
    """
    first = make_filler(member_organization, name="First filler")
    second = make_filler(member_organization, name="Second filler")

    agenda_views.fill_agenda_with_jukebox(START_DATE, days=1)

    played = list(Scheduleitem.objects.order_by("starttime").values_list("video_id", flat=True))
    assert len(played) == 23
    assert set(played) == {first.id, second.id}
    assert all(a != b for a, b in pairwise(played))


# --- boundaries against already-scheduled programming ----------------------


def test_the_jukebox_never_overlaps_an_existing_item(short_filler: Video) -> None:
    """
    A gap ends on the last whole minute *before* an existing item, never
    the one after.  An item starting at 12:11:30 makes 12:11 unusable
    even though a one-minute filler would nominally fit there.
    """
    occupy(
        short_filler,
        START_DATE + datetime.timedelta(minutes=11, seconds=30),
        datetime.timedelta(minutes=30),
    )

    agenda_views.fill_agenda_with_jukebox(START_DATE, days=HALF_HOUR)

    assert overlapping_pairs() == []


@pytest.mark.parametrize(
    ("occupied_minute", "expect_filled"),
    [
        pytest.param(6, False, id="exactly-300s-is-too-short"),
        pytest.param(7, True, id="360s-is-used"),
    ],
)
def test_the_minimum_gap_boundary_is_exclusive(
    short_filler: Video, occupied_minute: int, expect_filled: bool
) -> None:
    """`gap > MINIMUM_GAP_SECONDS`: a gap of exactly five minutes is left empty."""
    occupied_at = START_DATE + datetime.timedelta(minutes=occupied_minute)
    occupy(short_filler, occupied_at, datetime.timedelta(minutes=30))

    agenda_views.fill_agenda_with_jukebox(START_DATE, days=HALF_HOUR)

    filled = Scheduleitem.objects.filter(
        starttime__lt=occupied_at, schedulereason=Scheduleitem.REASON_JUKEBOX
    ).exists()
    assert filled is expect_filled


def test_a_second_run_over_the_same_window_adds_nothing(filler_video: Video) -> None:
    """
    The cron runs nightly over windows that overlap the previous night's,
    so a rerun must not double-book: its own items leave only sub-minimum
    gaps behind.
    """
    agenda_views.fill_agenda_with_jukebox(START_DATE, days=1)
    after_first_run = Scheduleitem.objects.count()

    agenda_views.fill_agenda_with_jukebox(START_DATE, days=1)

    assert Scheduleitem.objects.count() == after_first_run
    assert overlapping_pairs() == []


# --- who is eligible -------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"is_filler": False}, id="not-a-filler"),
        pytest.param({"has_tono_records": True}, id="tono-encumbered"),
        pytest.param({"proper_import": False}, id="not-properly-imported"),
    ],
)
def test_ineligible_videos_are_never_scheduled(
    member_organization: Organization, short_filler: Video, overrides: dict
) -> None:
    """
    The filler draws from `Video.objects.fillers()`, so the eligibility
    rules apply here exactly as they do to the CSV feed.  An eligible
    video shares the pool so that the run has something to schedule.
    """
    make_filler(member_organization, name="Ineligible", minutes=1, **overrides)

    agenda_views.fill_agenda_with_jukebox(START_DATE, days=HALF_HOUR)

    assert set(Scheduleitem.objects.values_list("video_id", flat=True)) == {short_filler.id}


def test_videos_from_non_member_organizations_are_never_scheduled(short_filler: Video) -> None:
    editor = User.objects.create(email="jukebox-non-member@example.test")
    non_member = Organization.objects.create(name="Non-member org", fkmember=False, editor=editor)
    make_filler(non_member, name="Non-member filler", minutes=1)

    agenda_views.fill_agenda_with_jukebox(START_DATE, days=HALF_HOUR)

    assert set(Scheduleitem.objects.values_list("video_id", flat=True)) == {short_filler.id}


# --- degenerate pools ------------------------------------------------------


def test_an_empty_filler_pool_schedules_nothing(db) -> None:
    """
    With nothing eligible to draw from, the run is a no-op.  It used to
    raise AttributeError -- `next_vid` returns None and the caller
    dereferenced it before its own guard -- which killed the nightly
    cron whenever the pool happened to be empty.
    """
    assert agenda_views.fill_agenda_with_jukebox(START_DATE, days=1) == []
    assert Scheduleitem.objects.count() == 0


def test_a_filler_longer_than_the_window_leaves_it_empty(
    member_organization: Organization,
) -> None:
    make_filler(member_organization, name="Too long", minutes=48 * 60)

    agenda_views.fill_agenda_with_jukebox(START_DATE, days=1)

    assert Scheduleitem.objects.count() == 0


def test_a_zero_length_filler_is_never_scheduled(member_organization: Organization) -> None:
    """
    A video of non-positive length cannot move `current_time` forward, so
    it is screened out of the pool.  A zero-length one used to be
    scheduled once a minute for the whole window.

    Negative lengths belonged to this case too -- they walked the clock
    backwards and looped until the process died -- but they can no longer
    reach the database at all; see
    `fk/tests/test_duration_constraints.py`.  The screen stays `<= 0`
    rather than `== 0` because what the filler needs is a duration that
    actually advances the clock, not merely a non-negative one.
    """
    make_filler(member_organization, name="Zero length", minutes=0)

    assert agenda_views.fill_agenda_with_jukebox(START_DATE, days=HALF_HOUR) == []
    assert Scheduleitem.objects.count() == 0


def test_a_positive_filler_shorter_than_a_minute_still_advances(
    member_organization: Organization,
) -> None:
    """The screening is on non-positive length only: sub-minute videos still play."""
    make_filler(member_organization, name="Thirty seconds", minutes=0.5)

    agenda_views.fill_agenda_with_jukebox(START_DATE, days=HALF_HOUR)

    starts = list(Scheduleitem.objects.order_by("starttime").values_list("starttime", flat=True))
    assert starts == [START_DATE + datetime.timedelta(minutes=m) for m in range(1, 30)]


# --- the entry point the cron actually calls -------------------------------


def test_the_management_command_fills_two_days_from_now(
    monkeypatch: pytest.MonkeyPatch, filler_video: Video
) -> None:
    """
    `fill_agenda_with_jukebox` is invoked by a nightly CronJob with no
    arguments; the command supplies days=2 and lets the view default the
    start to the current time.
    """
    monkeypatch.setattr(agenda_views.timezone, "now", lambda: START_DATE)

    call_command("fill_agenda_with_jukebox")

    starts = list(Scheduleitem.objects.order_by("starttime").values_list("starttime", flat=True))
    assert starts == [START_DATE + datetime.timedelta(minutes=1 + 61 * n) for n in range(47)]
