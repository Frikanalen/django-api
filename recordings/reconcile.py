"""Keep MediaMTX recording what has been requested, and hand finished
recordings to ingest.

Each pass (once every half minute or so, see manage.py keep_recordings):

- a request whose window, with a minute's margin either side, contains now
  gets its own MediaMTX path, contrib/record/<id>, which reads the source's
  live path and records it. Reading starts a pulled source even if nothing
  airs it. Paths added through the API are lost when MediaMTX restarts, so
  every pass puts back what is missing;
- a request whose window (and margin) has passed stops recording, and
  ingest is asked to archive it. Ingest asks back for the details, so the
  call carries nothing but the id. The path stays (record off, no source)
  until ingest reports back: MediaMTX's playback serves only configured
  paths;
- a hand-off ingest hasn't answered is tried again, a few times;
- a request that names a master (the source's own file, published some
  time after the programme) waits for it: once the file is up, ingest
  archives that instead of the recording; past the master's deadline it
  archives the recording after all, if anything was recorded. A request
  that doesn't record (record=False) only waits for its master.

MediaMTX writes segments by the clock; ingest cuts the exact window out of
them through the playback server, so the margins never reach the file.
"""

from __future__ import annotations

import datetime
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.utils import timezone

from fk import receiver

from .models import RecordingRequest

logger = logging.getLogger(__name__)
State = RecordingRequest.State

PREFIX = "contrib/record/"
# A finished recording's path: kept so playback can serve it to ingest.
STOPPED = {"record": False}
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
        record=True,
        state__in=(State.WAITING, State.RECORDING),
        starttime__lte=now + MARGIN,
        endtime__gt=now - MARGIN,
    )
    # Every recording path MediaMTX has that no request still needs goes:
    # one whose request was archived, failed or was cancelled.
    stray = set(receiver.config_path_names(PREFIX))
    # One whose window has just passed is stopped below, not removed.
    stray -= {
        f"{PREFIX}{pk}"
        for pk in RecordingRequest.objects.filter(
            state__in=(State.WAITING, State.RECORDING)
        ).values_list("pk", flat=True)
    }
    for request in due:
        stray.discard(request.path)
        if receiver.path_config(request.path) is None:
            logger.warning("Recording %s on %s", request, request.path)
            receiver.add_path(request.path, path_config(request))
        if request.state == State.WAITING:
            RecordingRequest.objects.filter(pk=request.pk).update(state=State.RECORDING)
    # MediaMTX's playback serves a path only while it is configured, so one
    # waiting for ingest stays, but stops recording (and reading its source).
    for request in RecordingRequest.objects.filter(state=State.IMPORTING, record=True):
        stray.discard(request.path)
        if not _stopped(receiver.path_config(request.path)):
            _stop(request)
    for name in sorted(stray):
        logger.warning("Removing %s: no request needs it", name)
        receiver.remove_path(name)

    finished = RecordingRequest.objects.filter(
        state__in=(State.WAITING, State.RECORDING), endtime__lte=now - MARGIN
    )
    for request in finished:
        if request.master_url:  # whatever was recorded, wait for the master (below)
            if request.record:
                _stop(request)
            request.state = State.IMPORTING
            request.save(update_fields=["state"])
            continue
        if request.state == State.WAITING:  # never got a path: nothing to import
            request.state, request.error = State.FAILED, "Opptaket startet aldri."
            request.save(update_fields=["state", "error"])
            continue
        if not receiver.recorded(request.path):  # the source sent nothing
            receiver.remove_path(request.path)
            logger.error("Nothing was recorded for %s", request)
            request.state = State.FAILED
            request.error = "Ingenting ble tatt opp: kilden sendte ikke noe signal."
            request.save(update_fields=["state", "error"])
            continue
        _stop(request)
        request.state = State.IMPORTING
        request.save(update_fields=["state"])
        hand_to_ingest(request, now)

    # Waiting for a master (or, past its deadline, about to use the recording).
    for request in RecordingRequest.objects.filter(
        state=State.IMPORTING, import_attempts=0
    ).exclude(master_url=""):
        _hand_over(request, now)

    for request in RecordingRequest.objects.filter(
        state=State.IMPORTING, import_requested_at__lte=now - IMPORT_RETRY
    ):
        if request.import_attempts >= IMPORT_ATTEMPTS:
            logger.error("Ingest never archived %s", request)
            request.state, request.error = State.FAILED, "Ingest arkiverte aldri opptaket."
            request.save(update_fields=["state", "error"])
        else:
            hand_to_ingest(request, now)


def _hand_over(request: RecordingRequest, now: datetime.datetime) -> None:
    """Hand ingest the master once it is up, or past its deadline the recording."""
    if request.master_deadline is None or now < request.master_deadline:
        if master_is_up(request.master_url):
            logger.warning("The master of %s is up: %s", request, request.master_url)
            request.use_master = True
            request.save(update_fields=["use_master"])
            hand_to_ingest(request, now)
        return
    if request.record and receiver.recorded(request.path):
        logger.warning(
            "No master for %s by %s; using the recording", request, request.master_deadline
        )
        hand_to_ingest(request, now)
        return
    logger.error("Neither a master nor a recording for %s", request)
    if request.record:
        receiver.remove_path(request.path)
    request.state = State.FAILED
    request.error = (request.error + " " if request.error else "") + (
        "Ingen ferdig fil før fristen, og ingenting ble tatt opp."
    )
    request.save(update_fields=["state", "error"])


def master_is_up(url: str) -> bool:
    call = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(call, timeout=TIMEOUT_SECONDS) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):  # 404 until it is published
        return False


def _stopped(config: dict | None) -> bool:
    # MediaMTX answers with every setting, defaults included.
    return (
        config is not None
        and not config.get("record")
        and config.get("source") in (None, "", "publisher")
    )


def _stop(request: RecordingRequest) -> None:
    """Keep the path for playback, but no longer recording or reading."""
    if receiver.path_config(request.path) is not None:
        try:
            receiver.replace_path(request.path, STOPPED)
            return
        except receiver.ReceiverUnavailable:  # gone in between: add it back
            logger.warning("Could not stop %s in place; adding it back", request.path)
    receiver.add_path(request.path, STOPPED)


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
