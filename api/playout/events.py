"""Relay the database's `playout_events` notifications to connected clients.

Postgres announces every schedule change itself (fk migration 0045), at
commit. Each server process holds one LISTEN connection and fans what it
hears out to its subscribers, so a change made through any process, the
admin or plain SQL reaches every client, with no broker to run.

Events say *that* something changed, and roughly when; clients refetch the
schedule over the ordinary API. A missed event therefore costs latency, not
correctness, and the hub says `resync` whenever it may have missed some.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import psycopg2
import psycopg2.extensions

logger = logging.getLogger(__name__)

CHANNEL = "playout_events"
RECONNECT_SECONDS = 2.0
QUEUE_SIZE = 64

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


class PlayoutEventHub:
    def __init__(self, connect: Callable[[], psycopg2.extensions.connection] = connect):
        self._connect = connect
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

    async def _relay(self, conn: psycopg2.extensions.connection) -> None:
        with conn.cursor() as cursor:
            cursor.execute(f"LISTEN {CHANNEL}")
        self.publish(RESYNC)

        loop = asyncio.get_running_loop()
        readable = asyncio.Event()
        loop.add_reader(conn.fileno(), readable.set)
        try:
            while True:
                await readable.wait()
                readable.clear()
                conn.poll()
                while conn.notifies:
                    notify = conn.notifies.pop(0)
                    try:
                        event = json.loads(notify.payload)
                    except ValueError:
                        logger.warning("Ignoring malformed playout event %r", notify.payload)
                        continue
                    self.publish(event)
        finally:
            loop.remove_reader(conn.fileno())


hub = PlayoutEventHub()
