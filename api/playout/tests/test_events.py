"""The playout event stream, from the database trigger to the SSE response.

Notifications are delivered only once a transaction commits, so the tests
that need one use a transactional database and write from a thread of their
own, as a request handled by another worker would.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test import AsyncClient, Client
from django.urls import reverse

from api.playout import events
from api.playout.events import PlayoutEventHub
from fk.models import LiveSource, LiveTransmission, Organization, Scheduleitem

STARTTIME = datetime(2026, 10, 31, 18, tzinfo=UTC)


def book(offset: timedelta = timedelta(0)) -> Scheduleitem:
    try:
        return Scheduleitem.objects.create(
            default_name="Live",
            starttime=STARTTIME + offset,
            duration=timedelta(minutes=30),
            schedulereason=Scheduleitem.REASON_ADMIN,
        )
    finally:
        # This runs in a worker thread with a connection of its own.
        connection.close()


def notifications(conn, timeout: float = 5.0) -> list[dict]:
    import select

    select.select([conn], [], [], timeout)
    conn.poll()
    received = [json.loads(n.payload) for n in conn.notifies]
    conn.notifies.clear()
    return received


@pytest.mark.django_db(transaction=True)
def test_a_committed_change_is_announced_with_its_window() -> None:
    listener = events.connect()
    try:
        with listener.cursor() as cursor:
            cursor.execute(f"LISTEN {events.CHANNEL}")

        item = book()
        [created] = notifications(listener)
        assert created == {
            "type": "schedule.changed",
            "table": "fk_scheduleitem",
            "op": "INSERT",
            "count": 1,
            "from": "2026-10-31T18:00:00+00:00",
            "until": "2026-10-31T18:30:00+00:00",
        }

        # A move announces both where it was and where it went.
        Scheduleitem.objects.filter(pk=item.pk).update(starttime=STARTTIME + timedelta(hours=1))
        [moved] = notifications(listener)
        assert (moved["op"], moved["count"]) == ("UPDATE", 1)
        assert (moved["from"], moved["until"]) == (
            "2026-10-31T18:00:00+00:00",
            "2026-10-31T19:30:00+00:00",
        )

        # A statement that touches nothing announces nothing.
        Scheduleitem.objects.filter(pk=-1).update(default_name="nothing")
        assert notifications(listener, timeout=0.5) == []
    finally:
        listener.close()


@pytest.mark.django_db(transaction=True)
def test_booking_a_live_source_is_announced_with_its_items_window() -> None:
    item = book()
    source = LiveSource.objects.create(
        organization=Organization.objects.create(name="Live org"), name="Studio"
    )
    listener = events.connect()
    try:
        with listener.cursor() as cursor:
            cursor.execute(f"LISTEN {events.CHANNEL}")
        LiveTransmission.objects.create(schedule_item=item, source=source)
        [booked] = notifications(listener)
        assert (booked["table"], booked["op"]) == ("fk_livetransmission", "INSERT")
        assert (booked["from"], booked["until"]) == (
            "2026-10-31T18:00:00+00:00",
            "2026-10-31T18:30:00+00:00",
        )
    finally:
        listener.close()


@pytest.mark.django_db(transaction=True)
def test_the_hub_relays_changes_to_every_subscriber() -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        hub = PlayoutEventHub()
        async with hub.subscribe() as first, hub.subscribe() as second:
            # LISTEN is in place once the hub says so.
            assert (await asyncio.wait_for(first.get(), 5))["type"] == "resync"
            assert (await asyncio.wait_for(second.get(), 5))["type"] == "resync"
            await asyncio.to_thread(book)
            got = [await asyncio.wait_for(q.get(), 5) for q in (first, second)]
        assert hub._listener is not None
        hub._listener.cancel()
        return [e["type"] for e in got], [e["op"] for e in got]

    types, ops = asyncio.run(scenario())
    assert types == ["schedule.changed", "schedule.changed"]
    assert ops == ["INSERT", "INSERT"]


def test_a_client_too_far_behind_is_told_to_resync() -> None:
    async def scenario() -> list[dict]:
        hub = PlayoutEventHub(connect=lambda: pytest.fail("not listening in this test"))
        queue: asyncio.Queue = asyncio.Queue(events.QUEUE_SIZE)
        hub._subscribers.add(queue)
        for n in range(events.QUEUE_SIZE + 1):
            hub.publish({"type": "schedule.changed", "n": n})
        return [queue.get_nowait() for _ in range(queue.qsize())]

    assert asyncio.run(scenario()) == [events.RESYNC]


@pytest.mark.django_db(transaction=True)
def test_the_stream_greets_then_relays(monkeypatch) -> None:
    monkeypatch.setattr("api.playout.views.hub", PlayoutEventHub())

    async def scenario() -> str:
        response = await AsyncClient().get(reverse("api-playout-events"))
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"
        assert "no-cache" in response["Cache-Control"]
        chunks = aiter(response.streaming_content)  # type: ignore[attr-defined]
        received = [await anext(chunks), await anext(chunks)]
        await asyncio.to_thread(book)
        received.append(await asyncio.wait_for(anext(chunks), 5))
        await chunks.aclose()
        return "".join(c.decode() if isinstance(c, bytes) else c for c in received)

    body = asyncio.run(scenario())
    assert body.startswith("retry: 2000\nevent: hello\n")
    assert "event: resync\n" in body
    assert "event: schedule.changed\n" in body


def test_wsgi_refuses_to_hold_a_worker_on_the_stream() -> None:
    response = Client().get(reverse("api-playout-events"))
    assert response.status_code == 503
