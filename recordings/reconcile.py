"""Keep MediaMTX recording what has been requested, and hand finished
recordings to ingest.

Each pass (once every half minute or so, see manage.py keep_recordings):

- a request whose window, with a minute's margin either side, contains now
  gets its own MediaMTX path, contrib/record/<id>, which reads the source's
  live path and records it. Reading starts a pulled source even if nothing
  airs it. Paths added through the API are lost when MediaMTX restarts, so
  every pass puts back what is missing;
- a request whose window (and margin) has passed loses its path, and ingest
  is asked to archive it. Ingest asks back for the details, so the call
  carries nothing but the id;
- a hand-off ingest hasn't answered is tried again, a few times.

MediaMTX writes segments by the clock; ingest cuts the exact window out of
them through the playback server, so the margins never reach the file.
"""

from __future__ import annotations

import datetime
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from fk import receiver

from .models import RecordingRequest

logger = logging.getLogger(__name__)
State = RecordingRequest.State

MARGIN = datetime.timedelta(minutes=1)
IMPORT_RETRY = datetime.timedelta(minutes=20)
IMPORT_ATTEMPTS = 3
TIMEOUT_SECONDS = 10


def path_config(request: RecordingRequest) -> dict:
    # Read over loopback SRT, which keeps picture and sound on the source's
    # shared timestamps (MediaMTX's RTSP output re-times each track by
    # arrival). Django allows loopback SRT reads of an enabled source.
    return {
        "source": f"srt://127.0.0.1:8890?streamid=read:{request.source.contribution_path}",
        "sourceOnDemand": False,
        "record": True,
    }


def reconcile(now: datetime.datetime | None = None) -> None:
    now = now or timezone.now()
    due = RecordingRequest.objects.select_related("source").filter(
        state__in=(State.WAITING, State.RECORDING),
        starttime__lte=now + MARGIN,
        endtime__gt=now - MARGIN,
    )
    for request in due:
        if receiver.path_config(request.path) is None:
            logger.warning("Recording %s on %s", request, request.path)
            receiver.add_path(request.path, path_config(request))
        if request.state == State.WAITING:
            RecordingRequest.objects.filter(pk=request.pk).update(state=State.RECORDING)

    finished = RecordingRequest.objects.filter(
        state__in=(State.WAITING, State.RECORDING), endtime__lte=now - MARGIN
    )
    for request in finished:
        receiver.remove_path(request.path)
        if request.state == State.WAITING:  # never got a path: nothing to import
            request.state, request.error = State.FAILED, "Opptaket startet aldri."
            request.save(update_fields=["state", "error"])
            continue
        request.state = State.IMPORTING
        request.save(update_fields=["state"])
        hand_to_ingest(request, now)

    for request in RecordingRequest.objects.filter(
        state=State.IMPORTING, import_requested_at__lte=now - IMPORT_RETRY
    ):
        if request.import_attempts >= IMPORT_ATTEMPTS:
            logger.error("Ingest never archived %s", request)
            request.state, request.error = State.FAILED, "Ingest arkiverte aldri opptaket."
            request.save(update_fields=["state", "error"])
        else:
            hand_to_ingest(request, now)

    # A request cancelled while it ran keeps no path behind.
    for request in RecordingRequest.objects.filter(
        Q(state=State.CANCELLED), endtime__gt=now - datetime.timedelta(days=1)
    ):
        receiver.remove_path(request.path)


def hand_to_ingest(request: RecordingRequest, now: datetime.datetime) -> None:
    """Ask ingest to archive `request`. A failed call is tried again later."""
    request.import_attempts += 1
    request.import_requested_at = now
    request.save(update_fields=["import_attempts", "import_requested_at"])
    base = settings.FK_INGEST_INTERNAL_URL.rstrip("/")
    call = urllib.request.Request(
        f"{base}/internal/recordings/{request.pk}", data=b"", method="POST"
    )
    try:
        with urllib.request.urlopen(call, timeout=TIMEOUT_SECONDS):
            logger.warning("Ingest is archiving %s", request)
    except (urllib.error.URLError, OSError) as error:
        logger.warning("Could not hand %s to ingest: %r", request, error)
