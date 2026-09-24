"""Live V1 payload cache: extra tabs share one rebuild within the TTL."""

from __future__ import annotations

from datetime import datetime, timezone

from scripts.aggression_dashboard.app import (
    LIVE_RESULT_CACHE_TTL_SEC,
    _LIVE_RESULT_CACHE,
    _LIVE_REBUILD_LOCK,
    _cached_live_aggression_http,
)
from scripts.aggression_dashboard.core import AggressionRequest


def _req(**overrides) -> AggressionRequest:
    payload = {
        "aggression_symbols": ("FV", "TU", "TY", "US"),
        "price_symbol": "TY",
        "start": datetime(2026, 9, 8, 22, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 9, 9, 21, 0, tzinfo=timezone.utc),
        "lookback_hours": 2.0,
    }
    payload.update(overrides)
    return AggressionRequest(**payload)


def test_live_cache_ttl_is_one_second() -> None:
    assert LIVE_RESULT_CACHE_TTL_SEC == 1.0


def test_second_identical_live_request_reuses_payload(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_build(req):
        calls["n"] += 1
        return {"success": True, "n": calls["n"]}, 200

    from scripts.aggression_dashboard import app as app_module

    monkeypatch.setattr(app_module, "_build_live_aggression_http", fake_build)
    with _LIVE_REBUILD_LOCK:
        _LIVE_RESULT_CACHE.clear()

    first = _cached_live_aggression_http(_req())
    second = _cached_live_aggression_http(_req())
    assert first == ({"success": True, "n": 1}, 200)
    assert second == first
    assert calls["n"] == 1


def test_different_lookback_does_not_share_cache(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_build(req):
        calls["n"] += 1
        return {"success": True, "lookback": req.lookback_hours}, 200

    from scripts.aggression_dashboard import app as app_module

    monkeypatch.setattr(app_module, "_build_live_aggression_http", fake_build)
    with _LIVE_REBUILD_LOCK:
        _LIVE_RESULT_CACHE.clear()

    _cached_live_aggression_http(_req(lookback_hours=2.0))
    _cached_live_aggression_http(_req(lookback_hours=6.0))
    assert calls["n"] == 2
