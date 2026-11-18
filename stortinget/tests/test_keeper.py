"""The keeper replayed against the sitting of 17 Nov 2026 (meeting 11685),
as the speaker list showed it, with the schedule in the database."""

import datetime
from datetime import timedelta

import pytest
from django.utils import timezone

from fk.models import LiveSource, Organization, Scheduleitem, User
from scheduling import services
from scheduling.models import AirtimeGrant
from stortinget import feed
from stortinget.feed import OSLO, Snapshot
from stortinget.keeper import Config, Keeper
from stortinget.models import KeeperBooking
from stortinget.rules import Debouncer, Phase, phase_of

pytestmark = pytest.mark.django_db
State = AirtimeGrant.State
Kind = KeeperBooking.Kind


def oslo(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 11, day, hour, minute, second, tzinfo=OSLO)


SITTING, NEXT = 11685, 11678


def feed_on(at: datetime.datetime) -> Snapshot:
    """The speaker list at `at`, as recorded on 13-17 Nov 2026."""
    if at < oslo(17, 10):
        return Snapshot(at, SITTING, "ikke_startet", oslo(17, 10), None)
    if at < oslo(17, 13, 12, 19):
        return Snapshot(at, SITTING, "aktiv", oslo(17, 10), None)
    if at < oslo(17, 14, 54, 2):
        return Snapshot(at, SITTING, "pause", oslo(17, 10), None)
    if at < oslo(17, 14, 54, 23):
        return Snapshot(at, SITTING, "aktiv", oslo(17, 10), None)
    if at < oslo(17, 15, 8, 4):
        return Snapshot(at, SITTING, "votering", oslo(17, 10), None)
    if at < oslo(17, 15, 10, 46):
        return Snapshot(at, SITTING, "aktiv", oslo(17, 10), None)
    return Snapshot(at, NEXT, "ikke_startet", oslo(18, 10), oslo(18, 10))


# -- The feed and the rules ---------------------------------------------------


def test_parses_the_speaker_list() -> None:
    body = b"""<talerliste_oversikt xmlns="http://data.stortinget.no">
      <respons_dato_tid>2026-11-17T13:12:20</respons_dato_tid>
      <mote_aktivitet_status>pause</mote_aktivitet_status>
      <mote_id>11685</mote_id>
      <mote_neste_start_dato_tid>0001-01-01T00:00:00</mote_neste_start_dato_tid>
      <mote_start_dato_tid>2026-11-17T10:00:00Z</mote_start_dato_tid>
      <taleinformasjon_liste/><taler_liste/>
    </talerliste_oversikt>"""
    at = oslo(17, 13, 12, 20)
    assert feed.parse(body, at) == Snapshot(at, SITTING, "pause", oslo(17, 10), None)


@pytest.mark.parametrize(
    ("snapshot", "phase"),
    [
        (Snapshot(oslo(17, 9), SITTING, "ikke_startet", oslo(17, 10), None), Phase.NOT_STARTED),
        (Snapshot(oslo(17, 11), SITTING, "aktiv", oslo(17, 10), None), Phase.LIVE),
        (Snapshot(oslo(17, 15), SITTING, "votering", oslo(17, 10), None), Phase.LIVE),
        (Snapshot(oslo(17, 13), SITTING, "pause", oslo(17, 10), None), Phase.BREAK),
        (Snapshot(oslo(17, 13), SITTING, "pause", oslo(17, 10), oslo(17, 15)), Phase.BREAK),
        (Snapshot(oslo(17, 13), SITTING, "pause", oslo(17, 10), oslo(17, 13, 20)), Phase.LIVE),
        (Snapshot(oslo(17, 16), NEXT, "ikke_startet", oslo(18, 10), None), Phase.ENDED),
        (Snapshot(oslo(17, 16), 0, "ikke_spesifisert", None, None), Phase.ENDED),
    ],
)
def test_phases(snapshot, phase) -> None:
    assert phase_of(snapshot, SITTING) == phase


def test_a_one_poll_blip_does_not_settle() -> None:
    debouncer = Debouncer()
    start = oslo(16, 1, 59)
    settled = set()
    for i in range(12):
        at = start + timedelta(seconds=10 * i)
        phase = Phase.ENDED if i == 3 else Phase.NOT_STARTED
        settled.add(debouncer.feed(at, phase))
    assert settled == {None, Phase.NOT_STARTED}


# -- The schedule -------------------------------------------------------------


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = oslo(13, 8, 32)

    monkeypatch.setattr(timezone, "now", lambda: Clock.now)
    return Clock


@pytest.fixture
def station(db) -> Organization:
    editor = User.objects.create(email="station-editor@example.test", identity_confirmed=True)
    return Organization.objects.create(name="Frikanalen", editor=editor, fkmember=True)


@pytest.fixture
def member(db) -> Organization:
    editor = User.objects.create(email="member-editor@example.test", identity_confirmed=True)
    return Organization.objects.create(name="Member", editor=editor, fkmember=True)


@pytest.fixture
def keeper(station) -> Keeper:
    chamber = LiveSource.objects.create(organization=station, name="Stortinget")
    democracy_now = LiveSource.objects.create(organization=station, name="Democracy Now!")
    return Keeper(
        Config(
            live_source_id=chamber.pk,
            days=frozenset({1, 2}),
            expected_end={1: datetime.time(16), 2: datetime.time(13)},
            hard_stop=datetime.time(18),
            democracy_now_source_id=democracy_now.pk,
            democracy_now_start=datetime.time(14),
            democracy_now_length=timedelta(hours=1),
            democracy_now_deadline=datetime.time(13, 58),
            dry_run=False,
        )
    )


