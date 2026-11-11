import datetime
from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from fk.models import Organization, User
from scheduling.models import FillRule, RecurringSlot


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
def rule(organization: Organization) -> FillRule:
    return FillRule.objects.create(
        name="Organization uploads",
        organization=organization,
        pool=FillRule.Pool.ORGANIZATION,
        ordering=FillRule.Ordering.LATEST,
    )


@pytest.fixture
def slot(organization: Organization, rule: FillRule) -> RecurringSlot:
    return RecurringSlot.objects.create(
        organization=organization,
        default_fill_rule=rule,
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
