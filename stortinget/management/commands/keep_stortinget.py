"""Run the Stortinget keeper: read the speaker list every few seconds and
keep the schedule in step with the sitting (see stortinget.keeper)."""

import logging
import time
import xml.etree.ElementTree as ET

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from stortinget import feed
from stortinget.keeper import Config, Keeper

logger = logging.getLogger("stortinget.keeper")

INTERVAL_SECONDS = 10
HEARTBEAT_SECONDS = 600


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
            if once:
                return
            time.sleep(INTERVAL_SECONDS)
