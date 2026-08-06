from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from fk.models import Organization, Scheduleitem, User, Video


@pytest.fixture
def now_in_the_drafting_week(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Pin the clock so the 2015-01-01 fixtures land in the *open*
    broadcast week: with now in the week of Mon 2014-12-15, the freeze
    boundary is Mon 2014-12-29 and the open week runs through Sunday
    2015-01-04 (see agenda.scheduling.policy). Modify-path tests need
    this; the read path never consults the freeze."""
    fixed_now = datetime(2014, 12, 17, 12, tzinfo=ZoneInfo("Europe/Oslo"))
    monkeypatch.setattr(timezone, "now", lambda: fixed_now)
    return fixed_now


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
