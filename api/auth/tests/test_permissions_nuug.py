import datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status

from api.auth.tests.permission_test import PermissionsTest
from fk.models import Scheduleitem, VideoFile


class NuugPermissionsTest(PermissionsTest):
    def test_nuug_user_reading_all_pages_from_root_expecting_status(self):
        pages = [
            ("asrun", status.HTTP_200_OK),
            ("category", status.HTTP_200_OK),
            ("jukebox-csv", status.HTTP_200_OK),
            ("user", status.HTTP_200_OK),
            ("scheduleitems", status.HTTP_200_OK),
            ("videofiles", status.HTTP_200_OK),
            ("videos", status.HTTP_200_OK),
            ("organization", status.HTTP_200_OK),
            ("user/register", status.HTTP_405_METHOD_NOT_ALLOWED),
        ]
        self.authenticate_nuug_user()
        self._helper_test_reading_all_pages_from_root(pages)

    def test_nuug_user_can_edit_profile(self):
        date_of_birth = datetime.date(year=1984, month=6, day=7)
        self.authenticate_nuug_user()

        r = self.client.get(reverse("api-user-detail"))
        self.assertEqual(200, r.status_code)
        r = self.client.patch(
            reverse("api-user-detail"),
            {
                "first_name": "Firstname",
                "last_name": "Lastname",
                "date_of_birth": date_of_birth,
                "email": "this_should_be_immutable@fake.com",
            },
            format="json",
        )
        self.assertEqual(200, r.status_code)
        # This will fail if email wasn't immutable, it should probably
        # return something else than 200 if I try to patch read-only
        # values but there you go
        u = get_user_model().objects.get(email="nuug_user@fake.com")
        self.assertEqual("Firstname", u.first_name)
        self.assertEqual("Lastname", u.last_name)
        self.assertEqual(date_of_birth, u.date_of_birth)
        # Uncomment when https://github.com/Frikanalen/frikanalen/issues/77 is fixed
        # self.assertEqual('Norway', u.userprofile.country)

    def authenticate_nuug_user(self):
        self._user_auth("nuug_user@fake.com")

    def test_nuug_user_can_edit_own_things(self):
        self.authenticate_nuug_user()

        thing_tests = [
            ("api-videofile-detail", VideoFile.objects.get(video__name="tech video"), "filename"),
        ]
        for url_name, obj, attr in thing_tests:
            r = self.client.patch(
                reverse(url_name, args=[obj.id]), {attr: "test fn"}, format="json"
            )
            self.assertEqual(status.HTTP_200_OK, r.status_code)
            self.assertEqual("test fn", r.data[attr])

    def test_nuug_user_can_only_see_own_upload_tokens(self):
        self.authenticate_nuug_user()
        self._get_upload_token_helper(
            VideoFile.objects.get(video__name="tech video"),
            200,
            {"upload_token": "deadbeef", "upload_url": settings.FK_UPLOAD_URL},
        )
        self._get_upload_token_helper(
            VideoFile.objects.get(video__name="dummy video"),
            403,
            {
                "type": "client_error",
                "errors": [
                    {
                        "code": "permission_denied",
                        "detail": "You do not have permission to perform this action.",
                    }
                ],
            },
        )

    def test_nuug_user_cannot_edit_nonowned_things(self):
        self.authenticate_nuug_user()
        thing_tests = [
            ("api-videofile-detail", VideoFile.objects.get(video__name="dummy video"), "filename"),
            (
                "api-scheduleitem-detail",
                Scheduleitem.objects.get(video__name="dummy video"),
                "default_name",
            ),
        ]
        for url_name, obj, attr in thing_tests:
            r = self.client.patch(reverse(url_name, args=[obj.id]), {attr: "test fn"})
            # Flexible check for error dict with possible 'attr': None
            self.assertEqual(r.data["type"], "client_error")
            self.assertEqual(len(r.data["errors"]), 1)
            error = r.data["errors"][0]
            self.assertEqual(error["code"], "permission_denied")
            self.assertEqual(error["detail"], "You do not have permission to perform this action.")
            if "attr" in error:
                self.assertIsNone(error["attr"])
            self.assertEqual(status.HTTP_403_FORBIDDEN, r.status_code)

    def test_nuug_user_can_create_videofile(self):
        self.authenticate_nuug_user()
        self.post_create(
            reverse("api-videofile-list"),
            {"video": 1, "format": "original", "filename": "test.mov"},
            status.HTTP_201_CREATED,
            {"id": 5, "video": 1, "filename": "test.mov"},
        )

    def test_nuug_user_can_create_video(self):
        self.authenticate_nuug_user()
        payload = {
            "name": "created test video",
            "duration": "01:2.3",
            "organization": 1,  # FIXME: Don't hardcode the org this way
            "categories": ["My Cat"],
        }
        expected = {
            "id": 5,
            "name": "created test video",
            "duration": "00:01:02.300000",
            "categories": ["My Cat"],
            "organization": 1,
            "creator": "nuug_user@fake.com",
        }
        self.post_create(reverse("api-video-list"), payload, status.HTTP_201_CREATED, expected)

    def test_nuug_user_can_create_scheduleitem(self):
        self.authenticate_nuug_user()
        url = reverse("api-scheduleitem-list") + "?date=20150101"
        payload = {
            "video": 1,
            "schedulereason": Scheduleitem.REASON_ADMIN,
            "starttime": "2015-01-01T11:00:00Z",
            "duration": "0:00:00.10",
        }
        expected = {"id": 3, "video": 1, "duration": "00:00:00.100000"}
        self.post_create(url, payload, status.HTTP_201_CREATED, expected)

    def test_nuug_user_cannot_create_asrun(self):
        self.authenticate_nuug_user()
        self.post_create(
            reverse("asrun-list"),
            {"video": 2, "playedAt": "2015-01-01 11:00:00Z"},
            status.HTTP_403_FORBIDDEN,
            {
                "type": "client_error",
                "errors": [
                    {
                        "code": "permission_denied",
                        "detail": "You do not have permission to perform this action.",
                    }
                ],
            },
        )
