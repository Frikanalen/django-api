"""The keeper and the EU Commission's midday briefing, replayed against the
briefing of 16 Nov 2026 as EbS's live status showed it."""

import datetime
import json
from datetime import timedelta

import pytest
from django.utils import timezone

from fk.models import LiveSource, Organization, Scheduleitem, User
from scheduling import services
from scheduling.models import AirtimeGrant
from stortinget import ebs
from stortinget.ebs import OnAir, Programme
from stortinget.feed import OSLO, Snapshot
from stortinget.keeper import Config, Keeper
from stortinget.models import KeeperBooking
from stortinget.rules import Phase, briefing_phase

pytestmark = pytest.mark.django_db
State = AirtimeGrant.State
Kind = KeeperBooking.Kind
UTC = datetime.UTC


def oslo(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 11, day, hour, minute, second, tzinfo=OSLO)


def utc(hour: int, minute: int, second: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 11, 16, hour, minute, second, tzinfo=UTC)


MONDAY = oslo(16, 0).date()
TITLE = "EC Midday press briefing of 16/11/2026"
PRESS_POINT = "EC press point by European Commission Executive Vice-Preside"
COP = "COP31 in Antalya, Türkiye"
I, IEF, IEFS = (
    frozenset({"INT"}),
    frozenset({"INT", "EN", "FR"}),
    frozenset({"INT", "EN", "FR", "ES"}),
)

# EbS's live status on 16 Nov 2026 (channel 1134), as it changed.
TRANSITIONS = [
    (utc(10, 50, 39), PRESS_POINT, IEFS),
    (utc(10, 57, 34), PRESS_POINT, I),  # a 41 s blip to INT only
    (utc(10, 58, 15), PRESS_POINT, IEFS),
    (utc(11, 0, 5), None, I),
    (utc(11, 0, 55), PRESS_POINT, IEFS),
    (utc(11, 6, 45), None, I),
    (utc(11, 16, 55), TITLE, IEF),  # the briefing began 11:14:35
    (utc(11, 19, 25), None, IEF),
    (utc(11, 20, 1), TITLE, IEF),
    (utc(11, 20, 41), None, IEF),
    (utc(11, 20, 51), TITLE, IEF),
    (utc(11, 44, 42), TITLE, I),  # over
    (utc(11, 59, 42), None, I),
    (utc(12, 0, 32), COP, I),
]
# As the grid listed it beforehand: 11:01Z, no length yet.
LISTED = Programme(TITLE, utc(11, 1), timedelta(0))


def on_air_at(at: datetime.datetime, transitions=TRANSITIONS) -> OnAir:
    title, languages = None, I
    for when, t, langs in transitions:
        if when <= at:
            title, languages = t, langs
    return OnAir(at, title, languages)


# -- EbS ----------------------------------------------------------------------


def test_reads_the_live_status() -> None:
    body = json.dumps(
        [
            {"languages": ["INT"], "channelId": 1136, "reference": "", "severity": 1},
            {
                "languages": ["INT"],
                "channelId": 1134,
                "reference": "I-297633",
                "severity": 3,
                "startDatetime": "2026-11-16T11:44:08.680Z",
                "title": "<p>EC Midday press briefing of 16/11/2026</p>",
            },
        ]
    )
    assert ebs.parse_live(body, utc(11, 44, 47)) == OnAir(utc(11, 44, 47), TITLE, I)
    assert ebs.parse_live("[]", utc(11, 44, 47)) is None


