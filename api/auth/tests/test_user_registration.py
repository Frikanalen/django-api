from django.urls import reverse
from rest_framework import status

from api.auth.tests.permission_test import PermissionsTest


class UserRegistrationTest(PermissionsTest):
    users = {
        "valid": {
            "email": "foo@example.com",
            "password": "foo",
            "first_name": "John",
            "last_name": "Smith",
            "date_of_birth": "2020-02-01",
        },
        "invalid_email": {
            "email": "fooexample.com",
            "password": "foo",
            "first_name": "John",
            "last_name": "Smith",
            "date_of_birth": "2020-02-01",
        },
    }

    def test_creating_new_user(self):
        r = self.client.post(reverse("api-user-create"), self.users["valid"], format="json")
        self.assertEqual(status.HTTP_201_CREATED, r.status_code)

    def test_logging_in_new_user(self):
        r = self.client.post(reverse("api-user-create"), self.users["valid"], format="json")
        self.assertEqual(status.HTTP_201_CREATED, r.status_code)
        login_successful = self.client.login(
            email=self.users["valid"]["email"], password=self.users["valid"]["password"]
        )
        self.assertEqual(login_successful, True)

    def test_duplicate_user_fails(self):
        r = self.client.post(reverse("api-user-create"), self.users["valid"], format="json")
        self.assertEqual(status.HTTP_201_CREATED, r.status_code)

        r = self.client.post(reverse("api-user-create"), self.users["valid"], format="json")
        self.assertEqual(status.HTTP_400_BAD_REQUEST, r.status_code)

    def test_invalid_email_fails(self):
        r = self.client.post(reverse("api-user-create"), self.users["invalid_email"], format="json")
        self.assertEqual(status.HTTP_400_BAD_REQUEST, r.status_code)

    def test_fails_without_mandatory_fields(self):
        for missing_mandatory_field in ["email", "first_name", "last_name"]:
            user_missing_field = dict(self.users["valid"])
            del user_missing_field[missing_mandatory_field]
            r = self.client.post(reverse("api-user-create"), user_missing_field, format="json")
            self.assertEqual(status.HTTP_400_BAD_REQUEST, r.status_code)
