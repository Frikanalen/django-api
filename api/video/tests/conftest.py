import pytest
from rest_framework.test import APIClient

from fk.models import Organization, User


@pytest.fixture
def editor() -> User:
    return User.objects.create(
        email="video-editor@example.test",
        first_name="Ada",
        last_name="Lovelace",
    )


@pytest.fixture
def organization(editor: User) -> Organization:
    organization = Organization.objects.create(name="Video test organization", editor=editor)
    organization.members.add(editor)
    return organization


@pytest.fixture
def editor_client(editor: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=editor)
    return client
