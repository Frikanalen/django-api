"""The time a Stortinget sitting leaves is filled from the jukebox."""

import datetime
from datetime import timedelta

import pytest

from fk.models import LiveSource, Organization, User
from scheduling.models import AirtimeGrant
from stortinget.feed import OSLO
from stortinget.keeper import Config, Keeper
from stortinget.models import KeeperBooking

pytestmark = pytest.mark.django_db
Kind = KeeperBooking.Kind
State = AirtimeGrant.State


def oslo(hour: int, minute: int = 0, second: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 11, 25, hour, minute, second, tzinfo=OSLO)  # a Wednesday


@pytest.fixture
def station(db) -> Organization:
    editor = User.objects.create(email="station-editor@example.test", identity_confirmed=True)
    return Organization.objects.create(name="Frikanalen", editor=editor, fkmember=True)


@pytest.fixture
def keeper(station) -> Keeper:
    stortinget = LiveSource.objects.create(organization=station, name="Stortinget")
    democracy_now = LiveSource.objects.create(organization=station, name="Democracy Now!")
    return Keeper(
        Config(
            live_source_id=stortinget.pk,
            days=frozenset({1, 2}),
            expected_end={},
            hard_stop=datetime.time(17),
            democracy_now_source_id=democracy_now.pk,
            democracy_now_start=datetime.time(14),
            democracy_now_length=timedelta(hours=1),
            democracy_now_deadline=datetime.time(13, 58),
            dry_run=False,
        )
    )


def sitting(station, start, end) -> AirtimeGrant:
    grant = AirtimeGrant.objects.create(
        organization=station, starttime=start, endtime=end, locks_at=start - timedelta(hours=1)
    )
    KeeperBooking.objects.create(grant=grant, kind=Kind.SITTING, day=start.date(), mote_id=1)
    return grant


def fills() -> list[tuple[datetime.datetime, datetime.datetime]]:
    return [
        (g.starttime, g.endtime)
        for g in AirtimeGrant.objects.active()
        .filter(keeper_booking__kind=Kind.FILL)
        .order_by("starttime")
    ]


def test_fills_what_a_sitting_left_a_chunk_at_a_time(keeper, station) -> None:
    sitting(station, oslo(10), oslo(14, 31, 5))  # cut when it ended
    keeper._fill_after_sittings(oslo(14, 31, 30))
    assert fills() == [(oslo(14, 34), oslo(15, 4))]
    grant = AirtimeGrant.objects.get(keeper_booking__kind=Kind.FILL)
    assert grant.recipe.jukebox_settings.name == "Etter Stortinget"

    keeper._fill_after_sittings(oslo(14, 40))  # nothing yet: the chunk runs
    assert len(fills()) == 1
    keeper._fill_after_sittings(oslo(15, 0))  # the next follows on
    assert fills()[-1] == (oslo(15, 4), oslo(15, 34))


def test_up_to_a_members_grant_and_the_hard_stop(keeper, station) -> None:
    sitting(station, oslo(10), oslo(16, 20))
    member = Organization.objects.create(name="Member")
    AirtimeGrant.objects.create(
        organization=member, starttime=oslo(16, 40), endtime=oslo(17, 30), locks_at=oslo(16)
    )
    keeper._fill_after_sittings(oslo(16, 20, 10))
    assert fills() == [(oslo(16, 23), oslo(16, 40))]
    keeper._fill_after_sittings(oslo(16, 55))
    assert len(fills()) == 1  # the member's, then the hard stop


def test_leaves_democracy_nows_hour_until_its_deadline(keeper, station) -> None:
    sitting(station, oslo(10), oslo(13, 40))
    keeper._fill_after_sittings(oslo(13, 40, 10))
    assert fills() == [(oslo(13, 43), oslo(14))]


def test_nothing_while_the_sitting_is_on_nor_on_other_days(keeper, station) -> None:
    sitting(station, oslo(10), oslo(14, 45))
    keeper._fill_after_sittings(oslo(14, 40))
    assert fills() == []
    keeper.config = keeper.config.__class__(**{**keeper.config.__dict__, "days": frozenset({1})})
    keeper._fill_after_sittings(oslo(15))
    assert fills() == []


def test_a_booking_takes_the_time_back(keeper, station) -> None:
    sitting(station, oslo(10), oslo(14, 31, 5))
    keeper._fill_after_sittings(oslo(14, 31, 30))
    keeper._book_fill(station, oslo(15, 4), oslo(15, 34))

    keeper._clear_fills(oslo(14, 50, 30), oslo(17), oslo(14, 50))  # the sitting resumes
    # The fill on air is cut where the booking begins; the next gives way.
    assert fills() == [(oslo(14, 34), oslo(14, 50, 30))]


def test_democracy_now_isnt_kept_out_by_a_fill(keeper, station) -> None:
    sitting(station, oslo(10), oslo(12))
    keeper._book_fill(station, oslo(13, 40), oslo(14, 10))  # say, from before a restart
    keeper._democracy_now_if_due(oslo(13).date(), oslo(13, 45))
    assert AirtimeGrant.objects.active().filter(keeper_booking__kind=Kind.DEMOCRACY_NOW).exists()
    assert fills() == [(oslo(13, 40), oslo(14))]


def test_the_stations_reservation_fills_from_the_jukebox_too(keeper, station) -> None:
    sitting(station, oslo(10), oslo(14, 31, 5))
    reserved = AirtimeGrant.objects.create(  # the 10-17 slot, pushed back by the sitting
        organization=station, starttime=oslo(14, 45), endtime=oslo(17), locks_at=oslo(9)
    )
    keeper._fill_after_sittings(oslo(14, 31, 30))
    reserved.refresh_from_db()
    assert reserved.recipe.jukebox_settings.name == "Etter Stortinget"
    assert fills() == [(oslo(14, 34), oslo(14, 45))]
