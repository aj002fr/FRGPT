"""Starlette front for V3: the live stream is async fan-out; every other route is the Flask app.

Each viewer is one coroutine draining an AsyncSubscriber that the publisher fills from its own
threads, so a viewer costs a coroutine instead of a server thread blocked in write(), and every
message queued since the last write goes out in one write.
"""

from __future__ import annotations

import asyncio
import collections
import queue
import threading
from typing import Any, Callable, List

from a2wsgi import WSGIMiddleware
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Mount, Route

STREAM_PREAMBLE = "retry: 5000\n: connected\n" + (" " * 2048) + "\n\n"
STREAM_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class AsyncSubscriber:
    """Bounded FIFO with the queue.Queue calls the publisher makes (put_nowait/get_nowait raising
    Full/Empty). Filled from publisher threads; drained by one coroutine on `loop`."""

    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int) -> None:
        self._loop = loop
        self.maxsize = maxsize
        self._items: collections.deque[str] = collections.deque()
        self._lock = threading.Lock()
        self._ready = asyncio.Event()

    def put_nowait(self, item: str) -> None:
        with self._lock:
            if len(self._items) >= self.maxsize:
                raise queue.Full
            self._items.append(item)
        self._loop.call_soon_threadsafe(self._ready.set)

    def get_nowait(self) -> str:
        with self._lock:
            if not self._items:
                raise queue.Empty
            return self._items.popleft()

    def empty(self) -> bool:
        with self._lock:
            return not self._items

    async def drain(self, timeout: float) -> List[str]:
        """Everything queued, after waiting up to `timeout` for the first item ([] on timeout)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            with self._lock:
                if self._items:
                    items = list(self._items)
                    self._items.clear()
                    return items
                # A put after this releases the lock schedules set(); earlier set() callbacks
                # may still be pending and wake us spuriously, hence the loop.
                self._ready.clear()
            remaining = deadline - loop.time()
            if remaining <= 0:
                return []
            try:
                await asyncio.wait_for(self._ready.wait(), remaining)
            except asyncio.TimeoutError:
                return []


def build_app(flask_app: Any, get_publisher: Callable[[], Any], *, heartbeat_sec: float, queue_max: int) -> Starlette:
    async def live_stream(request: Request) -> StreamingResponse:
        publisher = await run_in_threadpool(get_publisher)
        subscriber = AsyncSubscriber(asyncio.get_running_loop(), queue_max)
        await run_in_threadpool(publisher.subscribe, subscriber)

        async def body():
            try:
                yield STREAM_PREAMBLE
                while True:
                    items = await subscriber.drain(heartbeat_sec)
                    yield "".join(items) if items else ": heartbeat\n\n"
            finally:
                publisher.unsubscribe(subscriber)

        return StreamingResponse(body(), media_type="text/event-stream", headers=STREAM_HEADERS)

    return Starlette(routes=[
        Route("/api/live/stream", live_stream),
        Mount("/", app=WSGIMiddleware(flask_app)),
    ])
