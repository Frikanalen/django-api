from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from rest_framework.test import APIClient

from fk.models import Organization, Scheduleitem, User, Video


@pytest.fixture
def staff_user(db) -> User:
    return User.objects.create(
        email="schedule-admin@example.test",
        is_superuser=True,
    )


@pytest.fixture
def authenticated_client(staff_user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=staff_user)
    return client


@pytest.fixture
def organization(staff_user: User) -> Organization:
    return Organization.objects.create(name="Schedule test organization", editor=staff_user)


@pytest.fixture
def video(staff_user: User, organization: Organization) -> Video:
    return Video.objects.create(
        creator=staff_user,
        name="Schedule test video",
        organization=organization,
        proper_import=True,
    )


@pytest.fixture
def schedule_item_factory(video: Video) -> Callable[..., Scheduleitem]:
    def create(
        *,
        starttime: datetime,
        duration: timedelta = timedelta(hours=1),
    ) -> Scheduleitem:
        return Scheduleitem.objects.create(
            video=video,
            starttime=starttime,
            duration=duration,
            schedulereason=Scheduleitem.REASON_LEGACY,
        )

    return create
