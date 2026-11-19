"""Run the Stortinget keeper: read the speaker list every few seconds and
keep the schedule in step with the sitting (see stortinget.keeper). If the
EU midday briefing is carried, read EbS's grid every few minutes and its
live status every few seconds around the briefing, too."""

import datetime
import json
import logging
import time
import xml.etree.ElementTree as ET

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from websockets.exceptions import WebSocketException

from stortinget import ebs, feed
from stortinget.feed import OSLO
from stortinget.keeper import Config, Keeper
from stortinget.models import KeeperBooking

logger = logging.getLogger("stortinget.keeper")

INTERVAL_SECONDS = 10
HEARTBEAT_SECONDS = 600
GRID_SECONDS = 300
# Read EbS's live status this long either side of the listed briefing.
AROUND_BRIEFING = datetime.timedelta(minutes=10)


class Briefing:
    """EbS's grid and live status, read as often as the briefing needs."""

    def __init__(self, config: Config):
        self.config = config
        self.grid_day: datetime.date | None = None
        self.grid_read = 0.0
        self.programme: ebs.Programme | None = None
        self.last: tuple | None = None

    def read(self, now: datetime.datetime) -> tuple[ebs.OnAir | None, ebs.Programme | None]:
        today = now.astimezone(OSLO).date()
        if self.grid_day != today or time.monotonic() - self.grid_read > GRID_SECONDS:
            try:
                programme = ebs.find_briefing(ebs.fetch_grid(today), today)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                logger.warning("Could not read EbS's grid: %r", error)
            else:
                if programme != self.programme:
                    logger.warning("EbS's grid: briefing %s", programme)
                self.programme, self.grid_day = programme, today
                self.grid_read = time.monotonic()
        if self.grid_day != today:
            self.programme = None
        if not self._watching(now, today):
            return None, self.programme
        try:
            on_air = ebs.fetch_live(now)
        except (OSError, ValueError, TimeoutError, WebSocketException) as error:
            logger.warning("Could not read EbS's live status: %r", error)
            return None, self.programme
        seen = (on_air.title, sorted(on_air.languages)) if on_air else None
        if seen != self.last:
            logger.warning("EbS live: %s", seen)
            self.last = seen
        return on_air, self.programme

    def _watching(self, now: datetime.datetime, today: datetime.date) -> bool:
        programme = self.programme
        if programme is not None and (
            programme.start - AROUND_BRIEFING
            <= now
            <= programme.start + self.config.briefing_longest + AROUND_BRIEFING
        ):
            return True
        return KeeperBooking.objects.filter(
            kind=KeeperBooking.Kind.EU_BRIEFING, day=today, grant__endtime__gt=now
        ).exists()


class Command(BaseCommand):
    help = "Keep Stortinget's sittings on the schedule while they happen."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Read the feed once and exit.")

    def handle(self, *args, once=False, **options):
        config = Config.from_settings()
        if not config.live_source_id:
            logger.error("FK_STORTINGET_LIVE_SOURCE_ID is not set; nothing to keep")
            return
        logger.warning("Keeping Stortinget: %s", config)
        keeper = Keeper(config)
        briefing = Briefing(config) if config.briefing_source_id else None
        last = None
        heartbeat = 0.0
        while True:
            # A database failover leaves dead connections behind; start each
            # round on a fresh one rather than fail until a restart.
            close_old_connections()
            now = timezone.now()
            try:
                snapshot = feed.fetch(now)
            except (OSError, ET.ParseError, ValueError) as error:  # the next round tries again
                logger.warning("Could not read the speaker list: %r", error)
                snapshot = None
            if snapshot is not None:
                seen = (snapshot.mote_id, snapshot.status, snapshot.resumes_at)
                if seen != last or time.monotonic() - heartbeat > HEARTBEAT_SECONDS:
                    logger.warning(
                        "Speaker list: møte %s %s, resumes %s",
                        snapshot.mote_id, snapshot.status, snapshot.resumes_at,
                    )  # fmt: skip
                    last, heartbeat = seen, time.monotonic()
            try:
                keeper.tick(snapshot, now)
            except Exception:
                logger.exception("The keeper failed this round")
            if briefing is not None:
                try:
                    on_air, programme = briefing.read(now)
                    keeper.tick_briefing(on_air, programme, now)
                except Exception:
                    logger.exception("The keeper failed the EU briefing this round")
            if once:
                return
            time.sleep(INTERVAL_SECONDS)
