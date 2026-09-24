"""V3 Starlette stream: async subscriber semantics and an end-to-end uvicorn stream."""

from __future__ import annotations

import asyncio
import json
import queue
import socket
import threading
import time
import urllib.request

import pandas as pd
import pytest

from scripts.aggression_dashboard_v3.asgi import AsyncSubscriber, build_app
from tests import test_v3_live_stream as harness


def test_async_subscriber_bounds_drains_and_wakes() -> None:
    async def run() -> None:
        sub = AsyncSubscriber(asyncio.get_running_loop(), maxsize=2)
        assert await sub.drain(0.05) == []
        sub.put_nowait("a")
        sub.put_nowait("b")
        with pytest.raises(queue.Full):
            sub.put_nowait("c")
        assert await sub.drain(0.05) == ["a", "b"]
        with pytest.raises(queue.Empty):
            sub.get_nowait()
        threading.Timer(0.05, sub.put_nowait, args=("late",)).start()
        assert await sub.drain(2.0) == ["late"]

    asyncio.run(run())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_uvicorn_stream_sends_the_picture_then_trades() -> None:
    import uvicorn
    from flask import Flask

    pub = harness._publisher(harness._tape(harness._recent_start(20), minutes=20, n=30))
    pub.rebuild()
    flask_app = Flask("stub")
    flask_app.add_url_rule("/ping", "ping", lambda: "pong")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(build_app(flask_app, lambda: pub, heartbeat_sec=0.5, queue_max=128),
                                           host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=5).read() == b"pong"
        stream = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/live/stream", timeout=10)
        events, event, sent = [], None, False
        while len([e for e in events if e[0] == "trade"]) < 1 and time.time() < deadline + 10:
            line = stream.readline().decode("utf-8").rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event:
                events.append((event, json.loads(line[6:])))
                event = None
                if not sent:
                    sent = True
                    pub.apply_trades([{"ts": pub._frame.index.max() + pd.Timedelta(milliseconds=3), "price": 111.0,
                                       "size": 1, "side": "B", "dashboard_symbol": "TY"}])
        stream.close()
        assert events[0][0] == "snapshot" and events[0][1]["success"]
        trade = next(payload for kind, payload in events if kind == "trade")
        assert trade["prints"] and trade["server_ts"] > 0
        deadline = time.time() + 5
        while pub._subs and time.time() < deadline:
            time.sleep(0.05)
        assert not pub._subs
    finally:
        server.should_exit = True
        thread.join(timeout=10)