def run(keeper: Keeper, clock, start: datetime.datetime, end: datetime.datetime) -> None:
    at = start
    while at < end:
        clock.now = at
        keeper.tick(feed_on(at), at)
        at += timedelta(seconds=10)


def on_air(day: int = 17) -> list[tuple]:
    rows = Scheduleitem.objects.filter(
        starttime__gte=oslo(day, 0), starttime__lt=oslo(day + 1, 0)
    ).order_by("starttime")
    return [(r.starttime.astimezone(OSLO), r.endtime.astimezone(OSLO), r.video.name) for r in rows]


def grant(starttime, endtime, organization) -> AirtimeGrant:
    return AirtimeGrant.objects.create(
        organization=organization, starttime=starttime, endtime=endtime, locks_at=starttime
    )


def compile_today(clock) -> None:
    for g in AirtimeGrant.objects.active():
        services.compile_grant(g, clock.now)


def test_books_an_announced_tuesday_sitting_and_makes_room(keeper, clock, member) -> None:
    inside = grant(oslo(17, 12), oslo(17, 13), member)
    sticking_out = grant(oslo(17, 15, 30), oslo(17, 17), member)
    clock.now = oslo(13, 8, 32)
    keeper.tick(feed_on(clock.now), clock.now)

    booking = KeeperBooking.objects.get()
    assert (booking.kind, booking.mote_id, booking.day) == (
        Kind.SITTING,
        SITTING,
        oslo(17, 0).date(),
    )
    assert (booking.grant.starttime, booking.grant.endtime) == (oslo(17, 10), oslo(17, 16))
    video = booking.grant.items.get().video
    assert video.name == "Stortinget direkte – tirsdag 17. november 2026"
    assert video.live_transmission.source_id == keeper.config.live_source_id
    inside.refresh_from_db()
    sticking_out.refresh_from_db()
    assert inside.state == State.CANCELLED
    assert sticking_out.starttime == oslo(17, 16)

    # Seeing it again books nothing more.
    keeper.tick(feed_on(clock.now), clock.now)
    assert KeeperBooking.objects.count() == 1


def test_follows_the_sitting_of_17_november(keeper, clock) -> None:
    clock.now = oslo(13, 8, 32)
    keeper.tick(feed_on(clock.now), clock.now)
    clock.now = oslo(17, 9, 59)
    compile_today(clock)

    run(keeper, clock, oslo(17, 9, 59), oslo(17, 15, 20))

    assert on_air() == [
        # Polled every 10 s from 09:59:00. The debate, cut when the hall
        # emptied (13:12:19, seen 13:12:20, held 60 s, cut 15 s later).
        (oslo(17, 10), oslo(17, 13, 13, 35), "Stortinget direkte – tirsdag 17. november 2026"),
        # Democracy Now! in the empty hall, cut when the votes began (14:54:02).
        (oslo(17, 14), oslo(17, 14, 55, 25), "Democracy Now! 17. november 2026"),
        # Back for the votes, cut when the meeting id moved on (15:10:46).
        (
            oslo(17, 14, 55, 40),
            oslo(17, 15, 12, 5),
            "Stortinget direkte – tirsdag 17. november 2026",
        ),
    ]
    # The next sitting, Wednesday's, is booked to the day's expected end.
    wednesday = KeeperBooking.objects.get(mote_id=NEXT)
    assert (wednesday.grant.starttime, wednesday.grant.endtime) == (oslo(18, 10), oslo(18, 13))


def test_extends_a_sitting_that_runs_long(keeper, clock, member) -> None:
    clock.now = oslo(13, 8, 32)
    keeper.tick(feed_on(clock.now), clock.now)
    after = grant(oslo(17, 16), oslo(17, 17), member)
    clock.now = oslo(17, 9, 59)
    compile_today(clock)

    def still_going(at):
        return Snapshot(at, SITTING, "aktiv", oslo(17, 10), None)

    at = oslo(17, 15, 49)
    while at < oslo(17, 15, 52):
        clock.now = at
        keeper.tick(still_going(at), at)
        at += timedelta(seconds=10)

    sitting = KeeperBooking.objects.get(mote_id=SITTING).grant
    sitting.refresh_from_db()
    assert sitting.endtime == oslo(17, 16, 15)
    assert on_air()[0][:2] == (oslo(17, 10), oslo(17, 16, 15))
    after.refresh_from_db()
    assert after.starttime == oslo(17, 16, 15)


def test_dry_run_changes_nothing(keeper, clock, member) -> None:
    keeper.config = Config(**{**keeper.config.__dict__, "dry_run": True})
    inside = grant(oslo(17, 12), oslo(17, 13), member)
    clock.now = oslo(13, 8, 32)
    keeper.tick(feed_on(clock.now), clock.now)
    assert not KeeperBooking.objects.exists()
    inside.refresh_from_db()
    assert inside.state != State.CANCELLED


def test_only_carry_days(keeper, clock) -> None:
    thursday = Snapshot(oslo(17, 16), NEXT + 1, "ikke_startet", oslo(19, 10), None)
    keeper.tick(thursday, oslo(17, 16))
    assert not KeeperBooking.objects.exists()
