from __future__ import annotations

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
from scripts.aggression_dashboard_v3.banner import GROUP_STYLE, build_banner


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
    pub = _publisher(_tape(_recent_start(3), minutes=3, n=10))
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
    assert pub._group_runs
    assert pub._group_runs[0]["state"] in GROUP_STYLE


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


def test_lookback_clips_the_chart_but_not_the_session_sum():
    pub = _publisher(_tape(_recent_start(20), minutes=20, n=40, side="B"), lookback_hours=0.1)
    snapshot = pub.rebuild()
    assert snapshot and snapshot.get("success")
    p1 = snapshot["panels"]["p1"]["data"]
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
    before = {row["x"]: row["total_aggress"] for row in first["panels"]["p1"]["data"]}
    after = {row["x"]: row["total_aggress"] for row in second["panels"]["p1"]["data"]}
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
    assert shift == pytest.approx(84.0)


def test_prints_applied_during_rebuild_continue_the_rebuilt_sum():
    pub = _publisher(_tape(_recent_start(20), minutes=20, n=30))
    pub.rebuild()
    session = pub._sid[-1]
    last = pub._ag[-1]
    rows = [
        {"ts_ns": 1, "delta_a": 2.0, "total_aggress": last + 500.0, "session_id": session},
        {"ts_ns": 2, "delta_a": -1.0, "total_aggress": last + 499.0, "session_id": session},
    ]
    points = [{"total_aggress": row["total_aggress"], "implied_price": None} for row in rows]
    shift = pub._continue_kept(rows, points, (110.0, 0.0), {"ok": True, "beta_ref": 1e-8})
    assert [row["total_aggress"] for row in rows] == [last + 2.0, last + 1.0]
    assert [point["total_aggress"] for point in points] == [last + 2.0, last + 1.0]
    assert shift == pytest.approx(498.0)
    assert points[0]["implied_price"] == pytest.approx(110.0 + 1e-8 * (last + 2.0))


def test_same_timestamp_print_keeps_buysell_ewm():
    pub = _publisher(_tape(_recent_start(20), minutes=20, n=30))
    ts = pd.Timestamp("2026-09-24T14:00:00Z")
    assert pub._update_buysell_ewm(ts, 100.0) == 100.0
    later = ts + pd.Timedelta(seconds=30)
    after = pub._update_buysell_ewm(later, -100.0)
    assert after == pytest.approx(0.0)
    assert pub._update_buysell_ewm(later, 100.0) == after


class _FakePublisher:
    def __init__(self, key):
        self.key = key
        self.subs = 0
        self.started = False
        self.stopped = False
        self.trades = []

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def subscriber_count(self):
        return self.subs

    def offer_trade(self, trade):
        self.trades.append(trade)

    def offer_quote(self):
        pass

    def offer_history(self):
        pass


@pytest.fixture
def v3app(monkeypatch):
    from collections import OrderedDict

    from scripts.aggression_dashboard_v3 import app

    monkeypatch.setattr(app, "_PUBLISHERS", {})
    monkeypatch.setattr(app, "_PUBLISHER_IDLE_SINCE", {})
    monkeypatch.setattr(app, "_TENDAY_PATHS", OrderedDict())
    monkeypatch.setattr(app, "_make_publisher", _FakePublisher)
    monkeypatch.setattr(app.LIVE_BUFFER, "set_hooks", lambda **_kwargs: None)
    return app


def test_live_key_from_query_args(v3app):
    assert v3app.live_key_from_args({}) == v3app.DEFAULT_LIVE_KEY
    assert v3app.live_key_from_args({"chips": "TY"}) == ("TY", ("TY",), True)
    assert v3app.live_key_from_args({"chips": "us, ty,zz", "price": "fv", "dv01": "0"}) == ("FV", ("TY", "US"), False)
    assert v3app.live_key_from_args({"chips": " , "}) == v3app.DEFAULT_LIVE_KEY
    assert v3app.live_key_from_args({"price": "ZZ", "chips": "TY"}) == ("TY", ("TY",), True)


def test_registry_reuses_equal_selections(v3app):
    first = v3app._ensure_publisher(v3app.live_key_from_args({"chips": "TY,US"}))
    again = v3app._ensure_publisher(v3app.live_key_from_args({"chips": "US,TY"}))
    assert first is again and first.started
    assert v3app._ensure_publisher() is not first


