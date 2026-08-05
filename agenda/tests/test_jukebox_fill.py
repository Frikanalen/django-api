"""
The jukebox filler: fill_agenda_with_jukebox and its gap arithmetic.

An hour-long video placed on a whole minute occupies 61 minutes of
schedule (its end is ceiled to the next whole minute), which is where
the odd-looking expected counts (23 per day) come from.
"""

import datetime
from zoneinfo import ZoneInfo

import pytest

from agenda import views as agenda_views
from fk.models import Organization, Scheduleitem, User, Video

pytestmark = pytest.mark.django_db

OSLO = ZoneInfo("Europe/Oslo")
START_DATE = datetime.datetime(2019, 6, 30, 12, tzinfo=OSLO)


@pytest.fixture
def member_organization() -> Organization:
    editor = User.objects.create(email="jukebox-fill-editor@example.test")
    return Organization.objects.create(name="Jukebox fill org", fkmember=True, editor=editor)


@pytest.fixture
def filler_video(member_organization: Organization) -> Video:
    return Video.objects.create(
        name="Filler video",
        creator=member_organization.editor,
        organization=member_organization,
        duration=datetime.timedelta(minutes=60),
        proper_import=True,
        is_filler=True,
    )


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
