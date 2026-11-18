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
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from fk.models import LiveSource, LiveTransmission, Scheduleitem, Video
from scheduling import services
from scheduling.models import AirtimeGrant

from .feed import OSLO, Snapshot
from .models import KeeperBooking
from .rules import Debouncer, Phase, phase_of

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
        )


def _at(day: datetime.date, time: datetime.time) -> datetime.datetime:
    return datetime.datetime.combine(day, time, tzinfo=OSLO)


def _whole(moment: datetime.datetime) -> datetime.datetime:
    """Rounded up to a whole second."""
    if moment.microsecond:
        moment = moment.replace(microsecond=0) + datetime.timedelta(seconds=1)
    return moment


def _norwegian_date(day: datetime.date) -> str:
    return f"{DAY_NAMES[day.weekday()]} {day.day}. {MONTHS[day.month - 1]} {day.year}"


@dataclass
class Keeper:
    config: Config
    _debouncers: dict[int, Debouncer] = field(default_factory=dict)

    # -- The loop calls this ------------------------------------------------

    def tick(self, snapshot: Snapshot | None, now: datetime.datetime | None = None) -> None:
        """Act on one reading of the speaker list (None: the fetch failed,
        which neither confirms nor contradicts anything)."""
        now = now or timezone.now()
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
        if KeeperBooking.objects.filter(kind=Kind.DEMOCRACY_NOW, day=today).exists():
            return
        start = _at(today, self.config.democracy_now_start)
        end = start + self.config.democracy_now_length
        # Democracy Now! fills the sitting's time, but doesn't take anyone's.
        taken = AirtimeGrant.objects.active().filter(starttime__lt=end, endtime__gt=start)
        if (
            taken.exists()
            or Scheduleitem.objects.overlapping(start, end).filter(grant=None).exists()
        ):
            logger.info("Not booking Democracy Now!: %s-%s is taken", start, end)
            return
        self._book(Kind.DEMOCRACY_NOW, start, end, now)

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
            elif grant.endtime > end and grant.starttime > now:
                logger.warning("Moving the start of %s to %s for Stortinget", grant, end)
                if not self.config.dry_run:
                    services.update_grant(grant, starttime=end, now=now)
            else:
                self._cancel(grant, now, why="Stortinget needs the time")

    def _live_video(self, kind: str, source: LiveSource, start, end, mote_id) -> Video:
        day = start.astimezone(OSLO).date()
        if kind == Kind.SITTING:
            name = f"Stortinget direkte – {_norwegian_date(day)}"
            ref_url = f"https://data.stortinget.no/eksport/dagsorden?moteid={mote_id}"
            description = "Direkte fra Stortingssalen. Kilde: Stortinget."
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
            spoken_language="no" if kind == Kind.SITTING else "en",
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

    def _book(self, kind, start, end, now, *, mote_id: int | None = None) -> None:
        source_id = (
            self.config.live_source_id if kind == Kind.SITTING
            else self.config.democracy_now_source_id
        )  # fmt: skip
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
                if kind == Kind.SITTING:
                    self._make_room(start, end, now)
                video = self._live_video(kind, source, start, end, mote_id)
                grant = services.create_grant(
                    organization=source.organization,
                    starttime=start,
                    endtime=end,
                    lock_exempt=True,
                    videos=[video],
                )
                KeeperBooking.objects.create(grant=grant, kind=kind, mote_id=mote_id, day=day)
        except NoRoom as error:
            logger.error("Not booking %s: %s", Kind(kind).label, error)

    def _extend(self, grant: AirtimeGrant, end: datetime.datetime, now) -> None:
        """Lengthen a sitting on air: its grant, its video and the row
        playing now."""
        logger.warning("Extending %s to %s", grant, end.astimezone(OSLO))
        if self.config.dry_run:
            return
        old_end = grant.endtime
        try:
            with transaction.atomic():
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


def keeper_kind_filter(kind: str):
    """Videos the keeper made for `kind` (to copy categories from)."""
    return Q(grant_items__grant__keeper_booking__kind=kind)
