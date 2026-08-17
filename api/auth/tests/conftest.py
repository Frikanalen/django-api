from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from fk.models import (
    AsRun,
    Category,
    FileFormat,
    Organization,
    Scheduleitem,
    User,
    Video,
    VideoFile,
)


@pytest.fixture
def now_in_the_drafting_week(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Pin the clock so the 2015-01-01 scheduleitem fixture lands in the
    open broadcast week (freeze boundary Mon 2014-12-29, see
    agenda.scheduling.policy) -- the permission matrix is about *who*
    may modify, so the freeze must not answer first."""
    fixed_now = datetime(2014, 12, 17, 12, tzinfo=ZoneInfo("Europe/Oslo"))
    monkeypatch.setattr(timezone, "now", lambda: fixed_now)
    return fixed_now


@pytest.fixture
def staff_user(db) -> User:
    # User.is_staff is a read-only property aliasing is_superuser.
    return User.objects.create(email="matrix-staff@example.test", is_superuser=True)


@pytest.fixture
def member(db) -> User:
    return User.objects.create(
        email="matrix-member@example.test",
        identity_confirmed=True,
    )


@pytest.fixture
def outsider(db) -> User:
    return User.objects.create(email="matrix-outsider@example.test")


@pytest.fixture
def organization(member: User) -> Organization:
    organization = Organization.objects.create(
        name="Matrix organization", fkmember=True, editor=member
    )
    organization.members.add(member)
    return organization


@pytest.fixture
def video(member: User, organization: Organization) -> Video:
    return Video.objects.create(
        name="Matrix video",
        creator=member,
        organization=organization,
        duration=timedelta(hours=1),
        proper_import=True,
    )


@pytest.fixture
def file_format(db) -> FileFormat:
    return FileFormat.objects.create(fsname="original")


@pytest.fixture
def videofile(video: Video, file_format: FileFormat) -> VideoFile:
    return VideoFile.objects.create(video=video, format=file_format, filename="matrix.mp4")


@pytest.fixture
def scheduleitem(video: Video) -> Scheduleitem:
    return Scheduleitem.objects.create(
        video=video,
        starttime=datetime(2015, 1, 1, 10, tzinfo=UTC),
        duration=timedelta(hours=1),
        schedulereason=Scheduleitem.REASON_ADMIN,
    )


@pytest.fixture
def asrun(video: Video) -> AsRun:
    return AsRun.objects.create(video=video, played_at=datetime(2015, 1, 1, 10, tzinfo=UTC))


@pytest.fixture
def category(db) -> Category:
    # Category.id is a plain IntegerField primary key, so it must be given.
    return Category.objects.create(id=1, name="Matrix category")


@pytest.fixture
def client_as(db) -> Callable[[User | None], APIClient]:
    def make(user: User | None) -> APIClient:
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    return make


@pytest.fixture
def role_client(request, client_as) -> Callable[[str], APIClient]:
    """Resolve a role name ('anonymous' or a user fixture) to a client."""

    def get(role: str) -> APIClient:
        return client_as(None if role == "anonymous" else request.getfixturevalue(role))

    return get
