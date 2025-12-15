# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
import datetime

from django.urls import reverse
from django.test import TestCase


from fk.models import Scheduleitem


class WebPageTest(TestCase):
    fixtures = ["test.yaml"]

    def test_xmltv(self):
        r = self.client.get("/xmltv/2015/01/01")
        self.assertContains(r, '<title lang="no">tech video</title>', count=1)
        self.assertContains(r, '<title lang="no">dummy video</title>', count=1)
        self.assertContains(r, "<url>https://frikanalen.no/video/2/</url>", count=1)
        self.assertContains(r, "</programme>", count=2)


def rev(iterable):
    return list(reversed(iterable))


class ScheduleitemModelTests(TestCase):
    fixtures = ["test.yaml"]

    def _create_scheduleitem_at(self, datetime_iso: str):
        """Helper method to create a scheduleitem from an ISO 8601 datetime string."""
        return Scheduleitem.objects.create(
            video_id=1,
            duration=datetime.timedelta(10),
            schedulereason=Scheduleitem.REASON_LEGACY,
            starttime=(parse_to_datetime(datetime_iso)),
        )

    def test_by_day(self):
        items = [
            self._create_scheduleitem_at("2014-04-03T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T00:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T14:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-05T00:10:00+02:00"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date, days=1)
        self.assertEqual(rev(items[1:4]), list(by_day_items))

    def test_by_zero(self):
        items = [
            self._create_scheduleitem_at("2014-04-03T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-05T00:10:00+02:00"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date, days=1)
        self.assertEqual(rev(items[0:0]), list(by_day_items))

    def test_by_zero_surrounding(self):
        items = [
            self._create_scheduleitem_at("2014-04-03T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-05T00:10:00+02:00"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date, days=1, include_surrounding=True)
        self.assertEqual(rev(items), list(by_day_items))

    def test_by_day_only_one(self):
        items = [
            self._create_scheduleitem_at("2014-04-03T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-05T00:10:00+02:00"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date, days=1)
        self.assertEqual(rev(items[1:2]), list(by_day_items))

    def test_by_day_only_one_surrounding(self):
        items = [
            self._create_scheduleitem_at("2014-04-01T23:30:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-08T00:10:00+02:00"),
            self._create_scheduleitem_at("2014-04-09T07:10:00+02:00"),
        ]
        date = datetime.date(2014, 4, 8)
        by_day_items = Scheduleitem.objects.by_day(date, days=1, include_surrounding=True)
        self.assertEqual(rev(items[1:]), list(by_day_items))

    def test_by_day_surrounding(self):
        items = [
            self._create_scheduleitem_at("2014-04-03T23:31:00+02:00"),
            self._create_scheduleitem_at("2014-04-03T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T00:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T14:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-05T00:10:00+02:00"),
            self._create_scheduleitem_at("2014-04-05T00:20:00+02:00"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date, days=1, include_surrounding=True)
        self.assertEqual(rev(items[1:-1]), list(by_day_items))

    def test_by_day_more_days(self):
        items = [
            self._create_scheduleitem_at("2014-04-03T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T00:10:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-05T00:10:00+02:00"),
            self._create_scheduleitem_at("2014-04-05T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-06T00:10:00+02:00"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date, days=2)
        self.assertEqual(rev(items[1:-1]), list(by_day_items))

    def test_by_day_more_days_surrounding(self):
        items = [
            self._create_scheduleitem_at("2014-04-03T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-05T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-06T00:10:00+02:00"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date, days=2, include_surrounding=True)
        self.assertEqual(rev(items), list(by_day_items))

    def test_by_day_datetime(self):
        items = [
            self._create_scheduleitem_at("2014-04-03T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T12:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-04T23:50:00+02:00"),
            self._create_scheduleitem_at("2014-04-05T00:10:00+02:00"),
        ]
        dt = parse_to_datetime("2014-04-04T13:50:00+02:00")
        by_day_items = Scheduleitem.objects.by_day(dt, days=1)
        self.assertEqual(rev(items[1:-1]), list(by_day_items))


def parse_to_datetime(dt_str):
    """Parse ISO 8601 datetime string to timezone-aware datetime."""
    return datetime.datetime.fromisoformat(dt_str)


class APITest(TestCase):
    fixtures = ["test.yaml"]

    def test_api_root(self):
        r = self.client.get(reverse("api-root"))

        self.assertEqual(
            {
                "scheduleitems",
                "asrun",
                "category",
                "videofiles",
                "videos",
                "obtain-token",
                "jukebox-csv",
                "user",
                "organization",
                "user/register",
            },
            set(r.data.keys()),
        )

    def test_api_video_list(self):
        r = self.client.get(reverse("api-video-list"))

        self.assertEqual(
            ["unpublished video", "dummy video", "tech video"],
            [v["name"] for v in r.data["results"]],
        )

    def test_api_videofiles_list(self):
        r = self.client.get(reverse("api-videofile-list"))

        self.assertEqual(
            [
                "broken_video.mov",
                "unpublished_video.dv",
                "dummy_video.mov",
                "tech_video.mp4",
            ],
            [v["filename"] for v in r.data["results"]],
        )

    def test_api_scheduleitems_list(self):
        r = self.client.get(reverse("api-scheduleitem-list") + "?date=2014-12-31")

        self.assertEqual(
            ["tech video", "dummy video"], [v["video"]["name"] for v in r.data["results"]]
        )
        self.assertEqual([1, 2], [v["video"]["id"] for v in r.data["results"]])
        self.assertEqual(
            ["00:00:10.010000", "00:01:00"], [v["video"]["duration"] for v in r.data["results"]]
        )
        self.assertEqual(
            ["nuug_user@fake.com", "dummy_user@fake.com"],
            [v["video"]["creator"] for v in r.data["results"]],
        )
