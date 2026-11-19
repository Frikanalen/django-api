"""Run the recording reconciler: keep MediaMTX recording what has been
requested, and hand finished recordings to ingest (recordings.reconcile)."""

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from fk.receiver import ReceiverUnavailable
from recordings.reconcile import reconcile

logger = logging.getLogger("recordings")

INTERVAL_SECONDS = 20


class Command(BaseCommand):
    help = "Keep MediaMTX recording what has been requested."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="One pass, then exit.")

    def handle(self, *args, once=False, **options):
        if not (settings.FK_MEDIAMTX_API_URL and settings.FK_INGEST_INTERNAL_URL):
            logger.error("FK_MEDIAMTX_API_URL and FK_INGEST_INTERNAL_URL must be set")
            return
        logger.warning("Keeping recordings")
        while True:
            close_old_connections()
            try:
                reconcile()
            except ReceiverUnavailable as error:
                logger.warning("MediaMTX did not answer: %s", error)
            except Exception:
                logger.exception("The recording reconciler failed this pass")
            if once:
                return
            time.sleep(INTERVAL_SECONDS)
