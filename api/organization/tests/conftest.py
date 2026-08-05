import pytest
from rest_framework.test import APIClient

from fk.models import Organization, User


@pytest.fixture
def editor() -> User:
    return User.objects.create(
        email="organization-editor@example.test",
        first_name="Ada",
        last_name="Lovelace",
        phone_number="+4741234567",
    )


@pytest.fixture
def editor_client(editor: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=editor)
    return client


@pytest.fixture
def organization(editor: User) -> Organization:
    return Organization.objects.create(
        name="Organization test group",
        homepage="https://example.test",
        description="An organization used by the API tests.",
        postal_address="Postboks 1",
        street_address="Testgata 2",
        fkmember=True,
        editor=editor,
    )
