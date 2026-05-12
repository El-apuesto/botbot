"""
Twin Shadow - runtime/events.py
Real asyncio pub/sub event bus.

Replaces the stub that just constructed a TaskEvent and returned it.
Now actually dispatches to all registered subscriber queues.

Usage:
    # Subscribe (e.g. in the WebSocket handler)
    q = subscribe()
    async for event in iter_events(q):
        await ws.send_json(event)
    unsubscribe(q)

    # Emit from anywhere in the codebase
    await emit_event(task_id, "TASK_STARTED", {"module": "code"})
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, AsyncGenerator

from .models import TaskEvent

log = logging.getLogger("tsai.events")

# -- Subscriber registry -------------------------------------------------------

_subscribers: list[asyncio.Queue] = []
_MAX_QUEUE_SIZE = 256


def subscribe(maxsize: int = _MAX_QUEUE_SIZE) -> asyncio.Queue:
    """Register a new subscriber. Returns its dedicated Queue."""
    q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    """Remove a subscriber queue. Safe to call if already removed."""
    try:
        _subscribers.remove(q)
    except ValueError:
        pass


# -- Emit ---------------------------------------------------------------------

async def emit_event(
    task_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> dict:
    """
    Publish an event to all subscribers.

    Returns the serialized event dict (same shape as before so existing
    callers like persist_task_lifecycle stay compatible).
    """
    payload = payload or {}

    event = TaskEvent(
        task_id=task_id,
        event_type=event_type,
        payload=payload,
    )
    data = event.model_dump(mode="json")

    log.debug("[EVENT] %s - %s %s", event_type, task_id, payload)

    dead: list[asyncio.Queue] = []
    for q in _subscribers:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            log.warning(
                "[EVENT] subscriber queue full - dropping event %s for %s",
                event_type, task_id,
            )
        except Exception as exc:
            log.error("[EVENT] subscriber error: %s", exc)
            dead.append(q)

    for q in dead:
        unsubscribe(q)

    return data


# -- Convenience async iterator for WebSocket / SSE handlers ------------------

async def iter_events(
    q: asyncio.Queue,
    timeout: float = 30.0,
) -> AsyncGenerator[dict, None]:
    """
    Async-iterate over events from a subscriber queue.

    Yields each event dict. Stops when the queue is unsubscribed or on timeout.
    Wrap with try/finally to call unsubscribe(q).

    Example:
        q = subscribe()
        try:
            async for event in iter_events(q):
                await websocket.send_json(event)
        finally:
            unsubscribe(q)
    """
    while True:
        try:
            event = await asyncio.wait_for(q.get(), timeout=timeout)
            yield event
        except asyncio.TimeoutError:
            # Heartbeat / keep-alive - caller can decide what to do
            yield {"task_id": "", "event_type": "HEARTBEAT", "payload": {}}
        except asyncio.CancelledError:
            break


# -- Canonical event type constants -------------------------------------------

class EventType:
    BUILD_STARTED        = "BUILD_STARTED"
    BUILD_COMPLETED      = "BUILD_COMPLETED"
    BUILD_FAILED         = "BUILD_FAILED"

    APPROVE_STARTED      = "APPROVE_STARTED"
    APPROVE_COMPLETED    = "APPROVE_COMPLETED"
    APPROVE_FAILED       = "APPROVE_FAILED"

    MODULE_STARTED       = "MODULE_STARTED"
    MODULE_COMPLETED     = "MODULE_COMPLETED"
    MODULE_FAILED        = "MODULE_FAILED"

    AUDIT_STARTED        = "AUDIT_STARTED"
    AUDIT_PASSED         = "AUDIT_PASSED"
    AUDIT_FLAGGED        = "AUDIT_FLAGGED"

    BOARDROOM_STARTED    = "BOARDROOM_STARTED"
    BOARDROOM_ROUND_DONE = "BOARDROOM_ROUND_DONE"
    BOARDROOM_COMPLETED  = "BOARDROOM_COMPLETED"

    CROSS_VAL_STARTED    = "CROSS_VAL_STARTED"
    CROSS_VAL_COMPLETED  = "CROSS_VAL_COMPLETED"

    PROVIDER_SWITCHED    = "PROVIDER_SWITCHED"
    VALIDATION_FAILED    = "VALIDATION_FAILED"

    TASK_STARTED         = "TASK_STARTED"
    TASK_COMPLETED       = "TASK_COMPLETED"
    TASK_FAILED          = "TASK_FAILED"

    HEARTBEAT            = "HEARTBEAT"
