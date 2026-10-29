"""Server-sent events for playout and the on-air graphics.

Lives under /api/playout rather than with the schedule because it is
playout's channel: today it carries schedule changes, later whatever else
playout needs pushed to it.
"""

import asyncio
import json
from collections.abc import AsyncIterator

from django.core.handlers.asgi import ASGIRequest
from django.http import HttpRequest, HttpResponse, HttpResponseBase, StreamingHttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from api.playout.events import Event, hub

# Comfortably inside every proxy idle timeout between us and the client, so
# a quiet stream is never mistaken for a dead one.
HEARTBEAT_SECONDS = 15.0
# How long an EventSource waits before reconnecting after a drop.
RETRY_MILLISECONDS = 2000


def format_event(event: Event) -> str:
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event)}\n\n"


async def event_stream() -> AsyncIterator[str]:
    async with hub.subscribe() as queue:
        # Whatever the client knew before it connected may be stale.
        yield f"retry: {RETRY_MILLISECONDS}\n" + format_event({"type": "hello"})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield format_event(event)


@require_GET
@never_cache
async def events(request: HttpRequest) -> HttpResponseBase:
    """Stream playout events.

    Every event means "refetch what you show": `hello` on connect, `resync`
    when changes may have been missed, `schedule.changed` with the affected
    window (`from`, `until`) when schedule items or live transmissions change.
    """
    # Under WSGI an endless response would hold a worker for good. The chart
    # routes this path to its ASGI deployment; anything else is a mistake.
    if not isinstance(request, ASGIRequest):
        return HttpResponse(
            "Playout events are only served by the ASGI deployment.\n",
            status=503,
            content_type="text/plain",
        )
    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    # Keep any proxy from holding events back to fill a buffer.
    response["X-Accel-Buffering"] = "no"
    return response
