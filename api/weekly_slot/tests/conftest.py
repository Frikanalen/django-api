from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from fk.models import (
    Organization,
    SlotSourceStrategy,
    SlotSourceType,
    User,
    Video,
    WeeklySlot,
    WeeklySlotSource,
)


@pytest.fixture
def member() -> User:
    return User.objects.create(email="weekly-slot-member@example.test")


@pytest.fixture
def member_client(member: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(member)
    return client


@pytest.fixture
def organization(member: User) -> Organization:
    editor = User.objects.create(email="weekly-slot-editor@example.test")
    organization = Organization.objects.create(
        name="Weekly slot organization",
        editor=editor,
        fkmember=True,
    )
    organization.members.add(member)
    return organization


@pytest.fixture
def source(organization: Organization) -> WeeklySlotSource:
    return WeeklySlotSource.objects.create(
        name="Organization uploads",
        type=SlotSourceType.ORGANIZATION,
        strategy=SlotSourceStrategy.LATEST,
        organization=organization,
    )


@pytest.fixture
def slot(organization: Organization, source: WeeklySlotSource) -> WeeklySlot:
    return WeeklySlot.objects.create(
        organization=organization,
        source=source,
        day=2,
        start_time="18:00",
        duration=timedelta(hours=1),
    )


@pytest.fixture
def video(member: User, organization: Organization) -> Video:
    return Video.objects.create(
        name="Member programme",
        creator=member,
        organization=organization,
        proper_import=True,
    )


@pytest.fixture
def staff() -> User:
    return User.objects.create(email="weekly-slot-admin@example.test", is_superuser=True)


@pytest.fixture
def staff_client(staff: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(staff)
    return client
