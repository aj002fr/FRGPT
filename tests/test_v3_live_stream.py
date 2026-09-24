from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pandas as pd

from scripts.aggression_dashboard_v3.live_publisher import (
    LivePublisher,
    empty_tape_warming,
    encode_sse,
    frame_has_window_print,
)
from scripts.aggression_dashboard_v3.banner import build_banner


def _empty_symbol(symbol: str) -> pd.DataFrame:
    return pd.DataFrame(
        columns=["ts", "price", "size", "side", "instrument_id", "raw_symbol", "dashboard_symbol"]
    )


def _recent_start(minutes: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def _tape(start: datetime, *, minutes: float = 20.0, n: int = 40, size: int = 1) -> Dict[str, pd.DataFrame]:
    rows = []
    step = (minutes * 60.0) / max(n - 1, 1)
    for i in range(n):
        rows.append({
            "ts": start + timedelta(seconds=i * step),
            "price": 110.0 + i * (1.0 / 64.0),
            "size": size,
            "side": "A" if i % 2 == 0 else "B",
            "instrument_id": "1",
            "raw_symbol": "TYM6",
            "dashboard_symbol": "TY",
        })
    ty = pd.DataFrame(rows)
    ty["ts"] = pd.to_datetime(ty["ts"], utc=True)
    return {
        "TY": ty,
        "FV": _empty_symbol("FV"),
        "TU": _empty_symbol("TU"),
        "US": _empty_symbol("US"),
    }


def _publisher(tape: Dict[str, pd.DataFrame], *, cached: Dict[str, pd.DataFrame] | None = None) -> LivePublisher:
    def assemble(_start: datetime, _end: datetime):
        return tape, {}, {}

    def load_cached(_start: datetime, _end: datetime):
        return cached if cached is not None else {
            symbol: _empty_symbol(symbol) for symbol in ("FV", "TU", "TY", "US")
        }

    return LivePublisher(
        assemble_trades=assemble,
        load_cached_window=load_cached,
        peek_beta=lambda _now: {
            "ok": True,
            "beta_ref": 1e-8,
            "beta_display_yield_bp_per_1m": -0.32,
            "reason": None,
        },
        kick_beta=lambda: None,
        live_status=lambda: {"connected": True, "source": "accessapi", "market_open": True},
        tenday_path=lambda _beta: pd.DataFrame(),
        kick_tenday=lambda _beta, _dv01: None,
        load_dv01=lambda: {"TY": 1.0, "FV": 1.0, "TU": 1.0, "US": 1.0},
        aggression_symbols=("TY",),
        price_symbol="TY",
    )


def test_encode_sse_is_stable_bytes():
    first = encode_sse("trade", {"x": 1, "state": "QUIET"})
    second = encode_sse("trade", {"x": 1, "state": "QUIET"})
    assert first == second
    assert first.startswith("event: trade\n")


def test_empty_tape_warming_is_not_shared_snapshot():
    pub = _publisher(_tape(_recent_start(5), minutes=5, n=10))
    assert pub.rebuild() is None
    loading = pub.last_snapshot()
    assert loading is not None
    assert loading.get("success") is not True
    q = pub.subscribe()
    opening = q.get_nowait()
    assert opening.startswith("event: loading\n")


def test_two_subscribers_get_the_same_trade_bytes():
    tape = _tape(_recent_start(20), minutes=20, n=30)
    pub = _publisher(tape)
    snapshot = pub.rebuild()
    assert snapshot and snapshot.get("success")
    q1 = pub.subscribe()
    q2 = pub.subscribe()
    s1 = q1.get_nowait()
    s2 = q2.get_nowait()
    assert s1 == s2
    assert s1.startswith("event: snapshot\n")
    last = tape["TY"].iloc[-1]
    trade = {
        "ts": last["ts"] + timedelta(milliseconds=2),
        "price": float(last["price"]) + (1.0 / 64.0),
        "size": 2,
        "side": "A",
        "dashboard_symbol": "TY",
    }
    encoded = pub.apply_trades([trade])
    assert encoded and encoded.startswith("event: trade\n")
    assert q1.get_nowait() == encoded
    assert q2.get_nowait() == encoded


def test_trade_extends_strip_without_rewriting_past_runs():
    pub = _publisher(_tape(_recent_start(20), minutes=20, n=30))
    pub.rebuild()
    before = [dict(run) for run in pub._runs]
    assert before
    first = dict(before[0])
    last = pub._frame.iloc[-1]
    pub.apply_trades([{
        "ts": pub._frame.index.max() + pd.Timedelta(milliseconds=2),
        "price": float(last["price"]) + (1.0 / 64.0),
        "size": 1,
        "side": "B",
        "dashboard_symbol": "TY",
    }])
    assert pub._runs[0]["x0"] == first["x0"]
    assert pub._runs[0]["state"] == first["state"]
    assert pub._runs[0]["color"] == first["color"]


def test_hole_fill_that_changes_cumsum_is_a_history_revision():
    first_tape = _tape(_recent_start(20), minutes=20, n=30, size=1)
    pub = _publisher(first_tape)
    pub.rebuild()
    q = pub.subscribe()
    q.get_nowait()
    hole = first_tape["TY"].copy()
    hole.loc[0, "size"] = 50
    second = {
        "TY": hole,
        "FV": _empty_symbol("FV"),
        "TU": _empty_symbol("TU"),
        "US": _empty_symbol("US"),
    }
    pub.assemble_trades = lambda _s, _e: (second, {}, {})
    pub.rebuild()
    event = q.get_nowait()
    assert event.startswith("event: history\n")


def test_cached_window_can_supply_the_15m_print():
    start = _recent_start(20)
    live = _tape(start + timedelta(minutes=18), minutes=2, n=8)
    cached = _tape(start, minutes=20, n=30)
    pub = _publisher(live, cached=cached)
    snapshot = pub.rebuild()
    assert snapshot and snapshot.get("success")
    assert frame_has_window_print(pub._frame)


def test_trade_updates_shared_snapshot_tip():
    pub = _publisher(_tape(_recent_start(20), minutes=20, n=30))
    pub.rebuild()
    last = pub._frame.iloc[-1]
    pub.apply_trades([{
        "ts": pub._frame.index.max() + pd.Timedelta(milliseconds=2),
        "price": float(last["price"]) + (1.0 / 64.0),
        "size": 1,
        "side": "A",
        "dashboard_symbol": "TY",
    }])
    snap = pub.last_snapshot()
    p1 = ((snap or {}).get("panels") or {}).get("p1") or {}
    assert p1.get("data")
    assert p1["data"][-1]["x"] == snap["flow_dominance_series"][-1]["x"]


def test_empty_tape_warming_helper():
    banner = build_banner(
        pd.DataFrame(),
        {"ok": True, "beta_ref": 1e-8},
        price_symbol="TY",
        as_of=datetime.now(timezone.utc),
        live_connected=True,
    )
    assert empty_tape_warming(banner, pd.DataFrame())
