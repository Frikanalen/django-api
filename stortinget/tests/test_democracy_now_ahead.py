"""Democracy Now! live booked ahead every weekday; sittings take precedence."""

import dataclasses
import datetime
from datetime import timedelta

import pytest

from fk.models import LiveSource, Organization, User
from scheduling import services
from scheduling.models import AirtimeGrant
from stortinget.feed import OSLO
from stortinget.keeper import Config, Keeper
from stortinget.models import KeeperBooking

pytestmark = pytest.mark.django_db
Kind = KeeperBooking.Kind


def oslo(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 11, day, hour, minute, tzinfo=OSLO)


@pytest.fixture
def station(db) -> Organization:
    editor = User.objects.create(email="station-editor@example.test", identity_confirmed=True)
    return Organization.objects.create(name="Frikanalen", editor=editor, fkmember=True)


@pytest.fixture
def member(db) -> Organization:
    editor = User.objects.create(email="member-editor@example.test", identity_confirmed=True)
    return Organization.objects.create(name="Medlem", editor=editor, fkmember=True)


@pytest.fixture
def keeper(station) -> Keeper:
    democracy_now = LiveSource.objects.create(organization=station, name="Democracy Now!")
    return Keeper(
        Config(
            live_source_id=0,
            days=frozenset({1, 2}),  # Stortinget meets Tuesdays and Wednesdays
            expected_end={},
            hard_stop=datetime.time(18),
            democracy_now_source_id=democracy_now.pk,
            democracy_now_start=datetime.time(14),
            democracy_now_length=timedelta(hours=1),
            democracy_now_deadline=datetime.time(13, 58),
            dry_run=False,
            democracy_now_days=frozenset({0, 1, 2, 3, 4}),
            democracy_now_ahead=timedelta(days=3),
        )
    )


def booked() -> list[tuple[datetime.datetime, datetime.datetime]]:
    return [
        (b.grant.starttime, b.grant.endtime)
        for b in KeeperBooking.objects.filter(kind=Kind.DEMOCRACY_NOW).order_by("day")
    ]


def test_books_every_weekday_ahead(keeper) -> None:
    keeper.tick(None, oslo(20, 9))  # Friday 20.11: Fri, then Mon 23 (Sat/Sun off)

    assert booked() == [(oslo(20, 14), oslo(20, 15)), (oslo(23, 14), oslo(23, 15))]
    keeper.tick(None, oslo(23, 9))
    keeper.tick(None, oslo(23, 9, 1))
    assert [s.date() for s, _ in booked()] == [
        datetime.date(2026, 11, d) for d in (20, 23, 24, 25, 26)
    ]
    video = KeeperBooking.objects.get(day=datetime.date(2026, 11, 26)).grant.items.get().video
    assert video.live_transmission.source_id == keeper.config.democracy_now_source_id


def test_takes_over_the_stations_placeholder(keeper, station) -> None:
    placeholder = services.create_grant(
        organization=station, starttime=oslo(26, 14), endtime=oslo(26, 15), lock_exempt=True
    )

    keeper.tick(None, oslo(26, 13, 47))

    placeholder.refresh_from_db()
    assert placeholder.state == AirtimeGrant.State.CANCELLED
    assert booked()[0] == (oslo(26, 14), oslo(26, 15))


def test_leaves_a_members_grant_alone(keeper, member) -> None:
    theirs = services.create_grant(
        organization=member, starttime=oslo(26, 14, 30), endtime=oslo(26, 16), lock_exempt=True
    )

    keeper.tick(None, oslo(26, 9))

    theirs.refresh_from_db()
    assert theirs.state != AirtimeGrant.State.CANCELLED
    assert datetime.date(2026, 11, 26) not in [s.date() for s, _ in booked()]


def test_not_when_too_late(keeper) -> None:
    keeper.tick(None, oslo(26, 13, 59))
    assert datetime.date(2026, 11, 26) not in [s.date() for s, _ in booked()]


def test_off_without_days(keeper) -> None:
    keeper.config = dataclasses.replace(keeper.config, democracy_now_days=frozenset())
    keeper.tick(None, oslo(26, 9))
    assert booked() == []


def test_a_sitting_takes_the_hour_and_gives_it_back(keeper, station) -> None:
    chamber = LiveSource.objects.create(organization=station, name="Stortinget")
    keeper.config = dataclasses.replace(keeper.config, live_source_id=chamber.pk)
    keeper.tick(None, oslo(24, 9))  # Tuesday: booked ahead
    tuesday = KeeperBooking.objects.get(kind=Kind.DEMOCRACY_NOW, day=datetime.date(2026, 11, 24))

    keeper._book(Kind.SITTING, oslo(24, 10), oslo(24, 16), oslo(24, 9, 30), mote_id=1)
    tuesday.grant.refresh_from_db()
    assert tuesday.grant.state == AirtimeGrant.State.CANCELLED

    # The sitting ends at 12:30 (cut there): Democracy Now! is back on at 14.
    sitting = KeeperBooking.objects.get(kind=Kind.SITTING).grant
    services.update_grant(sitting, endtime=oslo(24, 12, 30), now=oslo(24, 12, 29))
    keeper._democracy_now_if_due(datetime.date(2026, 11, 24), oslo(24, 12, 30))
    again = KeeperBooking.objects.filter(
        kind=Kind.DEMOCRACY_NOW, day=datetime.date(2026, 11, 24)
    ).exclude(pk=tuesday.pk)
    assert [(b.grant.starttime, b.grant.endtime) for b in again] == [(oslo(24, 14), oslo(24, 15))]
