"""Democracy Now! recorded every weekday and rerun at 20:00 from the recording."""

import datetime
from datetime import timedelta

import pytest

from fk.models import LiveSource, Organization, User
from recordings.models import RecordingRequest
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
def keeper(station) -> Keeper:
    democracy_now = LiveSource.objects.create(organization=station, name="Democracy Now!")
    return Keeper(
        Config(
            live_source_id=0,
            days=frozenset({1, 2}),
            expected_end={},
            hard_stop=datetime.time(18),
            democracy_now_source_id=democracy_now.pk,
            democracy_now_start=datetime.time(14),
            democracy_now_length=timedelta(hours=1),
            democracy_now_deadline=datetime.time(13, 58),
            dry_run=False,
            democracy_now_rerun_at=datetime.time(20),
        )
    )


def test_records_and_books_the_rerun(keeper) -> None:
    keeper.tick(None, oslo(19, 6))  # Thursday morning
    keeper.tick(None, oslo(19, 6, 1))

    booking = KeeperBooking.objects.get()
    assert booking.kind == Kind.DEMOCRACY_NOW_RERUN
    assert (booking.grant.starttime, booking.grant.endtime) == (oslo(19, 20), oslo(19, 21))
    video = booking.grant.items.get().video
    assert video.name == "Democracy Now! 19. november 2026 (opptak)"
    assert not hasattr(video, "live_transmission")
    request = RecordingRequest.objects.get()
    assert (request.video, request.starttime, request.endtime) == (
        video,
        oslo(19, 14),
        oslo(19, 15),
    )
    assert request.source_id == keeper.config.democracy_now_source_id


def test_leaves_a_members_evening_alone(keeper) -> None:
    member = Organization.objects.create(name="Member")
    AirtimeGrant.objects.create(
        organization=member, starttime=oslo(19, 20, 30), endtime=oslo(19, 21), locks_at=oslo(19, 20)
    )
    keeper.tick(None, oslo(19, 6))
    assert not KeeperBooking.objects.exists()
    assert not RecordingRequest.objects.exists()


def test_not_on_weekends_nor_too_late(keeper) -> None:
    keeper.tick(None, oslo(21, 6))  # Saturday
    keeper.tick(None, oslo(20, 13, 56))  # Friday, four minutes before
    assert not RecordingRequest.objects.exists()