def test_registry_caps_extras_and_replaces_the_oldest(v3app):
    default = v3app._ensure_publisher()
    ty = v3app._ensure_publisher(("TY", ("TY",), True), claim=True)
    us = v3app._ensure_publisher(("TY", ("US",), True), claim=True)
    ty.subs = us.subs = 1
    fv = v3app._ensure_publisher(("TY", ("FV",), True), claim=True)
    assert ty.stopped and not us.stopped and not default.stopped
    assert set(v3app._PUBLISHERS) == {v3app.DEFAULT_LIVE_KEY, ("TY", ("US",), True), ("TY", ("FV",), True)}
    assert fv.started


def test_idle_extra_views_stop_after_ten_minutes(v3app):
    default = v3app._ensure_publisher()
    key = ("TY", ("TY",), True)
    ty = v3app._ensure_publisher(key, claim=True)
    v3app._note_viewer_left(key, ty)
    v3app._note_viewer_left(v3app.DEFAULT_LIVE_KEY, default)
    since = v3app._PUBLISHER_IDLE_SINCE[key]
    assert v3app.reap_idle_live_views(now=since + v3app.LIVE_VIEW_IDLE_SEC - 1) == []
    assert v3app.reap_idle_live_views(now=since + v3app.LIVE_VIEW_IDLE_SEC) == [key]
    assert ty.stopped and not default.stopped
    assert set(v3app._PUBLISHERS) == {v3app.DEFAULT_LIVE_KEY}


def test_returning_viewer_is_not_reaped(v3app):
    key = ("TY", ("TY",), True)
    ty = v3app._ensure_publisher(key, claim=True)
    v3app._note_viewer_left(key, ty)
    since = v3app._PUBLISHER_IDLE_SINCE[key]
    assert v3app._ensure_publisher(key, claim=True) is ty
    assert v3app.reap_idle_live_views(now=since + v3app.LIVE_VIEW_IDLE_SEC) == []


def test_trades_fan_out_to_every_live_view(v3app):
    default = v3app._ensure_publisher()
    ty = v3app._ensure_publisher(("TY", ("TY",), True))
    trade = {"ts": "2026-09-24T14:00:00Z", "dashboard_symbol": "FV", "price": 108.0, "size": 1, "side": "A"}
    v3app._fan_out_trade(trade)
    assert default.trades == [trade]
    assert ty.trades == [trade]


def test_ty_only_publisher_ignores_wing_prints():
    pub = _publisher(_tape(_recent_start(20), minutes=20, n=30))
    assert pub.aggression_symbols == ("TY",)
    wing = {"ts": pd.Timestamp.now(tz="UTC"), "dashboard_symbol": "FV", "price": 108.0, "size": 5, "side": "A"}
    assert pub._apply_one(wing) is None


def test_stream_replaces_the_oldest_extra_instead_of_refusing(v3app):
    v3app._ensure_publisher()
    oldest = v3app._ensure_publisher(("TY", ("TY",), True), claim=True)
    v3app._ensure_publisher(("TY", ("US",), True), claim=True).subs = 1
    oldest.subs = 1
    publisher = v3app._ensure_publisher(v3app.live_key_from_args({"chips": "FV"}), claim=True)
    assert oldest.stopped and publisher.started
    assert ("TY", ("FV",), True) in v3app._PUBLISHERS


def test_tenday_path_cache_holds_several_selections(v3app):
    frames = {name: pd.DataFrame({"x": [i]}) for i, name in enumerate(("curve", "ty", "us", "fv"))}
    v3app._store_tenday_path("curve", frames["curve"])
    v3app._store_tenday_path("ty", frames["ty"])
    assert v3app._snapshot_tenday_path("curve") is frames["curve"]
    assert v3app._snapshot_tenday_path("ty") is frames["ty"]
    v3app._store_tenday_path("us", frames["us"])
    v3app._store_tenday_path("fv", frames["fv"])
    assert v3app._snapshot_tenday_path("curve").empty
    assert v3app._snapshot_tenday_path("ty") is frames["ty"]
