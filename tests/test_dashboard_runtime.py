"""Local-runtime serving helpers: one build per (key, tick), last-good, gzip/ETag; v22/v27 producers."""

from __future__ import annotations

import gzip
import json
import threading
import time

import pandas as pd
import pytest
from flask import Flask

from scripts.dashboard_runtime.serve import ResponseCache, cached_response


def test_concurrent_requests_share_one_build_per_tick() -> None:
    cache = ResponseCache(wait_sec=5)
    calls = []

    def builder():
        calls.append(1)
        time.sleep(0.2)
        return {"n": len(calls)}, 200

    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get("k", 1, builder))) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and len({id(r) for r in results}) == 1
    assert cache.get("k", 1, builder) is results[0] and len(calls) == 1
    cache.get("k", 2, builder)
    assert len(calls) == 2


def test_failed_rebuild_serves_last_good() -> None:
    cache = ResponseCache(wait_sec=1)
    good = cache.get("k", 1, lambda: ({"ok": True}, 200))

    def broken():
        raise RuntimeError("share offline")

    assert cache.get("k", 2, broken) is good
    assert cache.get("k", 3, lambda: ({"ok": False}, 500)) is good
    with pytest.raises(RuntimeError):
        cache.get("other", 1, broken)


def test_cached_response_gzip_and_etag() -> None:
    app = Flask(__name__)
    cache = ResponseCache()

    @app.get("/x")
    def x():
        return cached_response(cache, "x", 1, lambda: ({"value": 42, "when": pd.Timestamp("2026-09-24").isoformat()}, 200))

    client = app.test_client()
    first = client.get("/x", headers={"Accept-Encoding": "gzip"})
    assert first.status_code == 200 and first.headers["Content-Encoding"] == "gzip"
    assert json.loads(gzip.decompress(first.data)) == {"value": 42, "when": "2026-09-24T00:00:00"}
    again = client.get("/x", headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304
    plain = client.get("/x")
    assert plain.get_json()["value"] == 42


@pytest.mark.parametrize("module_name", ["scripts.aggression_dashboard_v22.app", "scripts.aggression_dashboard_v27.app"])
def test_live_requests_read_the_producer_result(module_name, monkeypatch) -> None:
    import importlib

    mod = importlib.import_module(module_name)
    monkeypatch.setattr(mod, "_ensure_live", lambda: None)
    monkeypatch.setattr(mod, "_evaluate_live_once", lambda: pytest.fail("requests must not evaluate"))
    idx = pd.date_range("2026-09-24 13:00", periods=3, freq="1min", tz="UTC")
    model_df = pd.DataFrame({"price": [110.0, 110.1, 110.2], "fair_value": [110.0] * 3,
                             "residual": [0.0, 0.1, 0.2], "residual_z": [0.0, 0.5, 1.0]}, index=idx)
    lifo = {"state": "stub"}
    monkeypatch.setitem(mod._PRODUCED, "result", (model_df, {"ok": True}, {"stats": {}}, lifo))
    mod._PRODUCED_READY.set()
    client = mod.app.test_client()
    for _ in range(3):
        body = client.get("/api/live_signal").get_json()
        assert body["success"] is True and body["latest"]["residual_z"] == 1.0
    assert client.get("/api/lifo").get_json()["state"] == "stub"