def test_finds_the_briefing_in_the_grid() -> None:
    def programme(start, title, duration=0):
        return {
            "id": "x",
            "startDatetime": start,
            "duration": duration,
            "languages": ["INT", "EN", "FR"],
            "broadcastStatus": "LIVE",
            "titles": [{"language": "EN", "content": f"<p>{title}</p>"}],
            "transmissions": [],
        }

    body = json.dumps(
        [
            {
                "date": "20261119",
                "channel": {"id": "60771dc55de36aa6eb4759c6", "name": "EBS", "eidSat": 1134},
                "warnings": [],
                "programs": [
                    programme("2026-11-19T10:30:00.000Z", "Court of Justice of the European Union"),
                    programme("2026-11-19T11:00:00.000Z", "EC Midday press briefing of 19/11/2026"),
                    programme("2026-11-19T15:15:00.000Z", "EU-Montenegro Accession Conference"),
                ],
            }
        ]
    ).encode()
    programmes = ebs.parse_grid(body)
    thursday = oslo(19, 0).date()
    assert ebs.find_briefing(programmes, thursday) == Programme(
        "EC Midday press briefing of 19/11/2026",
        datetime.datetime(2026, 11, 19, 11, tzinfo=UTC),
        timedelta(0),
    )
    assert ebs.find_briefing(programmes, MONDAY) is None


@pytest.mark.parametrize(
    ("title", "languages", "seen_live", "phase"),
    [
        (None, IEF, True, None),
        (PRESS_POINT, IEFS, False, Phase.NOT_STARTED),
        (TITLE, IEF, False, Phase.LIVE),
        (TITLE, I, True, Phase.ENDED),
        (TITLE, I, False, Phase.NOT_STARTED),
        (COP, I, True, Phase.ENDED),
        ("EC Midday press briefing of 13/11/2026", IEF, True, Phase.ENDED),
    ],
)
def test_briefing_phases(title, languages, seen_live, phase) -> None:
    on_air = OnAir(utc(11, 30), title, languages)
    assert briefing_phase(on_air, MONDAY, seen_live=seen_live) == phase


# -- The schedule -------------------------------------------------------------


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = oslo(16, 8)

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
    eu = LiveSource.objects.create(organization=station, name="EbS (EU)")
    return Keeper(
        Config(
            live_source_id=chamber.pk,
            days=frozenset({1, 2}),
            expected_end={1: datetime.time(16), 2: datetime.time(13)},
            hard_stop=datetime.time(18),
            democracy_now_source_id=0,
            democracy_now_start=datetime.time(14),
            democracy_now_length=timedelta(hours=1),
            democracy_now_deadline=datetime.time(13, 58),
            dry_run=False,
            briefing_source_id=eu.pk,
        )
    )


def grant(starttime, endtime, organization) -> AirtimeGrant:
    return AirtimeGrant.objects.create(
        organization=organization, starttime=starttime, endtime=endtime, locks_at=starttime
    )


def compile_today(clock) -> None:
    for g in AirtimeGrant.objects.active():
        services.compile_grant(g, clock.now)


def run(keeper, clock, start, end, programme=LISTED, on_air=on_air_at) -> None:
    at = start
    while at < end:
        clock.now = at
        keeper.tick_briefing(on_air(at), programme, at)
        at += timedelta(seconds=10)


def on_air(day: int = 16) -> list[tuple]:
    rows = Scheduleitem.objects.filter(
        starttime__gte=oslo(day, 0), starttime__lt=oslo(day + 1, 0)
    ).order_by("starttime")
    return [(r.starttime.astimezone(OSLO), r.endtime.astimezone(OSLO), r.video.name) for r in rows]


def test_follows_the_briefing_of_16_november(keeper, clock) -> None:
    clock.now = oslo(16, 8)
    keeper.tick_briefing(None, LISTED, clock.now)
    booking = KeeperBooking.objects.get()
    assert booking.kind == Kind.EU_BRIEFING
    assert (booking.grant.starttime, booking.grant.endtime) == (oslo(16, 12, 1), oslo(16, 13, 1))
    clock.now = oslo(16, 11, 50)
    compile_today(clock)

    # From 11:50 Oslo (10:50Z), through the press point's blip.
    run(keeper, clock, oslo(16, 11, 50), oslo(16, 13, 10))

    assert on_air() == [
        # Interpretation dropped at 12:44:42, seen 12:44:50, held 90 s,
        # cut 15 s later.
        (
            oslo(16, 12, 1),
            oslo(16, 12, 46, 35),
            "EU-kommisjonens pressebriefing – 16. november 2026",
        ),
    ]


