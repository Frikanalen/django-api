# Copyright (c) 2012-2013 Benjamin Bruheim <grolgh@gmail.com>
# This file is covered by the LGPLv3 or later, read COPYING for details.
from django.test import TestCase
from django.urls import reverse


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
