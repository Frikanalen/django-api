from django.conf import settings
from django.urls import reverse
from rest_framework import status

from api.auth.tests.permission_test import PermissionsTest
from fk.models import Scheduleitem, VideoFile


class StaffPermissionsTest(PermissionsTest):
    def authenticate_staff(self):
        self._user_auth("staff_user@fake.com")

    def test_staff_user_can_create_videofile(self):
        self.authenticate_staff()
        self.post_create(
            reverse("api-videofile-list"),
            {"video": 1, "format": "original", "filename": "test.mov"},
            status.HTTP_201_CREATED,
            {"id": 5, "video": 1, "filename": "test.mov"},
        )

    def test_staff_user_can_create_scheduleitem(self):
        self.authenticate_staff()
        url = reverse("api-scheduleitem-list") + "?date=20150101"
        payload = {
            "video": 1,
            "schedulereason": Scheduleitem.REASON_ADMIN,
            "starttime": "2015-01-01T11:00:00Z",
            "duration": "0:00:13.10",
        }
        expected = {"id": 3, "video": 1, "duration": "00:00:13.100000"}
        self.post_create(url, payload, status.HTTP_201_CREATED, expected)

    def test_staff_user_can_create_asrun(self):
        self.authenticate_staff()
        self.post_create(
            reverse("asrun-list"),
            {"video": 2, "played_at": "2015-01-01T11:00:00+00:00"},
            status.HTTP_201_CREATED,
            {"id": 3, "video": 2, "playout": "main"},
        )

    def _assert_matches(self, actual_body: dict, expected_body: dict):
        self.assertEqual(
            expected_body,
            {
                expected_key: actual_body[expected_key]
                for expected_key in list(expected_body.keys())
            },
        )

    def test_staff_user_can_edit_nonowned_things(self):
        self.authenticate_staff()
        thing_tests = [
            ("api-videofile-detail", VideoFile.objects.get(video__name="tech video"), "filename"),
        ]
        for url_name, obj, attr in thing_tests:
            r = self.client.patch(
                reverse(url_name, args=[obj.id]), {attr: "test fn"}, format="json"
            )
            self.assertEqual(status.HTTP_200_OK, r.status_code)
            self.assertEqual("test fn", r.data[attr])

    def test_staff_user_can_see_all_upload_tokens(self):
        self.authenticate_staff()
        self._get_upload_token_helper(
            VideoFile.objects.get(video__name="tech video"),
            200,
            {"upload_token": "deadbeef", "upload_url": settings.FK_UPLOAD_URL},
        )
