# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
import datetime

from django.urls import reverse
from django.test import TestCase
from django.utils import timezone


from fk.models import Scheduleitem
from fk.templatetags import vod


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

    def test_by_day(self):
        c = lambda x: create_scheduleitem(starttime=parse_to_datetime(x))
        items = [
            c("2014-04-03 23:50"),
            c("2014-04-04 00:50"),
            c("2014-04-04 14:50"),
            c("2014-04-04 23:50"),
            c("2014-04-05 00:10"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date)
        self.assertEqual(rev(items[1:4]), list(by_day_items))

    def test_by_zero(self):
        c = lambda x: create_scheduleitem(starttime=parse_to_datetime(x))
        items = [
            c("2014-04-03 23:50"),
            c("2014-04-05 00:10"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date)
        self.assertEqual(rev(items[0:0]), list(by_day_items))

    def test_by_zero_surrounding(self):
        c = lambda x: create_scheduleitem(starttime=parse_to_datetime(x))
        items = [
            c("2014-04-03 23:50"),
            c("2014-04-05 00:10"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date, surrounding=True)
        self.assertEqual(rev(items), list(by_day_items))

    def test_by_day_only_one(self):
        c = lambda x: create_scheduleitem(starttime=parse_to_datetime(x))
        items = [
            c("2014-04-03 23:50"),
            c("2014-04-04 23:50"),
            c("2014-04-05 00:10"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date)
        self.assertEqual(rev(items[1:2]), list(by_day_items))

    def test_by_day_only_one_surrounding(self):
        c = lambda x: create_scheduleitem(starttime=parse_to_datetime(x))
        items = [
            c("2014-04-01 23:30"),
            c("2014-04-04 23:50"),
            c("2014-04-08 00:10"),
            c("2014-04-09 07:10"),
        ]
        date = datetime.date(2014, 4, 8)
        by_day_items = Scheduleitem.objects.by_day(date, surrounding=True)
        self.assertEqual(rev(items[1:]), list(by_day_items))

    def test_by_day_surrounding(self):
        c = lambda x: create_scheduleitem(starttime=parse_to_datetime(x))
        items = [
            c("2014-04-03 23:31"),
            c("2014-04-03 23:50"),
            c("2014-04-04 00:50"),
            c("2014-04-04 14:50"),
            c("2014-04-04 23:50"),
            c("2014-04-05 00:10"),
            c("2014-04-05 00:20"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date, surrounding=True)
        self.assertEqual(rev(items[1:-1]), list(by_day_items))

    def test_by_day_more_days(self):
        c = lambda x: create_scheduleitem(starttime=parse_to_datetime(x))
        items = [
            c("2014-04-03 23:50"),
            c("2014-04-04 00:10"),
            c("2014-04-04 23:50"),
            c("2014-04-05 00:10"),
            c("2014-04-05 23:50"),
            c("2014-04-06 00:10"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date, days=2)
        self.assertEqual(rev(items[1:-1]), list(by_day_items))

    def test_by_day_more_days_surrounding(self):
        c = lambda x: create_scheduleitem(starttime=parse_to_datetime(x))
        items = [
            c("2014-04-03 23:50"),
            c("2014-04-04 23:50"),
            c("2014-04-05 23:50"),
            c("2014-04-06 00:10"),
        ]
        date = datetime.date(2014, 4, 4)
        by_day_items = Scheduleitem.objects.by_day(date, days=2, surrounding=True)
        self.assertEqual(rev(items), list(by_day_items))

    def test_by_day_datetime(self):
        c = lambda x: create_scheduleitem(starttime=parse_to_datetime(x))
        items = [
            c("2014-04-03 23:50"),
            c("2014-04-04 12:50"),
            c("2014-04-04 23:50"),
            c("2014-04-05 00:10"),
        ]
        dt = parse_to_datetime("2014-04-04 13:50")
        by_day_items = Scheduleitem.objects.by_day(dt)
        self.assertEqual(rev(items[1:-1]), list(by_day_items))


def create_scheduleitem(starttime=None):
    if starttime is None:
        starttime = timezone.now()
    return Scheduleitem.objects.create(
        video_id=1,
        duration=datetime.timedelta(10),
        schedulereason=Scheduleitem.REASON_LEGACY,
        starttime=starttime,
    )


def parse_to_datetime(dt_str):
    dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
    tz = dt.replace(tzinfo=timezone.get_current_timezone())
    return tz


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


class VodTemplateTag(TestCase):
    fixtures = ["test.yaml"]

    def test_found(self):
        for t in [(1, "tech video"), ("2", "dummy video")]:
            l = vod.show_vod_widget(t[0])
            self.assertEqual(l["video_error"], None)
            self.assertEqual(l["video"].id, int(t[0]))
            self.assertEqual(l["title"], t[1])

    def test_not_found(self):
        for t in [8, "8", 9]:
            l = vod.show_vod_widget(t)
            self.assertIn("not found", l["video_error"])
            self.assertIn("not found", l["title"])
            self.assertEqual(l["video"], None)

    def test_invalid(self):
        for t in ["", "a"]:
            l = vod.show_vod_widget(t)
            self.assertIn("Invalid video id", l["video_error"])
            self.assertIn("Invalid video id", l["title"])
            self.assertEqual(l["video"], None)
