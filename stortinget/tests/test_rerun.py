"""Democracy Now! recorded every weekday and rerun at 20:00 from the recording."""

import dataclasses
import datetime
from datetime import timedelta

import pytest

from fk.models import LiveSource, Organization, User, VideoFile, VideoFileVariant
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


def test_takes_over_the_stations_placeholder(keeper, station) -> None:
    placeholder = AirtimeGrant.objects.create(
        organization=station, starttime=oslo(19, 20), endtime=oslo(19, 21), locks_at=oslo(19, 20)
    )
    keeper.tick(None, oslo(19, 6))

    booking = KeeperBooking.objects.get(kind=Kind.DEMOCRACY_NOW_RERUN)
    assert (booking.grant.starttime, booking.grant.endtime) == (oslo(19, 20), oslo(19, 21))
    placeholder.refresh_from_db()
    assert placeholder.state == AirtimeGrant.State.CANCELLED


MASTER = "https://mpeg.democracynow.org/dn%Y-%m%d-hd.mp4"


@pytest.fixture
def with_master(keeper) -> Keeper:
    keeper.config = dataclasses.replace(keeper.config, democracy_now_master_url=MASTER)
    return keeper


def _playing(booking) -> list[int]:
    return list(booking.grant.items.values_list("video_id", flat=True))


def _arrived(request: RecordingRequest) -> None:
    """Ingest archived the request's file."""
    VideoFile.objects.create(
        video=request.video, variant=VideoFileVariant.ORIGINAL, filename="x/original/x.mp4"
    )
    request.state = RecordingRequest.State.ARCHIVED
    request.save()


def test_fetches_the_days_master_into_a_video_of_its_own(with_master) -> None:
    with_master.tick(None, oslo(19, 6))
    recording = RecordingRequest.objects.get(record=True)
    fetch = RecordingRequest.objects.get(record=False)
    assert recording.master_url == ""
    assert fetch.master_url == "https://mpeg.democracynow.org/dn2026-1119-hd.mp4"
    assert fetch.master_deadline == oslo(19, 19, 50)
    assert (fetch.starttime, fetch.endtime) == (oslo(19, 14), oslo(19, 15))
    assert fetch.video.name == "Democracy Now! 19. november 2026"
    assert recording.video.name == "Democracy Now! 19. november 2026 (opptak)"
    booking = KeeperBooking.objects.get(kind=Kind.DEMOCRACY_NOW_RERUN)
    assert _playing(booking) == [recording.video_id]


def test_the_rerun_switches_to_the_master_once_it_plays(with_master) -> None:
    with_master.tick(None, oslo(19, 6))
    booking = KeeperBooking.objects.get(kind=Kind.DEMOCRACY_NOW_RERUN)
    recording = RecordingRequest.objects.get(record=True)
    fetch = RecordingRequest.objects.get(record=False)

    _arrived(recording)
    with_master.tick(None, oslo(19, 15, 30))
    assert _playing(booking) == [recording.video_id]  # the master isn't here yet

    _arrived(fetch)
    with_master.tick(None, oslo(19, 16))
    assert _playing(booking) == [fetch.video_id]
    with_master.tick(None, oslo(19, 16, 1))  # and stays
    assert _playing(booking) == [fetch.video_id]


def test_no_switch_in_the_last_minutes(with_master) -> None:
    with_master.tick(None, oslo(19, 6))
    booking = KeeperBooking.objects.get(kind=Kind.DEMOCRACY_NOW_RERUN)
    recording = RecordingRequest.objects.get(record=True)
    _arrived(recording)
    _arrived(RecordingRequest.objects.get(record=False))
    with_master.tick(None, oslo(19, 19, 51))
    assert _playing(booking) == [recording.video_id]
