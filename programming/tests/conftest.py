import datetime
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from fk.models import Organization, User, Video

OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime.datetime(2026, 1, 7, 12, tzinfo=OSLO)


@dataclass
class Window:
    """All a recipe needs to know about the airtime it fills."""

    pk: int = 1
    duration: timedelta = timedelta(hours=1)
    # Back to back unless a test asks for the five-minute grid.
    padded: bool = False
    starttime: datetime.datetime = field(default_factory=lambda: NOW)

    @property
    def endtime(self) -> datetime.datetime:
        return self.starttime + self.duration


@pytest.fixture
def editor(db) -> User:
    return User.objects.create(email="recipe-editor@example.test", identity_confirmed=True)


@pytest.fixture
def organization(editor: User) -> Organization:
    return Organization.objects.create(name="Recipe org", editor=editor, fkmember=True)


@pytest.fixture
def make_video(editor: User, organization: Organization) -> Callable[..., Video]:
    counter = iter(range(1, 10_000))

    def make(minutes: float = 10, **fields) -> Video:
        n = next(counter)
        fields.setdefault("organization", organization)
        fields.setdefault("proper_import", True)
        created_time = fields.pop(
            "created_time", datetime.datetime(2025, 1, 1, tzinfo=OSLO) + timedelta(days=n)
        )
        categories = fields.pop("categories", [])
        video = Video.objects.create(
            name=fields.pop("name", f"Video {n}"),
            creator=editor,
            duration=timedelta(minutes=minutes),
            **fields,
        )
        video.categories.set(categories)
        # auto_now_add ignores what create() was given.
        Video.objects.filter(pk=video.pk).update(created_time=created_time)
        video.refresh_from_db()
        return video

    return make
