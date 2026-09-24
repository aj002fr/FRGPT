from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pandas as pd
import pytest

from scripts.aggression_dashboard_v3 import live_publisher
from scripts.aggression_dashboard_v3.live_publisher import (
    LivePublisher,
    _keep_newer_tail,
    _prefix_changed,
    empty_tape_warming,
    encode_sse,
    frame_has_window_print,
)
from scripts.aggression_dashboard_v3.banner import build_banner
from scripts.aggression_dashboard_v3.panels import expand_payload


@pytest.fixture(autouse=True)
def _session_opened_hours_ago(monkeypatch):
    """Synthetic tapes are 'recent'; keep them inside one session regardless of wall clock."""
    monkeypatch.setattr(
        live_publisher,
        "default_start_time",
        lambda now=None: (now or datetime.now(timezone.utc)) - timedelta(hours=6),
    )


def _empty_symbol(symbol: str) -> pd.DataFrame:
    return pd.DataFrame(
        columns=["ts", "price", "size", "side", "instrument_id", "raw_symbol", "dashboard_symbol"]
    )


def _recent_start(minutes: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def _tape(
    start: datetime,
    *,
    minutes: float = 20.0,
    n: int = 40,
    size: int = 1,
    side: str | None = None,
) -> Dict[str, pd.DataFrame]:
    rows = []
    step = (minutes * 60.0) / max(n - 1, 1)
    for i in range(n):
        rows.append({
            "ts": start + timedelta(seconds=i * step),
            "price": 110.0 + i * (1.0 / 64.0),
            "size": size,
            "side": side or ("A" if i % 2 == 0 else "B"),
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


def _publisher(
    tape: Dict[str, pd.DataFrame],
    *,
    cached: Dict[str, pd.DataFrame] | None = None,
    lookback_hours: float = 2.0,
) -> LivePublisher:
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
        lookback_hours=lookback_hours,
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


def test_rebuilds_coalesce_to_the_minimum_interval():
    from scripts.aggression_dashboard_v3 import live_publisher as lp

    pub = _publisher(_tape(_recent_start(5), minutes=5, n=10))
    assert pub._rebuild_wait_sec() == 0.0
    assert pub.rebuild() is None
    assert 0 < pub._rebuild_wait_sec() <= lp.WARMING_REBUILD_MIN_INTERVAL_SEC
    pub.assemble_trades = lambda _s, _e: (_tape(_recent_start(20), minutes=20, n=30), {}, {})
    assert pub.rebuild() is not None
    assert lp.WARMING_REBUILD_MIN_INTERVAL_SEC < pub._rebuild_wait_sec() <= lp.REBUILD_MIN_INTERVAL_SEC


def test_trade_thread_requests_rebuild_instead_of_running_it():
    pub = _publisher(_tape(_recent_start(20), minutes=20, n=30))
    calls = []
    pub.rebuild = lambda: calls.append(1)
    pub.apply_trades([{
        "ts": datetime.now(timezone.utc),
        "price": 111.0,
        "size": 1,
        "side": "B",
        "dashboard_symbol": "TY",
    }])
    assert calls == []
    assert pub._rebuild_event.is_set()


def test_history_broadcast_reuses_snapshot_bytes_then_replays_newer_prints():
    first_tape = _tape(_recent_start(20), minutes=20, n=30, size=1)
    pub = _publisher(first_tape)
    pub.rebuild()
    q = pub.subscribe()
    q.get_nowait()
    last = pub._frame.iloc[-1]
    newer = {
        "ts": pub._frame.index.max() + pd.Timedelta(milliseconds=5),
        "price": float(last["price"]) + (1.0 / 64.0),
        "size": 3,
        "side": "A",
        "dashboard_symbol": "TY",
    }
    hole = first_tape["TY"].copy()
    hole.loc[0, "size"] = 50
    pub.assemble_trades = lambda _s, _e: ({**first_tape, "TY": hole}, {}, {})
    prepare = pub._prepare_picture
    sent = []

    def arrives_mid_rebuild(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        sent.append(pub.apply_trades([newer]))
        return prepared

    pub._prepare_picture = arrives_mid_rebuild
    pub.rebuild()
    assert q.get_nowait() == sent[0]
    history = q.get_nowait()
    assert history.startswith("event: history\n")
    assert history.split("\n", 1)[1] == pub._snapshot_bytes.split("\n", 1)[1]
    replay = q.get_nowait()
    assert replay.startswith("event: trade\n")
    prints = json.loads(replay.split("data: ", 1)[1])["prints"]
    assert [p["buy_aggress"] + p["sell_aggress"] for p in prints] == [-3.0]
    assert pub._snapshot_through_ns < int(pd.Timestamp(newer["ts"]).value)


def test_catch_up_applies_prints_in_bounded_batches():
    from scripts.aggression_dashboard_v3 import live_publisher as lp

    start = _recent_start(1)
    n = lp.CATCH_UP_BATCH * 2 + 5
    rows = pd.DataFrame({
        "ts": pd.to_datetime([start + timedelta(microseconds=10 * i) for i in range(n)], utc=True),
        "price": 110.0,
        "size": 1,
        "side": "B",
        "instrument_id": "1",
        "raw_symbol": "TYZ6",
        "dashboard_symbol": "TY",
    })
    pub = _publisher({"TY": rows, "FV": _empty_symbol("FV"), "TU": _empty_symbol("TU"), "US": _empty_symbol("US")})
    batches = []
    pub.apply_trades = lambda trades: batches.append(len(trades))
    pub._catch_up_live(start - timedelta(seconds=1))
    assert batches == [lp.CATCH_UP_BATCH, lp.CATCH_UP_BATCH, 5]


def test_chunked_json_matches_one_shot_json():
    import numpy as np

    from scripts.aggression_dashboard_v3 import live_publisher as lp

    pub = _publisher(_tape(_recent_start(20), minutes=20, n=30))
    snapshot = pub.rebuild()
    one_shot = lambda value: json.dumps(value, separators=(",", ":"), default=lp._json_default)
    assert lp.encode_json_chunked(snapshot) == one_shot(snapshot)
    ts = pd.Timestamp("2026-09-24 13:30:00.123456789", tz="UTC")
    tricky = {
        "points": [{"x": f"2026-09-24T09:{i // 60:02d}:{i % 60:02d}", "y": float(i) / 3, "n": np.int64(i), "t": ts} for i in range(5003)],
        "nested": {"empty": {}, "list": [], "nan": float("nan"), "tuple": (1, 2.5, "é"), "unicode": "Δ ✓"},
        "int_keys": {1: "a", 2.5: "b", None: "c", True: "d"},
        "np": np.float64(1e-8),
        "big": list(range(4001)),
    }
    assert lp.encode_json_chunked(tricky) == one_shot(tricky)
    assert lp.encode_sse("history", snapshot).split("data: ", 1)[1] == lp.encode_json_chunked(snapshot) + "\n\n"


def test_empty_tape_warming_helper():
    banner = build_banner(
        pd.DataFrame(),
        {"ok": True, "beta_ref": 1e-8},
        price_symbol="TY",
        as_of=datetime.now(timezone.utc),
        live_connected=True,
    )
    assert empty_tape_warming(banner, pd.DataFrame())


def test_lookback_clips_the_chart_but_not_the_session_sum():
    pub = _publisher(_tape(_recent_start(20), minutes=20, n=40, side="B"), lookback_hours=0.1)
    snapshot = pub.rebuild()
    assert snapshot and snapshot.get("success")
    p1 = expand_payload(snapshot)["panels"]["p1"]["data"]
    assert 0 < len(p1) < 40
    assert p1[0]["total_aggress"] == 40 - len(p1) + 1
    assert p1[-1]["total_aggress"] == 40
    assert snapshot["meta"]["flow"]["total_aggress_final"] == 40


def test_longer_tape_keeps_values_and_sends_no_history():
    start = _recent_start(20)
    first_tape = _tape(start, minutes=18, n=36, side="B")
    pub = _publisher(first_tape, lookback_hours=0.1)
    first = pub.rebuild()
    q = pub.subscribe()
    q.get_nowait()
    extra = _tape(
        first_tape["TY"]["ts"].iloc[-1].to_pydatetime() + timedelta(seconds=30),
        minutes=1,
        n=3,
        side="B",
    )["TY"]
    longer = dict(first_tape)
    longer["TY"] = pd.concat([first_tape["TY"], extra], ignore_index=True)
    pub.assemble_trades = lambda _s, _e: (longer, {}, {})
    second = pub.rebuild()
    assert q.empty()
    before = {row["x"]: row["total_aggress"] for row in expand_payload(first)["panels"]["p1"]["data"]}
    after = {row["x"]: row["total_aggress"] for row in expand_payload(second)["panels"]["p1"]["data"]}
    shared = set(before) & set(after)
    assert shared
    assert all(before[x] == after[x] for x in shared)


def test_prefix_changed_ignores_float_noise_only():
    assert not _prefix_changed(((1, 100.0),), ((1, 100.2),))
    assert _prefix_changed(((1, 100.0),), ((1, 101.0),))
    assert not _prefix_changed(((1, 100.0),), ((2, 500.0),))
    assert _prefix_changed(((1, None),), ((1, 3.0),))


def test_newer_tail_continues_the_rebuilt_session_sum():
    session = "2026-09-23T18:00"
    rebuilt = pd.DataFrame(
        {"total_aggress": [10.0, 12.0], "delta_a": [10.0, 2.0], "session_id": [session, session]},
        index=pd.to_datetime(["2026-09-24T14:00:00Z", "2026-09-24T14:00:01Z"], utc=True),
    )
    tail = pd.DataFrame(
        {"total_aggress": [99.0, 98.0], "delta_a": [3.0, -1.0], "session_id": [session, session]},
        index=pd.to_datetime(["2026-09-24T14:00:02Z", "2026-09-24T14:00:03Z"], utc=True),
    )
    out, shift = _keep_newer_tail(rebuilt, tail)
    assert out["total_aggress"].tolist() == [10.0, 12.0, 15.0, 14.0]
    assert shift == 84.0


def test_same_timestamp_print_keeps_buysell_ewm():
    pub = _publisher(_tape(_recent_start(20), minutes=20, n=30))
    ts = pd.Timestamp("2026-09-24T14:00:00Z")
    assert pub._update_buysell_ewm(ts, 100.0) == 100.0
    later = ts + pd.Timedelta(seconds=30)
    after = pub._update_buysell_ewm(later, -100.0)
    assert after == pytest.approx(0.0)
    assert pub._update_buysell_ewm(later, 100.0) == after


def test_prints_applied_during_a_rebuild_continue_the_rebuilt_sum():
    """Warming prints sum from 0; a print applied mid-rebuild must be re-based onto the rebuilt tape."""
    start = _recent_start(30)
    hist = _tape(start, minutes=20, n=80, side="A", size=10)["TY"]
    last_ts = hist["ts"].iloc[-1]
    live_rows: list = []
    state = {"hist": False}

    def trade(sec: float, side: str = "B") -> Dict[str, Any]:
        return {"ts": last_ts + pd.Timedelta(seconds=sec), "price": 111.0, "size": 2, "side": side, "dashboard_symbol": "TY"}

    def assemble(_s, _e):
        parts = ([hist] if state["hist"] else []) + ([pd.DataFrame(live_rows)] if live_rows else [])
        ty = pd.concat(parts, ignore_index=True) if parts else _empty_symbol("TY")
        if len(ty):
            ty["ts"] = pd.to_datetime(ty["ts"], utc=True)
            ty = ty.sort_values("ts").reset_index(drop=True)
        return {"TY": ty, "FV": _empty_symbol("FV"), "TU": _empty_symbol("TU"), "US": _empty_symbol("US")}, {}, {}

    pub = _publisher({"TY": hist}, lookback_hours=1.0)
    pub.assemble_trades = assemble

    def live(sec: float) -> None:
        tr = trade(sec)
        live_rows.append({**tr, "instrument_id": "1", "raw_symbol": "TYM6"})
        pub.apply_trades([tr])

    for sec in range(1, 6):
        live(sec)
    assert pub._ag[-1] == 10.0
    state["hist"] = True
    prepare = pub._prepare_picture

    def prepare_with_a_print_arriving(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        pub.apply_trades([trade(6.5)])
        return prepared

    pub._prepare_picture = prepare_with_a_print_arriving
    q = pub.subscribe()
    pub.rebuild()
    pub._prepare_picture = prepare
    rebuilt = float(pub._frame["total_aggress"].iloc[-1])
    assert pub._ag[-1] == rebuilt + 2.0
    live(8)
    assert pub._ag[-1] == rebuilt + 4.0
    replay = pub.subscribe()
    replay.get_nowait()
    prints = json.loads(replay.get_nowait().split("data: ", 1)[1])["prints"]
    assert [p["total_aggress"] for p in prints] == [rebuilt + 2.0, rebuilt + 4.0]
    del q


def test_overflowing_subscriber_is_resynced_with_the_picture_and_replay():
    from scripts.aggression_dashboard_v3 import live_publisher as lp

    pub = _publisher(_tape(_recent_start(20), minutes=20, n=30))
    pub.rebuild()
    q = pub.subscribe()
    last_ts = pub._frame.index.max()
    for k in range(lp.SUBSCRIBER_QUEUE_MAX + 5):
        pub.apply_trades([{"ts": last_ts + pd.Timedelta(milliseconds=5 * (k + 1)), "price": 111.0, "size": 1,
                           "side": "B", "dashboard_symbol": "TY"}])
    assert pub.resyncs >= 1
    messages = []
    while not q.empty():
        messages.append(q.get_nowait())
    first_snapshot = next(i for i, m in enumerate(messages) if m.startswith("event: snapshot\n"))
    replay = json.loads(messages[first_snapshot + 1].split("data: ", 1)[1])
    after = [json.loads(m.split("data: ", 1)[1]) for m in messages[first_snapshot + 2:] if m.startswith("event: trade\n")]
    stamps = [p["x"] for p in replay["prints"]] + [p["x"] for msg in after for p in msg["prints"]]
    assert stamps == sorted(stamps) and len(stamps) == len(set(stamps)) == lp.SUBSCRIBER_QUEUE_MAX + 5


def test_trade_messages_carry_server_time_and_stream_lag():
    pub = _publisher(_tape(_recent_start(20), minutes=20, n=30))
    pub.rebuild()
    q = pub.subscribe()
    while not q.empty():
        q.get_nowait()
    encoded = pub.apply_trades([{"ts": pub._frame.index.max() + pd.Timedelta(milliseconds=2), "price": 111.0,
                                 "size": 1, "side": "B", "dashboard_symbol": "TY"}])
    payload = json.loads(encoded.split("data: ", 1)[1])
    assert payload["server_ts"] > 0 and payload["stream_lag_sec"] >= 0
