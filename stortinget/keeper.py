"""Keep Stortinget's sittings on the schedule while they happen.

On a carry day (Tuesdays and Wednesdays to start with) the keeper books
the sitting from its announced start to an expected end, as soon as the
speaker list shows it. While the sitting runs it follows the list:

- live: stay on air, extending 15 minutes at a time near the end, up to a
  hard stop;
- a long break or the end: cut to filler, and if that happens before
  13:58, put Democracy Now! on 14:00-15:00;
- live again after a break: back on air, cutting Democracy Now! short if
  that is what's on (Stortinget has precedence);

Stortinget has precedence over members' airtime too: what is in the way
of a sitting is shortened, moved or cancelled. Otherwise the keeper only
changes grants it made itself (see :class:`KeeperBooking`). It holds no
state of its own beyond what phase the feed has settled in, so a restart
picks up where it left off.

The keeper also carries the European Commission's midday press briefing
from EbS (see :mod:`stortinget.ebs`), on the days EbS lists one:

- booked from its listed start for an hour, into free time only: it never
  moves anyone, and Stortinget's sitting takes its time when they meet;
- live: extended 15 minutes at a time, into free time, up to a longest;
- over (interpretation gone, or another programme on): cut to filler;
- never began, half an hour after the listed start: cut to filler;
- already on when the keeper first sees it (after a sitting breaks off,
  say): booked from now, if the time is free.

Democracy Now! goes on live 14:00-15:00 every weekday, booked a few days
ahead into free time (the station's own placeholder there gives way); a
sitting that runs on takes the hour, and gives it back as above.

And Democracy Now! is recorded every weekday, 14:00-15:00, and rerun from
the recording at 20:00-21:00, whether or not it aired live: the keeper
books the evening slot in the morning, if it is free, together with a
recording request (see :mod:`recordings`).
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from fk.models import LiveSource, LiveTransmission, Scheduleitem, Video, VideoFileVariant
from programming.services import named_jukebox
from recordings.models import RecordingRequest
from scheduling import services
from scheduling.models import AirtimeGrant

from .ebs import OnAir, Programme
from .feed import OSLO, Snapshot
from .models import KeeperBooking
from .rules import BRIEFING_HOLD, Debouncer, Phase, briefing_phase, phase_of

logger = logging.getLogger(__name__)
Kind = KeeperBooking.Kind

# When a cut takes effect: far enough ahead for playout to hear of it.
CUT_DELAY = datetime.timedelta(seconds=15)
# When a sitting that comes back goes on air again: time for the pull to start.
RESUME_DELAY = datetime.timedelta(seconds=30)
# The shortest booking for a sitting that comes back.
SHORTEST_RESUME = datetime.timedelta(minutes=30)
# Extend a sitting on air when its end is this close, by this much.
EXTEND_WHEN = datetime.timedelta(minutes=10)
EXTEND_BY = datetime.timedelta(minutes=15)
# The EU midday briefing: the shortest booking worth making, and how long
# after its listed start a briefing that hasn't begun is given up.
SHORTEST_BRIEFING = datetime.timedelta(minutes=20)
BRIEFING_NEVER_BEGAN = datetime.timedelta(minutes=30)
# After the grid's real end (once rewritten), the briefing is over regardless.
BRIEFING_GRID_GRACE = datetime.timedelta(minutes=2)
# Set up Democracy Now!'s recording no later than this before it starts.
RECORDING_LEAD = datetime.timedelta(minutes=5)
# The time a sitting leaves (on a carry day, up to the hard stop) is filled
# from the jukebox, in grants of at most FILL_CHUNK made FILL_LEAD before
# they begin, none shorter than SHORTEST_FILL. Any other booking takes their
# time back.
FILL_LEAD = datetime.timedelta(minutes=2)
FILL_AHEAD = datetime.timedelta(minutes=5)
FILL_CHUNK = datetime.timedelta(minutes=30)
SHORTEST_FILL = datetime.timedelta(minutes=5)
FILL_JUKEBOX = "Etter Stortinget"
# No clips or test pictures in the keeper's jukebox: nothing shorter.
JUKEBOX_SHORTEST = datetime.timedelta(minutes=2)
# How long after a rerun ends the videos it was made of are deleted.
CLEAN_UP_AFTER = datetime.timedelta(minutes=5)
# The jukebox theme that fills the rest of the rerun's hour.
RERUN_JUKEBOX = "Etter Democracy Now!"
# Playout can leave black if an item changes just before it starts or ends.
BOUNDARY_GUARD = datetime.timedelta(seconds=10)

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAY_NAMES = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
MONTHS = [
    "januar", "februar", "mars", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "desember",
]  # fmt: skip


class NoRoom(Exception):
    """Something the keeper may not move is in the way."""


def _clock(text: str) -> datetime.time:
    return datetime.time.fromisoformat(text)


@dataclass(frozen=True)
class Config:
    live_source_id: int
    days: frozenset[int]
    expected_end: dict[int, datetime.time]
    hard_stop: datetime.time
    democracy_now_source_id: int
    democracy_now_start: datetime.time
    democracy_now_length: datetime.timedelta
    democracy_now_deadline: datetime.time
    dry_run: bool
    briefing_source_id: int = 0
    briefing_days: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    # "listed": on air from the listed start (EbS's own picture until the
    # briefing begins); "live": only once EbS shows it has begun.
    briefing_start: str = "listed"
    briefing_length: datetime.timedelta = datetime.timedelta(hours=1)
    # The latest a briefing stays on, counted from its listed start.
    briefing_longest: datetime.timedelta = datetime.timedelta(minutes=90)
    # Democracy Now!'s rerun from the day's recording (None: no rerun).
    democracy_now_rerun_at: datetime.time | None = None
    democracy_now_rerun_days: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    # Democracy Now!'s own file of the day's show (a strftime pattern),
    # fetched into a video of its own; the rerun plays it instead of the
    # recording if it is playable by democracy_now_master_lead before.
    democracy_now_master_url: str = ""
    democracy_now_master_lead: datetime.timedelta = datetime.timedelta(minutes=10)
    # Democracy Now! live on these days, booked this far ahead; a sitting
    # takes the time when it runs on.
    democracy_now_days: frozenset[int] = frozenset()
    democracy_now_ahead: datetime.timedelta = datetime.timedelta(days=3)

    @classmethod
    def from_settings(cls) -> Config:
        def days_map(text: str) -> dict[int, datetime.time]:
            pairs = (part.split("=") for part in text.split(",") if part)
            return {WEEKDAYS[day.strip()]: _clock(time.strip()) for day, time in pairs}

        return cls(
            live_source_id=settings.FK_STORTINGET_LIVE_SOURCE_ID,
            days=frozenset(
                WEEKDAYS[d.strip()] for d in settings.FK_STORTINGET_DAYS.split(",") if d
            ),
            expected_end=days_map(settings.FK_STORTINGET_EXPECTED_END),
            hard_stop=_clock(settings.FK_STORTINGET_HARD_STOP),
            democracy_now_source_id=settings.FK_DEMOCRACY_NOW_LIVE_SOURCE_ID,
            democracy_now_start=_clock(settings.FK_DEMOCRACY_NOW_START),
            democracy_now_length=datetime.timedelta(minutes=settings.FK_DEMOCRACY_NOW_MINUTES),
            democracy_now_deadline=_clock(settings.FK_DEMOCRACY_NOW_DEADLINE),
            dry_run=settings.FK_STORTINGET_DRY_RUN,
            briefing_source_id=settings.FK_EU_BRIEFING_LIVE_SOURCE_ID,
            briefing_days=frozenset(
                WEEKDAYS[d.strip()] for d in settings.FK_EU_BRIEFING_DAYS.split(",") if d
            ),
            briefing_start=settings.FK_EU_BRIEFING_START,
            briefing_length=datetime.timedelta(minutes=settings.FK_EU_BRIEFING_MINUTES),
            briefing_longest=datetime.timedelta(minutes=settings.FK_EU_BRIEFING_LONGEST_MINUTES),
            democracy_now_rerun_at=(
                _clock(settings.FK_DEMOCRACY_NOW_RERUN_AT)
                if settings.FK_DEMOCRACY_NOW_RERUN_AT
                else None
            ),
            democracy_now_rerun_days=frozenset(
                WEEKDAYS[d.strip()] for d in settings.FK_DEMOCRACY_NOW_RERUN_DAYS.split(",") if d
            ),
            democracy_now_master_url=settings.FK_DEMOCRACY_NOW_MASTER_URL,
            democracy_now_master_lead=datetime.timedelta(
                minutes=settings.FK_DEMOCRACY_NOW_MASTER_LEAD_MINUTES
            ),
            democracy_now_days=frozenset(
                WEEKDAYS[d.strip()] for d in settings.FK_DEMOCRACY_NOW_DAYS.split(",") if d
            ),
            democracy_now_ahead=datetime.timedelta(days=settings.FK_DEMOCRACY_NOW_AHEAD_DAYS),
        )


def _at(day: datetime.date, time: datetime.time) -> datetime.datetime:
    return datetime.datetime.combine(day, time, tzinfo=OSLO)


def _whole(moment: datetime.datetime) -> datetime.datetime:
    """Rounded up to a whole second."""
    if moment.microsecond:
        moment = moment.replace(microsecond=0) + datetime.timedelta(seconds=1)
    return moment


def _minute(moment: datetime.datetime) -> datetime.datetime:
    """Rounded up to a whole minute."""
    if moment.second or moment.microsecond:
        moment = moment.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
    return moment


def _norwegian_date(day: datetime.date) -> str:
    return f"{DAY_NAMES[day.weekday()]} {day.day}. {MONTHS[day.month - 1]} {day.year}"


@dataclass
class Keeper:
    config: Config
    _debouncers: dict[int, Debouncer] = field(default_factory=dict)
    _briefing_debouncers: dict[datetime.date, Debouncer] = field(default_factory=dict)
    # Days whose briefing the keeper has seen live, and seen end.
    _briefing_seen: set[datetime.date] = field(default_factory=set)
    _briefing_over: set[datetime.date] = field(default_factory=set)
    _briefing_skipped: set[datetime.date] = field(default_factory=set)
    _rerun_skipped: set[datetime.date] = field(default_factory=set)
    _democracy_now_skipped: set[datetime.date] = field(default_factory=set)
    # The rerun grants last filled, by the lengths of what they play.
    _rerun_filled: dict[int, tuple] = field(default_factory=dict)

    # -- The loop calls this ------------------------------------------------

    def tick(self, snapshot: Snapshot | None, now: datetime.datetime | None = None) -> None:
        """Act on one reading of the speaker list (None: the fetch failed,
        which neither confirms nor contradicts anything)."""
        now = now or timezone.now()
        self._democracy_now_ahead(now)
        self._democracy_now_rerun(now)
        self._democracy_now_master(now)
        self._fill_after_sittings(now)
        self._clean_up_reruns(now)
        if snapshot is not None:
            self._book_ahead(snapshot, now)
        today = now.astimezone(OSLO).date()
        for mote_id in self._sittings_on(today, snapshot):
            debouncer = self._debouncers.setdefault(mote_id, Debouncer())
            if snapshot is not None:
                debouncer.feed(snapshot.at, phase_of(snapshot, mote_id))
            if debouncer.settled is not None:
                self._follow(mote_id, debouncer.settled, today, now)

    def _sittings_on(self, today: datetime.date, snapshot: Snapshot | None) -> list[int]:
        """The sittings to follow today: the ones booked for today, or the
        one on now if it's a carry day and nothing is booked yet (the
        keeper was started mid-sitting)."""
        booked = set(
            KeeperBooking.objects.filter(day=today, kind=Kind.SITTING).values_list(
                "mote_id", flat=True
            )
        )
        if (
            snapshot is not None
            and snapshot.mote_id
            and snapshot.starts_at is not None
            and snapshot.starts_at.astimezone(OSLO).date() == today
            and today.weekday() in self.config.days
        ):
            booked.add(snapshot.mote_id)
        return sorted(m for m in booked if m)

    # -- Booking ahead ------------------------------------------------------

    def _book_ahead(self, snapshot: Snapshot, now: datetime.datetime) -> None:
        """Book an announced sitting on a carry day, from its start to the
        day's expected end."""
        start = snapshot.starts_at
        if snapshot.status != "ikke_startet" or not snapshot.mote_id or start is None:
            return
        day = start.astimezone(OSLO).date()
        if day.weekday() not in self.config.days or start <= now + RESUME_DELAY:
            return
        if KeeperBooking.objects.filter(kind=Kind.SITTING, mote_id=snapshot.mote_id).exists():
            return
        end = max(_at(day, self.config.expected_end.get(day.weekday(), self.config.hard_stop)),
                  start + SHORTEST_RESUME)  # fmt: skip
        end = min(end, _at(day, self.config.hard_stop))
        self._book(Kind.SITTING, start, end, now, mote_id=snapshot.mote_id)

    # -- Following a sitting ------------------------------------------------

    def _follow(self, mote_id: int, phase: Phase, today: datetime.date, now: datetime.datetime):
        sitting = self._on_air(Kind.SITTING, today, now, mote_id=mote_id)
        if phase in (Phase.BREAK, Phase.ENDED):
            if sitting is not None:
                self._cut(sitting, now, why=f"sitting {mote_id}: {phase.value}")
            if phase == Phase.ENDED:
                for grant in self._coming(Kind.SITTING, today, now, mote_id=mote_id):
                    self._cancel(grant, now, why=f"sitting {mote_id} is over")
            self._democracy_now_if_due(today, now)
        elif phase == Phase.LIVE:
            if sitting is not None:
                self._extend_if_due(sitting, today, now)
            elif not self._coming(Kind.SITTING, today, now, mote_id=mote_id):
                self._resume(mote_id, today, now)

    def _resume(self, mote_id: int, today: datetime.date, now: datetime.datetime) -> None:
        hard_stop = _at(today, self.config.hard_stop)
        start = _whole(now + RESUME_DELAY)
        if start + datetime.timedelta(minutes=5) >= hard_stop:
            return
        democracy_now = self._on_air(Kind.DEMOCRACY_NOW, today, now)
        if democracy_now is not None:
            self._cut(democracy_now, now, why=f"sitting {mote_id} is back")
        for grant in self._coming(Kind.DEMOCRACY_NOW, today, now):
            self._cancel(grant, now, why=f"sitting {mote_id} is back")
        expected = _at(today, self.config.expected_end.get(today.weekday(), self.config.hard_stop))
        end = min(max(expected, start + SHORTEST_RESUME), hard_stop)
        self._book(Kind.SITTING, start, end, now, mote_id=mote_id)

    def _extend_if_due(self, grant: AirtimeGrant, today: datetime.date, now: datetime.datetime):
        hard_stop = _at(today, self.config.hard_stop)
        if grant.endtime - now > EXTEND_WHEN or grant.endtime >= hard_stop:
            return
        self._extend(grant, min(grant.endtime + EXTEND_BY, hard_stop), now)

    def _democracy_now_if_due(self, today: datetime.date, now: datetime.datetime) -> None:
        if not self.config.democracy_now_source_id:
            return
        local = now.astimezone(OSLO)
        if local.date() != today or local.time() >= self.config.democracy_now_deadline:
            return
        if KeeperBooking.objects.filter(
            kind=Kind.DEMOCRACY_NOW, day=today, grant__in=AirtimeGrant.objects.active()
        ).exists():
            return
        start = _at(today, self.config.democracy_now_start)
        end = start + self.config.democracy_now_length
        # Democracy Now! fills the sitting's time, but doesn't take anyone's
        # (the station's own placeholder gives way, jukebox fills too).
        placeholders = self._station_placeholders(start, end)
        if self._free_until(start, end, ignoring=placeholders) != end:
            logger.info("Not booking Democracy Now!: %s-%s is taken", start, end)
            return
        self._book(Kind.DEMOCRACY_NOW, start, end, now, replacing=placeholders)

    def _democracy_now_ahead(self, now: datetime.datetime) -> None:
        """Book Democracy Now! live a few days ahead, into free time or the
        station's placeholder there. A sitting takes its time if it runs on
        (and gives it back if it breaks off before the deadline)."""
        config = self.config
        if not config.democracy_now_source_id:
            return
        today = now.astimezone(OSLO).date()
        for offset in range(config.democracy_now_ahead.days + 1):
            day = today + datetime.timedelta(days=offset)
            if day.weekday() not in config.democracy_now_days or day in self._democracy_now_skipped:
                continue
            start = _at(day, config.democracy_now_start)
            end = start + config.democracy_now_length
            if start - now <= FILL_LEAD:
                continue  # too late for playout to cue it
            if KeeperBooking.objects.filter(kind=Kind.DEMOCRACY_NOW, day=day).exists():
                continue
            placeholders = self._station_placeholders(start, end)
            if self._free_until(start, end, ignoring=placeholders) != end:
                logger.info("Not booking Democracy Now!: %s-%s is taken", start, end)
                self._democracy_now_skipped.add(day)
                continue
            self._book(Kind.DEMOCRACY_NOW, start, end, now, replacing=placeholders)
            if config.dry_run:
                self._democracy_now_skipped.add(day)  # say it once

    def _station_placeholders(self, start, end) -> list[AirtimeGrant]:
        """The station's own grants inside start-end that the keeper didn't
        make: placeholders showing the time is reserved, which give way to
        the keeper's Democracy Now! bookings."""
        return list(
            AirtimeGrant.objects.active().filter(
                starttime__gte=start,
                endtime__lte=end,
                organization__live_sources=self.config.democracy_now_source_id,
                keeper_booking__isnull=True,
            )
        )

    # -- Democracy Now!'s evening rerun --------------------------------------

    def _democracy_now_rerun(self, now: datetime.datetime) -> None:
        """Book today's rerun and the recording it plays, once, in good time."""
        config = self.config
        if not config.democracy_now_source_id or config.democracy_now_rerun_at is None:
            return
        today = now.astimezone(OSLO).date()
        if today.weekday() not in config.democracy_now_rerun_days or today in self._rerun_skipped:
            return
        record_start = _at(today, config.democracy_now_start)
        record_end = record_start + config.democracy_now_length
        if now > record_start - RECORDING_LEAD:
            return  # too late to record today
        if KeeperBooking.objects.filter(kind=Kind.DEMOCRACY_NOW_RERUN, day=today).exists():
            return
        start = _at(today, config.democracy_now_rerun_at)
        end = start + config.democracy_now_length
        # The station's own slot there (a placeholder that shows the hour is
        # reserved) gives way to the rerun; anyone else's grant doesn't.
        placeholders = self._station_placeholders(start, end)
        if self._free_until(start, end, ignoring=placeholders) != end:
            logger.info("Not rerunning Democracy Now!: %s-%s is taken", start, end)
            self._rerun_skipped.add(today)
            return
        logger.warning(
            "Recording Democracy Now! %s-%s for a rerun %s-%s",
            record_start.astimezone(OSLO), record_end.astimezone(OSLO),
            start.astimezone(OSLO), end.astimezone(OSLO),
        )  # fmt: skip
        if config.dry_run:
            self._rerun_skipped.add(today)  # say it once a day
            return
        try:
            with transaction.atomic():
                source = LiveSource.objects.select_related("organization__editor").get(
                    pk=config.democracy_now_source_id
                )
                organization = source.organization
                if organization.editor is None:
                    raise NoRoom(f"{organization} has no responsible editor")
                title = f"Democracy Now! {today.day}. {MONTHS[today.month - 1]} {today.year}"
                video = self._democracy_now_video(organization, f"{title} (opptak)")
                for placeholder in placeholders:
                    self._cancel(placeholder, now, why="the Democracy Now! rerun takes it")
                RecordingRequest.objects.create(
                    source=source, video=video, starttime=record_start, endtime=record_end
                )
                if config.democracy_now_master_url:
                    # The show as Democracy Now! publishes it, into a video
                    # of its own: the rerun switches to it once it plays.
                    RecordingRequest.objects.create(
                        source=source,
                        video=self._democracy_now_video(organization, title),
                        starttime=record_start,
                        endtime=record_end,
                        record=False,
                        master_url=today.strftime(config.democracy_now_master_url),
                        master_deadline=start - config.democracy_now_master_lead,
                    )
                grant = services.create_grant(
                    organization=organization,
                    starttime=start,
                    endtime=end,
                    lock_exempt=True,
                    # What the show leaves of the hour: from the jukebox.
                    recipe=named_jukebox(organization, RERUN_JUKEBOX, shortest=JUKEBOX_SHORTEST),
                    videos=[video],
                )
                KeeperBooking.objects.create(grant=grant, kind=Kind.DEMOCRACY_NOW_RERUN, day=today)
        except NoRoom as error:
            logger.error("Not rerunning Democracy Now!: %s", error)
            self._rerun_skipped.add(today)

    def _democracy_now_video(self, organization, name: str) -> Video:
        video = Video.objects.create(
            name=name,
            organization=organization,
            creator=organization.editor,
            duration=self.config.democracy_now_length,
            ref_url="https://www.democracynow.org/",
            spoken_language="en",
        )
        earlier = (
            Video.objects.filter(keeper_kind_filter(Kind.DEMOCRACY_NOW)).order_by("-pk").first()
        )
        if earlier is not None:
            video.categories.set(earlier.categories.all())
        return video

    def _clean_up_reruns(self, now: datetime.datetime) -> None:
        """Delete the videos a Democracy Now! rerun was made of once it has
        aired: they were for that evening, not for the jukebox."""
        if not self.config.democracy_now_source_id or self.config.dry_run:
            return
        aired = KeeperBooking.objects.filter(
            kind=Kind.DEMOCRACY_NOW_RERUN, grant__endtime__lte=now - CLEAN_UP_AFTER
        ).values_list("day", flat=True)
        starts = [_at(day, self.config.democracy_now_start) for day in aired]
        for request in RecordingRequest.objects.filter(
            source_id=self.config.democracy_now_source_id,
            starttime__in=starts,
            state__in=(
                RecordingRequest.State.ARCHIVED,
                RecordingRequest.State.FAILED,
                RecordingRequest.State.CANCELLED,
            ),
        ).select_related("video"):
            self._delete(request.video, now)

    def _delete(self, video: Video, now: datetime.datetime) -> None:
        try:
            services.delete_video(video, now=now)
        except ValidationError as error:  # on air: next time
            logger.info("Not deleting %s yet: %s", video, error)
            return
        logger.warning("Deleted %s", video)

    def _fill_rerun(self, grant: AirtimeGrant, now: datetime.datetime) -> None:
        """Fill the rerun's hour again whenever what it plays changes length
        (the show's real duration arrives with its file)."""
        lengths = tuple(
            grant.items.exclude(video=None)
            .filter(pinned=True)
            .values_list("video__duration", flat=True)
        )
        if self._rerun_filled.get(grant.pk) == lengths:
            return
        self._rerun_filled[grant.pk] = lengths
        if services.refill(grant, now=now):
            logger.info("Filled the rest of the Democracy Now! rerun from the jukebox")

    def _democracy_now_master(self, now: datetime.datetime) -> None:
        """Switch today's rerun to Democracy Now!'s own file once it plays."""
        today = now.astimezone(OSLO).date()
        booking = (
            KeeperBooking.objects.filter(kind=Kind.DEMOCRACY_NOW_RERUN, day=today)
            .select_related("grant")
            .first()
        )
        if (
            booking is None
            or booking.grant.starttime - self.config.democracy_now_master_lead <= now
        ):
            return
        self._fill_rerun(booking.grant, now)
        playing = list(booking.grant.items.exclude(video=None).values_list("video_id", flat=True))
        fetch = (
            RecordingRequest.objects.filter(
                record=False,
                state=RecordingRequest.State.ARCHIVED,
                video__in=Video.objects.filter(
                    videofile__variant__in=(VideoFileVariant.ORIGINAL, VideoFileVariant.BROADCAST)
                ),
                starttime=_at(today, self.config.democracy_now_start),
                source_id=self.config.democracy_now_source_id,
            )
            .exclude(video_id__in=playing)
            .select_related("video")
            .first()
        )
        if fetch is None:
            return
        recording = RecordingRequest.objects.filter(
            record=True, video_id__in=playing, starttime=fetch.starttime
        ).first()
        if recording is None:
            return
        if services.replace_video(booking.grant, recording.video, fetch.video, now=now):
            logger.warning("The Democracy Now! rerun plays %s now", fetch.video)
            # The recording is no longer wanted: gone before the jukebox finds it.
            self._delete(recording.video, now)

    # -- The EU midday briefing ---------------------------------------------

    def tick_briefing(
        self,
        on_air: OnAir | None,
        programme: Programme | None,
        now: datetime.datetime | None = None,
    ) -> None:
        """Act on one reading of EbS's live status (None: not read) and
        today's briefing in EbS's grid (None: not listed, or not read)."""
        if not self.config.briefing_source_id:
            return
        now = now or timezone.now()
        today = now.astimezone(OSLO).date()
        if programme is not None and self.config.briefing_start == "listed":
            self._book_briefing_ahead(programme, now)
        debouncer = self._briefing_debouncers.setdefault(today, Debouncer(hold=BRIEFING_HOLD))
        if on_air is not None:
            phase = briefing_phase(on_air, today, seen_live=today in self._briefing_seen)
            if phase is not None:
                debouncer.feed(on_air.at, phase)
        phase = debouncer.settled
        if phase == Phase.LIVE:
            self._briefing_seen.add(today)
        elif (
            programme is not None
            and programme.duration
            and now > programme.start + programme.duration + BRIEFING_GRID_GRACE
        ):
            phase = Phase.ENDED  # the grid has been rewritten with its real end
        briefing = self._on_air(Kind.EU_BRIEFING, today, now)
        if phase == Phase.ENDED:
            self._briefing_over.add(today)
            if briefing is not None:
                self._cut(briefing, now, why="the briefing is over")
            for grant in self._coming(Kind.EU_BRIEFING, today, now):
                self._cancel(grant, now, why="the briefing is over")
        elif phase == Phase.LIVE:
            if briefing is not None:
                self._extend_briefing_if_due(briefing, programme, today, now)
            elif today not in self._briefing_over and not self._coming(
                Kind.EU_BRIEFING, today, now
            ):
                self._join_briefing(programme, today, now)
        elif (
            phase == Phase.NOT_STARTED  # held: not just a restart mid-briefing
            and briefing is not None
            and today not in self._briefing_seen
            and now >= briefing.starttime + BRIEFING_NEVER_BEGAN
        ):
            self._briefing_over.add(today)
            self._cut(briefing, now, why="the briefing never began")

    def _briefing_latest(self, programme: Programme | None, now) -> datetime.datetime:
        return (programme.start if programme is not None else now) + self.config.briefing_longest

    def _book_briefing_ahead(self, programme: Programme, now: datetime.datetime) -> None:
        start = programme.start
        day = start.astimezone(OSLO).date()
        if day.weekday() not in self.config.briefing_days or start <= now + RESUME_DELAY:
            return
        if KeeperBooking.objects.filter(kind=Kind.EU_BRIEFING, day=day).exists():
            return
        end = min(start + self.config.briefing_length, self._briefing_latest(programme, now))
        self._book_briefing(_whole(start), end, now)

    def _join_briefing(self, programme: Programme | None, today, now) -> None:
        if today.weekday() not in self.config.briefing_days:
            return
        latest = self._briefing_latest(programme, now)
        start = _whole(now + RESUME_DELAY)
        if start + SHORTEST_BRIEFING > latest:
            return
        listed = programme.start if programme is not None else now
        end = min(max(listed + self.config.briefing_length, start + SHORTEST_BRIEFING), latest)
        self._book_briefing(start, end, now)

    def _book_briefing(self, start, end, now) -> None:
        """Book the briefing for start-end, or as much of it as is free from
        start on, if that is worth it."""
        free = self._free_until(start, end)
        day = start.astimezone(OSLO).date()
        if free is None or free - start < SHORTEST_BRIEFING:
            if day not in self._briefing_skipped:
                logger.info("Not booking the EU briefing: %s-%s is taken", start, end)
                self._briefing_skipped.add(day)
            return
        self._book(Kind.EU_BRIEFING, start, free, now)

    def _extend_briefing_if_due(self, grant, programme, today, now) -> None:
        latest = self._briefing_latest(programme, now)
        if grant.endtime - now > EXTEND_WHEN or grant.endtime >= latest:
            return
        free = self._free_until(grant.endtime, min(grant.endtime + EXTEND_BY, latest))
        if free is None or free <= grant.endtime:
            return
        self._extend(grant, free, now, make_room=False)

    def _free_until(
        self, start, end, *, ignoring=(), fills_count=False
    ) -> datetime.datetime | None:
        """How far the schedule is free from `start`, up to `end`; None if
        something is on at `start` itself. Grants in `ignoring` don't count,
        nor jukebox fills (which give way) unless `fills_count`."""
        grants = (
            AirtimeGrant.objects.active()
            .filter(starttime__lt=end, endtime__gt=start)
            .exclude(pk__in=[g.pk for g in ignoring])
        )
        if not fills_count:
            grants = grants.exclude(keeper_booking__kind=Kind.FILL)
        rows = Scheduleitem.objects.overlapping(start, end).filter(grant=None)
        if (
            grants.filter(starttime__lte=start).exists()
            or rows.filter(starttime__lte=start).exists()
        ):
            return None
        starts = [g.starttime for g in grants] + [r.starttime for r in rows]
        return min([end, *starts])

    # -- Filling what a sitting leaves --------------------------------------

    def _fill_after_sittings(self, now: datetime.datetime) -> None:
        """On a carry day, once a sitting is off air (a break, or over), give
        the station the free time up to the hard stop, a chunk at a time,
        to fill from the jukebox: the contingency kept for Stortinget
        shouldn't air nothing."""
        config = self.config
        today = now.astimezone(OSLO).date()
        if today.weekday() not in config.days:
            return
        sittings = self._bookings(Kind.SITTING, today).order_by("starttime")
        first = sittings.select_related("organization").first()
        if first is None or first.starttime > now or self._on_air(Kind.SITTING, today, now):
            return
        until = _at(today, config.hard_stop)
        # The station's own reservation for the day, pushed back by the
        # sitting, would air nothing: it fills from the jukebox too.
        for reserved in AirtimeGrant.objects.active().filter(
            organization=first.organization,
            keeper_booking__isnull=True,
            recipe=None,
            starttime__gt=now,
            starttime__lt=until,
        ):
            logger.warning("Filling %s from the jukebox", reserved)
            if self.config.dry_run:
                continue
            reserved.recipe = named_jukebox(
                first.organization, FILL_JUKEBOX, shortest=JUKEBOX_SHORTEST
            )
            reserved.save(update_fields=["recipe", "updated_at"])
            services.refill(reserved, now=now)
        # Democracy Now! may still take 14-15 until its deadline.
        dn_start = _at(today, config.democracy_now_start)
        dn_open = (
            config.democracy_now_source_id
            and now.astimezone(OSLO).time() < config.democracy_now_deadline
            and not KeeperBooking.objects.filter(kind=Kind.DEMOCRACY_NOW, day=today).exists()
        )
        earliest = _minute(now + FILL_LEAD)
        # Where something free could begin soon: now, or where what is on ends.
        ends = AirtimeGrant.objects.active().filter(
            endtime__gt=earliest, endtime__lte=earliest + FILL_AHEAD
        )
        for start in sorted({earliest, *ends.values_list("endtime", flat=True)}):
            if start >= until or start < first.starttime:
                continue
            free = self._free_until(start, until, fills_count=True)
            if free is None:
                continue
            end = min(free, start + FILL_CHUNK)
            if dn_open and start < dn_start:
                end = min(end, dn_start)
            if end - start < SHORTEST_FILL:
                continue
            self._book_fill(first.organization, start, end)
            return

    def _book_fill(self, organization, start, end) -> None:
        logger.warning(
            "Filling %s-%s from the jukebox", start.astimezone(OSLO), end.astimezone(OSLO)
        )
        if self.config.dry_run:
            return
        with transaction.atomic():
            grant = services.create_grant(
                organization=organization,
                starttime=start,
                endtime=end,
                lock_exempt=True,
                recipe=named_jukebox(organization, FILL_JUKEBOX, shortest=JUKEBOX_SHORTEST),
            )
            KeeperBooking.objects.create(
                grant=grant, kind=Kind.FILL, day=start.astimezone(OSLO).date()
            )

    def _clear_fills(self, start, end, now) -> None:
        """Take start-end back from jukebox fills: one on air is cut where
        the booking begins, one still to come gives way."""
        fills = AirtimeGrant.objects.active().filter(
            keeper_booking__kind=Kind.FILL, starttime__lt=end, endtime__gt=start
        )
        for grant in fills:
            if grant.starttime < max(start, now):
                at = max(start, _whole(now + CUT_DELAY))
                if at < grant.endtime and not self.config.dry_run:
                    services.update_grant(grant, endtime=at, now=now)
            else:
                self._cancel(grant, now, why="a booking needs the time")

    # -- Looking things up --------------------------------------------------

    def _bookings(self, kind: str, today: datetime.date, mote_id: int | None = None):
        bookings = KeeperBooking.objects.filter(kind=kind, day=today)
        if mote_id is not None:
            bookings = bookings.filter(mote_id=mote_id)
        return AirtimeGrant.objects.active().filter(keeper_booking__in=bookings)

    def _on_air(self, kind, today, now, mote_id=None) -> AirtimeGrant | None:
        return (
            self._bookings(kind, today, mote_id).filter(starttime__lte=now, endtime__gt=now).first()
        )

    def _coming(self, kind, today, now, mote_id=None) -> list[AirtimeGrant]:
        return list(self._bookings(kind, today, mote_id).filter(starttime__gt=now))

    def _near_boundary(self, moment: datetime.datetime, *, apart_from: AirtimeGrant) -> bool:
        """Whether another programme starts or ends within BOUNDARY_GUARD of
        `moment`."""
        low, high = moment - BOUNDARY_GUARD, moment + BOUNDARY_GUARD
        rows = Scheduleitem.objects.exclude(grant=apart_from)
        return (
            rows.filter(starttime__gte=low, starttime__lte=high).exists()
            or rows.filter(airtime__endswith__gte=low, airtime__endswith__lte=high).exists()
        )

    # -- Changing the schedule ----------------------------------------------

    def _cut(self, grant: AirtimeGrant, now: datetime.datetime, *, why: str) -> None:
        at = _whole(now + CUT_DELAY)
        if at >= grant.endtime:
            return
        if self._near_boundary(at, apart_from=grant):
            logger.info("Not cutting %s at %s yet: too close to another programme", grant, at)
            return
        logger.warning("Cutting %s at %s (%s)", grant, at.astimezone(OSLO), why)
        if self.config.dry_run:
            return
        services.update_grant(grant, endtime=at, now=now)

    def _cancel(self, grant: AirtimeGrant, now: datetime.datetime, *, why: str) -> None:
        logger.warning("Cancelling %s (%s)", grant, why)
        if self.config.dry_run:
            return
        services.cancel_grant(grant, now=now)

    def _make_room(self, start, end, now, *, keep: AirtimeGrant | None = None) -> None:
        """Clear start-end of everything but `keep`: Stortinget has
        precedence. A grant that sticks out is shortened or moved, one
        inside is cancelled. A staff entry is never moved: NoRoom."""
        staff = Scheduleitem.objects.overlapping(start, end).filter(grant=None)
        if staff.exists():
            raise NoRoom(f"staff entry {staff.first()} is in the way")
        in_the_way = AirtimeGrant.objects.active().filter(starttime__lt=end, endtime__gt=start)
        if keep is not None:
            in_the_way = in_the_way.exclude(pk=keep.pk)
        for grant in in_the_way.select_related("organization"):
            if grant.starttime < start:
                logger.warning("Shortening %s to end at %s for Stortinget", grant, start)
                if not self.config.dry_run:
                    services.update_grant(grant, endtime=start, now=now)
            elif grant.endtime > end and grant.starttime > now and not _is_briefing(grant):
                logger.warning("Moving the start of %s to %s for Stortinget", grant, end)
                if not self.config.dry_run:
                    services.update_grant(grant, starttime=end, now=now)
            else:
                self._cancel(grant, now, why="Stortinget needs the time")

    def _live_video(self, kind: str, source: LiveSource, start, end, mote_id) -> Video:
        day = start.astimezone(OSLO).date()
        language = "en"
        if kind == Kind.SITTING:
            name = f"Stortinget direkte – {_norwegian_date(day)}"
            ref_url = f"https://data.stortinget.no/eksport/dagsorden?moteid={mote_id}"
            description = "Direkte fra Stortingssalen. Kilde: Stortinget."
            language = "no"
        elif kind == Kind.EU_BRIEFING:
            name = f"EU-kommisjonens pressebriefing – {day.day}. {MONTHS[day.month - 1]} {day.year}"
            ref_url = "https://audiovisual.ec.europa.eu/"
            description = (
                "Direkte fra Europakommisjonens daglige pressebriefing i Brussel. "
                f"© European Union, {day.year}. Kilde: EbS."
            )
        else:
            name = f"Democracy Now! {day.day}. {MONTHS[day.month - 1]} {day.year}"
            ref_url = "https://www.democracynow.org/"
            description = ""
        organization = source.organization
        if organization.editor is None:
            raise NoRoom(f"{organization} has no responsible editor")
        video = Video.objects.create(
            name=name,
            description=description,
            organization=organization,
            creator=organization.editor,
            duration=end - start,
            ref_url=ref_url,
            spoken_language=language,
        )
        earlier = (
            Video.objects.filter(keeper_kind_filter(kind))
            .exclude(pk=video.pk)
            .order_by("-pk")
            .first()
        )
        if earlier is not None:
            video.categories.set(earlier.categories.all())
        LiveTransmission.objects.create(video=video, source=source)
        return video

    def _book(self, kind, start, end, now, *, mote_id: int | None = None, replacing=()) -> None:
        source_id = {
            Kind.SITTING: self.config.live_source_id,
            Kind.DEMOCRACY_NOW: self.config.democracy_now_source_id,
            Kind.EU_BRIEFING: self.config.briefing_source_id,
        }[kind]
        day = start.astimezone(OSLO).date()
        logger.warning(
            "Booking %s %s-%s%s",
            Kind(kind).label,
            start.astimezone(OSLO),
            end.astimezone(OSLO),
            f" (møte {mote_id})" if mote_id else "",
        )
        if self.config.dry_run:
            return
        try:
            with transaction.atomic():
                source = LiveSource.objects.select_related("organization__editor").get(pk=source_id)
                self._clear_fills(start, end, now)
                for placeholder in replacing:
                    self._cancel(placeholder, now, why=f"{Kind(kind).label} takes it")
                if kind == Kind.SITTING:
                    self._make_room(start, end, now)
                video = self._live_video(kind, source, start, end, mote_id)
                grant = services.create_grant(
                    organization=source.organization,
                    starttime=start,
                    endtime=end,
                    lock_exempt=True,
                    videos=[video],
                    now=now,
                )
                KeeperBooking.objects.create(grant=grant, kind=kind, mote_id=mote_id, day=day)
        except NoRoom as error:
            logger.error("Not booking %s: %s", Kind(kind).label, error)

    def _extend(self, grant: AirtimeGrant, end: datetime.datetime, now, *, make_room=True) -> None:
        """Lengthen a booking on air: its grant, its video and the row
        playing now. A sitting makes room; the briefing only takes free time."""
        logger.warning("Extending %s to %s", grant, end.astimezone(OSLO))
        if self.config.dry_run:
            return
        old_end = grant.endtime
        try:
            with transaction.atomic():
                self._clear_fills(old_end, end, now)
                if make_room:
                    self._make_room(old_end, end, now, keep=grant)
                AirtimeGrant.objects.filter(pk=grant.pk).update(endtime=end)
                for row in grant.schedule_items.filter(starttime__lte=now, is_live=True):
                    if row.endtime == old_end:
                        row.duration += end - old_end
                        row.save(update_fields=["duration"])
                        if row.video_id is not None:
                            Video.objects.filter(pk=row.video_id).update(duration=row.duration)
        except NoRoom as error:
            logger.error("Not extending %s: %s", grant, error)
            return
        transaction.on_commit(cache.clear)


def _is_briefing(grant: AirtimeGrant) -> bool:
    """The EU briefing is not worth keeping for what is left after a sitting."""
    return KeeperBooking.objects.filter(grant=grant, kind=Kind.EU_BRIEFING).exists()


def keeper_kind_filter(kind: str):
    """Videos the keeper made for `kind` (to copy categories from)."""
    return Q(grant_items__grant__keeper_booking__kind=kind)
