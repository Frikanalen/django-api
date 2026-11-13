import datetime
from collections.abc import Callable
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from fk.models import Organization, User, Video
from programming.models import Recipe
from programming.services import uploads_recipe
from scheduling import broadcast_time
from scheduling.models import AirtimeGrant, RecurringSlot

OSLO = ZoneInfo("Europe/Oslo")

# Wednesday 2026-01-07, 12:00 Oslo. Its broadcast week starts Monday
# 2026-01-05; grants are issued through Sunday 2026-01-25.
NOW = datetime.datetime(2026, 1, 7, 12, tzinfo=OSLO)


@pytest.fixture
def editor(db) -> User:
    return User.objects.create(email="grant-editor@example.test", identity_confirmed=True)


@pytest.fixture
def organization(editor: User) -> Organization:
    return Organization.objects.create(name="Grant org", editor=editor, fkmember=True)


@pytest.fixture
def make_video(editor: User, organization: Organization) -> Callable[..., Video]:
    counter = iter(range(1, 10_000))

    def make(minutes: float = 10, **fields) -> Video:
        n = next(counter)
        fields.setdefault("organization", organization)
        fields.setdefault("proper_import", True)
        fields.setdefault(
            "created_time", datetime.datetime(2025, 1, 1, tzinfo=OSLO) + timedelta(days=n)
        )
        created_time = fields.pop("created_time")
        video = Video.objects.create(
            name=fields.pop("name", f"Video {n}"),
            creator=editor,
            duration=timedelta(minutes=minutes),
            **fields,
        )
        # auto_now_add ignores what create() was given.
        Video.objects.filter(pk=video.pk).update(created_time=created_time)
        video.refresh_from_db()
        return video

    return make


@pytest.fixture
def recipe(organization: Organization) -> Recipe:
    """Everything the organization uploads: new first, then the archive."""
    return uploads_recipe(organization)


@pytest.fixture
def make_grant(organization: Organization) -> Callable[..., AirtimeGrant]:
    def make(start: datetime.datetime, minutes: float = 60, **fields) -> AirtimeGrant:
        fields.setdefault("organization", organization)
        fields.setdefault("locks_at", broadcast_time.default_locks_at(start))
        return AirtimeGrant.objects.create(
            starttime=start, endtime=start + timedelta(minutes=minutes), **fields
        )

    return make


@pytest.fixture
def weekly_slot(organization: Organization, recipe: Recipe) -> RecurringSlot:
    """Thursdays 19:00-20:00 Oslo."""
    return RecurringSlot.objects.create(
        organization=organization,
        frequency=RecurringSlot.Frequency.WEEKLY,
        weekdays=[3],
        start_time=datetime.time(19),
        duration=timedelta(hours=1),
        starts_on=datetime.date(2026, 1, 1),
        recipe=recipe,
    )
