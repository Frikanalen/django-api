"""Relay the database's `playout_events` notifications to connected clients.

Postgres announces every schedule change itself (fk migration 0045), at
commit. Each server process holds one LISTEN connection and fans what it
hears out to its subscribers, so a change made through any process, the
admin or plain SQL reaches every client, with no broker to run.

Events say *that* something changed, and roughly when; clients refetch the
schedule over the ordinary API. A missed event therefore costs latency, not
correctness, and the hub says `resync` whenever it may have missed some.

A LISTEN connection can also go deaf without an error: on 17 Nov 2026,
after a database failover, a schedule change never reached playout though
the hub was connected and listening. So the hub pings itself through the
database every PING_SECONDS, from a connection of its own as a real change
would come, and starts over (with a `resync`) if it doesn't hear the ping
within PING_TIMEOUT. Pings are not passed on.
"""

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import psycopg2
import psycopg2.extensions

logger = logging.getLogger(__name__)

CHANNEL = "playout_events"
RECONNECT_SECONDS = 2.0
QUEUE_SIZE = 64
PING_SECONDS = 30.0
PING_TIMEOUT = 15.0
PING = "ping"

# Sent once LISTEN is (re-)established: anything that changed while it was
# not is unknown, so every client should refetch.
RESYNC: dict[str, Any] = {"type": "resync"}

Event = dict[str, Any]


def connect() -> psycopg2.extensions.connection:
    """A dedicated autocommit connection, so LISTEN takes effect at once.

    TCP keepalives make a silently dropped connection show up as an error
    within half a minute instead of leaving the hub deaf indefinitely.
    """
    from django.db import connections

    params = connections["default"].get_connection_params()
    # django-prometheus's cursor wrapper expects a Django connection.
    params.pop("cursor_factory", None)
    params.update(keepalives=1, keepalives_idle=10, keepalives_interval=5, keepalives_count=3)
    conn = psycopg2.connect(**params)
    conn.autocommit = True
    return conn


class DeafListener(Exception):
    """The LISTEN connection did not hear the hub's own ping."""


class PlayoutEventHub:
    def __init__(
        self,
        connect: Callable[[], psycopg2.extensions.connection] = connect,
        ping_seconds: float = PING_SECONDS,
        ping_timeout: float = PING_TIMEOUT,
    ):
        self._connect = connect
        self._ping_seconds = ping_seconds
        self._ping_timeout = ping_timeout
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._listener: asyncio.Task[None] | None = None

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(QUEUE_SIZE)
        self._subscribers.add(queue)
        # Started on first use rather than at import, because it needs the
        # running event loop. It then runs for the life of the process.
        if self._listener is None or self._listener.done():
            self._listener = asyncio.create_task(self._listen())
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def publish(self, event: Event) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A client this far behind has missed events; what it needs
                # now is one instruction to start over, not the backlog.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(RESYNC)

    async def _listen(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                conn = await loop.run_in_executor(None, self._connect)
            except Exception:
                logger.exception("Could not connect to listen for playout events")
                await asyncio.sleep(RECONNECT_SECONDS)
                continue
            try:
                await self._relay(conn)
            except Exception:
                logger.exception("Lost the playout events connection")
            finally:
                conn.close()
            await asyncio.sleep(RECONNECT_SECONDS)

    def _ping(self, token: str) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                payload = json.dumps({"type": PING, "token": token})
                cursor.execute("SELECT pg_notify(%s, %s)", [CHANNEL, payload])
        finally:
            conn.close()

    async def _relay(self, conn: psycopg2.extensions.connection) -> None:
        with conn.cursor() as cursor:
            cursor.execute(f"LISTEN {CHANNEL}")
        self.publish(RESYNC)

        loop = asyncio.get_running_loop()
        readable = asyncio.Event()
        loop.add_reader(conn.fileno(), readable.set)
        awaiting: str | None = None  # the token of the ping not yet heard
        due = loop.time() + self._ping_seconds  # the next ping, or its deadline
        try:
            while True:
                try:
                    await asyncio.wait_for(readable.wait(), max(due - loop.time(), 0))
                except TimeoutError:
                    if awaiting is not None:
                        raise DeafListener(f"no ping back in {self._ping_timeout} s") from None
                    awaiting = uuid.uuid4().hex
                    await loop.run_in_executor(None, self._ping, awaiting)
                    due = loop.time() + self._ping_timeout
                    continue
                readable.clear()
                conn.poll()
                while conn.notifies:
                    notify = conn.notifies.pop(0)
                    try:
                        event = json.loads(notify.payload)
                    except ValueError:
                        logger.warning("Ignoring malformed playout event %r", notify.payload)
                        continue
                    if event.get("type") == PING:
                        if awaiting is not None and event.get("token") == awaiting:
                            awaiting = None
                            due = loop.time() + self._ping_seconds
                        continue
                    self.publish(event)
        finally:
            loop.remove_reader(conn.fileno())


hub = PlayoutEventHub()
