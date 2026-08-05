import datetime
import random

from django.test import TestCase
from django.utils import timezone

from fk.models import Scheduleitem, Video

from . import views as agenda_views


def parse_to_datetime(dt_str):
    dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
    return timezone.make_aware(dt, timezone.get_current_timezone())


class FillJukeboxIntegrationTests(TestCase):
    fixtures = ["test.yaml"]

    def test_fills_in(self):
        Video.objects.create(
            name="video",
            creator_id=1,
            organization_id=1,
            duration=datetime.timedelta(minutes=60),
            proper_import=True,
            is_filler=True,
        )
        start_date = parse_to_datetime("2019-06-30 12:00")
        pre_count = Scheduleitem.objects.count()

        agenda_views.fill_agenda_with_jukebox(start_date, days=1)

        self.assertEqual(pre_count + 23, Scheduleitem.objects.count())

    def test_fills_in_only_where_it_can(self):
        Video.objects.create(
            name="video",
            creator_id=1,
            organization_id=1,
            duration=datetime.timedelta(minutes=60),
            proper_import=True,
            is_filler=True,
        )
        start_date = parse_to_datetime("2019-06-30 12:00")
        Scheduleitem.objects.create(
            video_id=1,
            starttime=start_date - datetime.timedelta(minutes=10),
            duration=datetime.timedelta(minutes=1),
            schedulereason=Scheduleitem.REASON_AUTO,
        )
        Scheduleitem.objects.create(
            video_id=2,
            starttime=start_date + datetime.timedelta(hours=6, minutes=0),
            duration=datetime.timedelta(minutes=60),
            schedulereason=Scheduleitem.REASON_AUTO,
        )
        Scheduleitem.objects.create(
            video_id=1,
            starttime=start_date + datetime.timedelta(hours=24, minutes=10),
            duration=datetime.timedelta(minutes=1),
            schedulereason=Scheduleitem.REASON_AUTO,
        )
        pre_count = Scheduleitem.objects.count()

        agenda_views.fill_agenda_with_jukebox(start_date, days=0.5)

        self.assertEqual(pre_count + 9, Scheduleitem.objects.count())


class FillJukeboxUnitTests(TestCase):
    start_date = parse_to_datetime("2019-06-30 12:00")

    @classmethod
    def _video(cls, video_id=None, minutes=60, **kwargs):
        video_id = video_id or random.randint(0, 1000)
        if "duration" not in kwargs:
            kwargs["duration"] = datetime.timedelta(minutes=minutes)
        return Video(
            id=video_id,
            name="id:%d, min:%d" % (video_id, minutes),
            creator_id=1,
            organization_id=1,
            proper_import=True,
            is_filler=True,
            **kwargs,
        )

    def test_two_videos_fills_time(self):
        videos = [
            self._video(video_id=1, minutes=2),
            self._video(video_id=2, minutes=3),
        ]

        end = self.start_date + datetime.timedelta(minutes=15)

        res = agenda_views._items_for_gap(self.start_date, end, videos)

        self.assertEqual([1, 2, 1, 2], [r["id"] for r in res])


class FillJukeboxGapTests(TestCase):
    """Covers the rounding and minimum-gap rules in `_items_for_gap`."""

    fixtures = ["test.yaml"]
    start_date = parse_to_datetime("2019-06-30 12:00")

    def test_short_gap_before_scheduled_item_is_left_empty(self):
        """
        Filling starts at 12:00:13 and an item occupies 12:02:27 to 12:03:27,
        leaving 12:01:00 to 12:02:00 free.  That gap is under MINIMUM_GAP_SECONDS,
        so nothing is placed in it; filling resumes on the whole minute after the
        scheduled item ends.
        """
        videos = [
            FillJukeboxUnitTests._video(
                video_id=1, duration=datetime.timedelta(minutes=1, seconds=1)
            ),
            FillJukeboxUnitTests._video(video_id=2, duration=datetime.timedelta(hours=1)),
            FillJukeboxUnitTests._video(video_id=3, duration=datetime.timedelta(seconds=50)),
        ]
        Scheduleitem.objects.create(
            video_id=1,
            starttime=self.start_date + datetime.timedelta(minutes=2, seconds=27),
            duration=datetime.timedelta(minutes=1),
            schedulereason=Scheduleitem.REASON_AUTO,
        )
        start = self.start_date + datetime.timedelta(seconds=13)
        end = self.start_date + datetime.timedelta(minutes=10, seconds=3)

        res = agenda_views._items_for_gap(start, end, videos)

        self.assertEqual([1, 3, 1, 3], [r["id"] for r in res])
        self.assertEqual(
            [self.start_date + datetime.timedelta(minutes=m) for m in (4, 6, 7, 9)],
            [r["starttime"] for r in res],
        )
