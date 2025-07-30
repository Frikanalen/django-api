from django.urls import reverse
from rest_framework import status

from api.auth.tests.permission_test import PermissionsTest


class AnonymousPermissionsTest(PermissionsTest):
    def test_anonymous_does_not_have_token(self):
        r = self.client.get(reverse("api-token-auth"))
        error_msg = {"detail": "Authentication credentials were not provided."}
        self.assertEqual(status.HTTP_401_UNAUTHORIZED, r.status_code)
        self.assertEqual(error_msg, r.data)

    def test_anonymous_can_list_videofiles(self):
        """
        Will list all videofiles even where parent video not proper_import
        """
        r = self.client.get(reverse("api-videofile-list"))
        video_ids = [v["id"] for v in r.data["results"]]
        self.assertEqual(video_ids, [4, 3, 2, 1])
        self.assertEqual(status.HTTP_200_OK, r.status_code)

    def test_anonymous_can_list_scheduleitem(self):
        """
        Will list all videofiles even where parent video not proper_import
        """
        r = self.client.get(reverse("api-scheduleitem-list") + "?date=2015-01-01")
        videos = [v["video"]["name"] for v in r.data["results"]]
        self.assertEqual(videos, ["tech video", "dummy video"])
        self.assertEqual(status.HTTP_200_OK, r.status_code)

    def test_anonymous_can_detail_video(self):
        """
        Can view every video without restriction
        """
        videos = [
            (1, "tech video"),
            (2, "dummy video"),
            (3, "unpublished video"),
            (4, "broken video"),
        ]
        for id, name in videos:
            r = self.client.get(
                reverse(
                    "api-video-detail",
                    args=(id,),
                )
            )
            self.assertEqual(status.HTTP_200_OK, r.status_code)
            self.assertEqual(r.data["name"], name)

    def test_anonymous_can_not_detail_video_upload_token(self):
        """
        Can not see upload_token
        """

        self._expect_status(
            status.HTTP_401_UNAUTHORIZED, reverse("api-video-upload-token-detail", args=(1,))
        )

    def test_anonymous_can_list_category(self):
        """
        Will list all category items
        """

        r = self.client.get(reverse("category-list"))
        self._check_status(status.HTTP_200_OK, r.status_code)
        results = r.json()["results"]
        self.assertEqual(["My Cat", "Second Category"], [i["name"] for i in results])

    def test_anonymous_can_list_asrun(self):
        """
        Will list all asrun log items
        """
        r = self.client.get(reverse("asrun-list"))
        self.assertEqual(status.HTTP_200_OK, r.status_code)
        results = r.data["results"]
        self.assertEqual(
            [(2, 1, "2015-"), (1, 1, "2014-")],
            [(i["id"], i["video"], i["played_at"][:5]) for i in results],
        )

    def _ensure_forbidden(self, url: str, data: dict | None = None):
        res = self.client.post(url, data=data or {})
        self.assertEqual(status.HTTP_401_UNAUTHORIZED, res.status_code)
        self.assertEqual(res.json()["detail"], "Authentication credentials were not provided.")

    def test_anonymous_cannot_mutate(self):
        self._ensure_forbidden(reverse("api-video-list"))
        self._ensure_forbidden(reverse("api-videofile-list"))
        self._ensure_forbidden(reverse("api-scheduleitem-list"))
        self._ensure_forbidden(reverse("asrun-list"))
        self._ensure_forbidden(reverse("category-list"))
        self._ensure_forbidden(reverse("api-video-detail", args=(1,)))
        self._ensure_forbidden(reverse("api-videofile-detail", args=(1,)))
        self._ensure_forbidden(reverse("api-scheduleitem-detail", args=(1,)))
        self._ensure_forbidden(reverse("asrun-detail", args=(1,)))
        self._ensure_forbidden(reverse("api-token-auth"))

    def test_anonymous_reading_all_pages_from_root_expecting_status(self):
        self._expect_status(status.HTTP_200_OK, reverse("asrun-list"))
        self._expect_status(status.HTTP_200_OK, reverse("category-list"))
        self._expect_status(status.HTTP_200_OK, reverse("jukebox-csv"))
        self._expect_status(status.HTTP_200_OK, reverse("api-scheduleitem-list"))
        self._expect_status(status.HTTP_200_OK, reverse("api-videofile-list"))
        self._expect_status(status.HTTP_200_OK, reverse("api-video-list"))
        self._expect_status(status.HTTP_200_OK, reverse("api-organization-list"))
        self._expect_status(status.HTTP_401_UNAUTHORIZED, reverse("api-user-detail"))
        self._expect_status(status.HTTP_405_METHOD_NOT_ALLOWED, reverse("api-user-create"))

    def test_anonymous_can_list_videos(self):
        """
        Will list all videos except ones without proper_import
        """
        r = self.client.get(reverse("api-video-list"))
        videos = [v["name"] for v in r.data["results"]]
        self.assertEqual(
            videos,
            [
                "unpublished video",
                "dummy video",
                "tech video",
            ],
        )
        self.assertEqual(status.HTTP_200_OK, r.status_code)