def test_gives_up_a_briefing_that_never_begins(keeper, clock) -> None:
    clock.now = oslo(16, 8)
    keeper.tick_briefing(None, LISTED, clock.now)
    clock.now = oslo(16, 11, 59)
    compile_today(clock)

    run(keeper, clock, oslo(16, 11, 59), oslo(16, 12, 40), on_air=lambda at: OnAir(at, COP, I))

    assert on_air()[0][:2] == (oslo(16, 12, 1), oslo(16, 12, 31, 15))


# Each extension commits on its own, as in the keeper's loop: the database
# checks a grant's rows against its window at commit, as of each change.
@pytest.mark.django_db(transaction=True)
def test_extends_a_briefing_that_runs_long(keeper, clock) -> None:
    clock.now = oslo(16, 8)
    keeper.tick_briefing(None, LISTED, clock.now)
    clock.now = oslo(16, 11, 59)
    compile_today(clock)

    run(keeper, clock, oslo(16, 12, 40), oslo(16, 13, 40), on_air=lambda at: OnAir(at, TITLE, IEF))

    # 13:01 + 15 min, then up to its longest, 90 min after the listed start.
    assert on_air()[0][:2] == (oslo(16, 12, 1), oslo(16, 13, 31))


def test_takes_only_free_time(keeper, clock, member) -> None:
    grant(oslo(16, 12, 30), oslo(16, 13), member)
    clock.now = oslo(16, 8)
    keeper.tick_briefing(None, LISTED, clock.now)
    booking = KeeperBooking.objects.get()
    assert (booking.grant.starttime, booking.grant.endtime) == (oslo(16, 12, 1), oslo(16, 12, 30))


def test_skips_a_briefing_with_no_room(keeper, clock, member) -> None:
    taken = grant(oslo(16, 12, 10), oslo(16, 13), member)
    clock.now = oslo(16, 8)
    keeper.tick_briefing(None, LISTED, clock.now)
    assert not KeeperBooking.objects.exists()
    taken.refresh_from_db()
    assert taken.state != State.CANCELLED


def test_joins_a_briefing_already_on(keeper, clock) -> None:
    # Not booked ahead (no grid, say), and already on at 12:20.
    run(keeper, clock, oslo(16, 12, 20), oslo(16, 12, 22), programme=None,
        on_air=lambda at: OnAir(at, TITLE, IEF))  # fmt: skip
    booking = KeeperBooking.objects.get()
    assert booking.grant.starttime == oslo(16, 12, 22)


def test_a_sitting_takes_the_briefings_time(keeper, clock) -> None:
    wednesday_listing = Programme(
        "EC Midday press briefing of 18/11/2026", oslo(18, 12), timedelta(0)
    )
    clock.now = oslo(16, 8)
    keeper.tick_briefing(None, wednesday_listing, clock.now)
    briefing = KeeperBooking.objects.get(kind=Kind.EU_BRIEFING).grant

    # Wednesday's sitting is announced: 10:00 to the expected 13:00.
    keeper.tick(Snapshot(clock.now, 11684, "ikke_startet", oslo(18, 10), None), clock.now)

    briefing.refresh_from_db()
    assert briefing.state == State.CANCELLED
    assert KeeperBooking.objects.filter(kind=Kind.SITTING).exists()


def test_off_without_a_source(keeper, clock) -> None:
    keeper.config = Config(**{**keeper.config.__dict__, "briefing_source_id": 0})
    keeper.tick_briefing(None, LISTED, oslo(16, 8))
    assert not KeeperBooking.objects.exists()
