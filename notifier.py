"""
notifier.py
In-process pub/sub using asyncio.Queue.
The background worker calls notify(job_id, status) when a job finishes.
SSE endpoints subscribe and block until they receive the notification.
"""

import asyncio
from collections import defaultdict
from typing import Literal

# job_id → list of queues (one per connected SSE client)
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)


def subscribe(job_id: str) -> asyncio.Queue:
    """Register a new SSE listener for a job. Returns a queue to await on."""
    q: asyncio.Queue = asyncio.Queue()
    _subscribers[job_id].append(q)
    return q


def unsubscribe(job_id: str, q: asyncio.Queue) -> None:
    """Remove a listener (called when SSE client disconnects)."""
    try:
        _subscribers[job_id].remove(q)
    except ValueError:
        pass
    if not _subscribers[job_id]:
        del _subscribers[job_id]


def notify(job_id: str, status: Literal["done", "error"], detail: str = "") -> None:
    """
    Called by the background worker when a job completes or fails.
    Puts a message into every waiting SSE client queue for that job.
    Uses call_soon_threadsafe because the worker runs in a ThreadPoolExecutor,
    not the main asyncio event loop.
    """
    import threading

    payload = {"status": status, "detail": detail}

    def _put():
        for q in list(_subscribers.get(job_id, [])):
            q.put_nowait(payload)

    # The worker thread doesn't own the event loop — schedule safely
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(_put)
        else:
            _put()
    except RuntimeError:
        _put()