import datetime
from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from fk.models import Organization, User
from programming.models import Recipe
from programming.services import uploads_recipe
from scheduling.models import RecurringSlot


@pytest.fixture
def member() -> User:
    return User.objects.create(email="slot-member@example.test")


@pytest.fixture
def member_client(member: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(member)
    return client


@pytest.fixture
def organization(member: User) -> Organization:
    editor = User.objects.create(email="slot-editor@example.test")
    organization = Organization.objects.create(
        name="Slot organization",
        editor=editor,
        fkmember=True,
    )
    organization.members.add(member)
    return organization


@pytest.fixture
def recipe(organization: Organization) -> Recipe:
    return uploads_recipe(organization)


@pytest.fixture
def slot(organization: Organization, recipe: Recipe) -> RecurringSlot:
    return RecurringSlot.objects.create(
        organization=organization,
        recipe=recipe,
        weekdays=[2],
        start_time="18:00",
        duration=timedelta(hours=1),
        starts_on=datetime.date(2026, 1, 1),
    )


@pytest.fixture
def staff() -> User:
    return User.objects.create(email="slot-admin@example.test", is_superuser=True)


@pytest.fixture
def staff_client(staff: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(staff)
    return client
