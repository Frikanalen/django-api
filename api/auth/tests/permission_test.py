from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from fk.models import User


class PermissionsTest(APITestCase):
    fixtures = ["test.yaml"]
    client: APIClient

    def _user_auth(self, email):
        """
        Look up the user by email and set the Authorization header
        so downstream requests run as that user.
        """
        user = User.objects.get(email=email)
        self.client.credentials(HTTP_AUTHORIZATION="Token " + user.auth_token.key)

    def _expect_status(self, code: int, url: str, data: dict | None = None):
        res = self.client.get(url, data=data or {})
        self.assertEqual(code, res.status_code)
        return res

    def _helper_test_reading_all_pages_from_root(self, pages):
        root_response = self.client.get(reverse("api-root"))
        self.assertEqual(status.HTTP_200_OK, root_response.status_code)
        # Every page exists
        self.assertEqual(set(p[0] for p in pages), set(root_response.data.keys()))
        for name, code in pages:
            url = root_response.data[name]
            page_response = self.client.get(url)
            self.assertEqual(
                code,
                page_response.status_code,
                "{} status is {} expected {}".format(name, page_response.status_code, code),
            )

    def post_create(self, url: str, obj: dict, exp_status: int, expected_body: dict | None = None):
        res = self.client.post(url, obj, format="json")
        self._check_status(exp_status, res.status_code)

        if expected_body is not None:
            response_body = res.json()
            self._check_body(expected_body, response_body)

    def _check_status(self, exp_status: int, actual_status: int):
        self.assertEqual(
            exp_status, actual_status, f"Expected status {exp_status} but got {actual_status}"
        )

    def _check_body(self, expected_body: dict, actual_body: dict):
        self.assertEqual(
            expected_body,
            {expected: actual_body[expected] for expected in list(expected_body.keys())},
        )

    def _get_upload_token_helper(self, obj, status, data):
        r = self.client.get(reverse("api-video-upload-token-detail", args=[obj.id]))
        self.assertEqual(data, r.data)
        self.assertEqual(status, r.status_code)
