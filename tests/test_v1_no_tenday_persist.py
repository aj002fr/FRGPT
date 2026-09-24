"""V1 keeps ten-day Databento bars in memory only; flow history gets live rows alone."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

APP_PATH = Path(__file__).resolve().parents[1] / "scripts" / "aggression_dashboard" / "app.py"


def _persist_sources() -> list[str | None]:
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    sources: list[str | None] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "persist_completed_bars":
            source = next((kw.value for kw in node.keywords if kw.arg == "source"), None)
            sources.append(source.value if isinstance(source, ast.Constant) else None)
    return sources


def test_only_live_persist_calls_remain() -> None:
    sources = _persist_sources()
    assert "tenday" not in sources
    assert sources.count("live") == 2
    assert set(sources) == {"live"}


class _Stop(Exception):
    pass


def _bars(symbol: str) -> pd.DataFrame:
    idx = pd.date_range(datetime.now(timezone.utc) - timedelta(minutes=5), periods=3, freq="1min")
    return pd.DataFrame({"buy_contracts": [1, 2, 3], "sell_contracts": [0, 1, 0]}, index=idx)


def test_tenday_refresher_fills_memory_without_persisting(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.aggression_dashboard import app as app_module

    calls: list[dict] = []
    monkeypatch.setattr(app_module, "persist_completed_bars", lambda *a, **k: calls.append(k) or 0)
    monkeypatch.setattr(app_module, "fetch_curve_trades", lambda *a, **k: ({"TY": pd.DataFrame({"ts": []})}, {}))
    monkeypatch.setattr(app_module, "trades_to_symbol_minute_bars", lambda df, symbol, **k: (_bars(symbol), {}))

    def stop(_seconds):
        raise _Stop

    monkeypatch.setattr(app_module.time_module, "sleep", stop)
    with app_module._TENDAY_LOCK:
        saved = dict(app_module._TENDAY)
        app_module._TENDAY["fit_date"] = None
    try:
        with pytest.raises(_Stop):
            app_module._tenday_refresher()
        with app_module._TENDAY_LOCK:
            assert set(app_module._TENDAY["bars"]) == {"TY"}
    finally:
        with app_module._TENDAY_LOCK:
            app_module._TENDAY.clear()
            app_module._TENDAY.update(saved)
    assert calls == []


def test_startup_seed_persists_live_session_bars_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.aggression_dashboard import app as app_module

    calls: list[dict] = []
    monkeypatch.setattr(app_module, "persist_completed_bars", lambda bars, **k: calls.append({"symbols": set(bars), **k}) or 0)
    monkeypatch.setattr(app_module, "backfill_from_trade_cache", lambda: {"skipped": True})
    with app_module._TENDAY_LOCK, app_module._SESSION_1M_LOCK:
        saved_tenday = dict(app_module._TENDAY)
        saved_session = dict(app_module._SESSION_1M)
        app_module._TENDAY["bars"] = {"TY": _bars("TY")}
        app_module._TENDAY["covered_end"] = datetime.now(timezone.utc)
        app_module._SESSION_1M["bars"] = {"FV": _bars("FV")}
        app_module._SESSION_1M["rebuild_from"] = datetime.now(timezone.utc)
    try:
        app_module._flow_history_backfill()
    finally:
        with app_module._TENDAY_LOCK, app_module._SESSION_1M_LOCK:
            app_module._TENDAY.clear()
            app_module._TENDAY.update(saved_tenday)
            app_module._SESSION_1M.clear()
            app_module._SESSION_1M.update(saved_session)
    assert [call["source"] for call in calls] == ["live"]
    assert calls[0]["symbols"] == {"FV"}
