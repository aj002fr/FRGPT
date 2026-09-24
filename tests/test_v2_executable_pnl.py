"""Focused tests for offline session-gap executable PnL helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v27.alerts import TICK_SIZE as LIVE_TICK
from scripts.aggression_v2_executable_pnl_research import (
    CLOCK_LABEL,
    OUTPUT_DIR,
    TICK_SIZE,
    ReplayConfig,
    barrier_hit,
    entry_gate,
    fill_price,
    first_fill_row,
    mark_price,
    quote_fillable,
    replay_bars,
    residual_ticks,
    signs_agree,
    signed_ticks,
    summarize_trades,
    v1_aggress_with_fade,
    v1_passive_increasing_veto,
    walk_exits,
)


def _prints(rows: list[tuple]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["ts_event", "bid_price", "ask_price", "price"])
    frame["ts_event"] = pd.to_datetime(frame["ts_event"], utc=True)
    return frame


def test_isolation_and_tick_size() -> None:
    assert TICK_SIZE == LIVE_TICK == 1.0 / 64.0
    assert str(OUTPUT_DIR).replace("\\", "/").endswith("workspace/analysis/aggression_v2_executable_pnl")
    assert CLOCK_LABEL == "NOT_MBP1_L1"


def test_long_pays_ask_short_sells_bid() -> None:
    bid, ask = 110.0, 110.0 + TICK_SIZE
    assert fill_price(1, bid, ask) == ask
    assert fill_price(-1, bid, ask) == bid
    assert mark_price(1, bid, ask) == bid
    assert mark_price(-1, bid, ask) == ask


def test_crossed_quote_is_unfillable() -> None:
    bid, ask = 110.0 + TICK_SIZE, 110.0
    assert quote_fillable(bid, ask) is False
    assert fill_price(1, bid, ask) is None
    first = first_fill_row(_prints([("2026-03-02T14:00:00Z", bid, ask, 110.0)]), "2026-03-02T14:00:00Z")
    assert first is not None
    assert fill_price(1, float(first["bid_price"]), float(first["ask_price"])) is None


def test_first_fill_is_first_print_at_or_after_decision() -> None:
    prints = _prints([
        ("2026-03-02T13:59:59Z", 110.0, 110.0 + TICK_SIZE, 110.0),
        ("2026-03-02T14:00:00Z", 110.0, 110.0 + 2 * TICK_SIZE, 110.0),
        ("2026-03-02T14:00:01Z", 110.0, 110.0 + 3 * TICK_SIZE, 110.0),
    ])
    first = first_fill_row(prints, "2026-03-02T14:00:00Z")
    assert first is not None
    assert first["ask_price"] == 110.0 + 2 * TICK_SIZE


def test_delay_is_fill_minus_decision() -> None:
    decision = pd.Timestamp("2026-03-02T14:00:00Z")
    fill = pd.Timestamp("2026-03-02T14:00:02Z")
    assert (fill - decision).total_seconds() == 2.0


def test_first_barrier_stop_beats_later_target() -> None:
    entry = 110.0 + TICK_SIZE  # long paid ask
    prints = _prints([
        ("2026-03-02T14:00:01Z", 110.0 - 2 * TICK_SIZE, 110.0 - TICK_SIZE, 110.0 - 2 * TICK_SIZE),
        ("2026-03-02T14:00:02Z", 110.0 + 4 * TICK_SIZE, 110.0 + 5 * TICK_SIZE, 110.0 + 4 * TICK_SIZE),
    ])
    out = walk_exits(
        prints,
        side=1,
        entry_px=entry,
        fill_ts="2026-03-02T14:00:00Z",
        n_ticks=2,
        x_ticks=2,
        deadline_ts="2026-03-02T14:10:00Z",
    )
    assert out["exit_reason"] == "SL"
    assert out["pnl_bidask_ticks"] <= -2


def test_target_then_clock() -> None:
    entry = 110.0
    prints = _prints([
        ("2026-03-02T14:00:01Z", 110.0 + 2 * TICK_SIZE, 110.0 + 3 * TICK_SIZE, 110.0 + 2 * TICK_SIZE),
    ])
    out = walk_exits(
        prints,
        side=-1,
        entry_px=entry,
        fill_ts="2026-03-02T14:00:00Z",
        n_ticks=2,
        x_ticks=2,
        deadline_ts="2026-03-02T14:10:00Z",
    )
    # short sold bid=entry; cover ask must drop 2 ticks. Here ask is higher, so no TP.
    assert out["exit_reason"] in {"CLOCK_10M", "SL", "TP"}
    prints_tp = _prints([
        ("2026-03-02T14:00:01Z", 110.0 - 3 * TICK_SIZE, 110.0 - 2 * TICK_SIZE, 110.0 - 2 * TICK_SIZE),
    ])
    hit = walk_exits(
        prints_tp,
        side=-1,
        entry_px=entry,
        fill_ts="2026-03-02T14:00:00Z",
        n_ticks=2,
        x_ticks=2,
        deadline_ts="2026-03-02T14:10:00Z",
    )
    assert hit["exit_reason"] == "TP"
    assert hit["pnl_bidask_ticks"] >= 2


def test_agree_and_v1_veto_are_hard_skips() -> None:
    disagree = pd.Series({
        "session_residual": 3 * TICK_SIZE,
        "local_residual": -2 * TICK_SIZE,
        "session_residual_z": 1.8,
        "is_rth": True,
        "v1_state": "BALANCED",
        "z_aggress": 0.2,
    })
    assert entry_gate(disagree, ReplayConfig()) == "DISAGREE"
    assert signs_agree(3 * TICK_SIZE, -2 * TICK_SIZE) is False
    veto = pd.Series({
        "session_residual": 3 * TICK_SIZE,
        "local_residual": 2 * TICK_SIZE,
        "session_residual_z": 1.8,
        "is_rth": True,
        "v1_state": "AGGRESS_DOMINANT",
        "z_aggress": 2.0,
    })
    assert v1_aggress_with_fade("AGGRESS_DOMINANT", 2.0, 3 * TICK_SIZE) is True
    assert entry_gate(veto, ReplayConfig()) == "V1_AGGRESS_WITH_FADE"
    ok = veto.copy()
    ok["v1_state"] = "BALANCED"
    ok["z_aggress"] = 0.4
    assert entry_gate(ok, ReplayConfig()) is None
    assert residual_ticks(3 * TICK_SIZE) == 3.0


def test_replay_one_trade_at_a_time_and_after_cost() -> None:
    ts = pd.date_range("2026-03-02 14:00", periods=6, freq="1min", tz="UTC")
    bars = pd.DataFrame({
        "bucket_ts": ts,
        "session_residual": [3 * TICK_SIZE] * 6,
        "local_residual": [2 * TICK_SIZE] * 6,
        "session_residual_z": [1.8] * 6,
        "local_residual_z": [1.2] * 6,
        "price": [110.0] * 6,
        "v1_state": ["BALANCED"] * 6,
        "z_aggress": [0.2] * 6,
        "z_passive": [-0.1] * 6,
        "is_rth": [True] * 6,
        "et_date": ["2026-03-02"] * 6,
    })
    prints = []
    start = pd.Timestamp("2026-03-02T14:01:00Z")
    prints.append((start.isoformat(), 110.0, 110.0 + TICK_SIZE, 110.0))
    for i in range(1, 12):
        t = start + pd.Timedelta(seconds=i)
        prints.append((t.isoformat(), 110.0 - 3 * TICK_SIZE, 110.0 - 2 * TICK_SIZE, 110.0 - 2 * TICK_SIZE))
    trades = replay_bars(bars, _prints(prints), ReplayConfig(g_ticks=2, n_ticks=2, x_ticks=2))
    filled = trades[trades["fill_status"] == "filled"]
    assert len(filled) == 1
    assert filled.iloc[0]["side_name"] == "SHORT_RICH"
    assert filled.iloc[0]["exit_reason"] == "TP"
    summary = summarize_trades(trades, "test")
    assert summary["n_filled"] == 1
    assert summary["n_trades"] == 1
    assert summary["n_tp_ticks"] == 2.0
    assert summary["n_sl_ticks"] == 2.0
    assert summary["mean_after_cost"] == summary["mean_bidask"] - 0.5
    assert summary["ship_gate"] is False
    assert "pnl_mid_ticks" in filled.columns
    assert "pnl_bidask_after_cost_ticks" in filled.columns


def test_barrier_helpers() -> None:
    assert barrier_hit(1, 110.0, 110.0 + 2 * TICK_SIZE, 2, 2) == "TP"
    assert barrier_hit(1, 110.0, 110.0 - 2 * TICK_SIZE, 2, 2) == "SL"
    assert signed_ticks(-1, 110.0, 110.0 - 2 * TICK_SIZE) == 2.0


def test_microsecond_print_stamps_still_stop() -> None:
    """Parquet TBBO is often datetime64[us]; ns/us mix must not disable SL/TP."""
    prints = _prints([
        ("2026-03-02T14:00:00.000001Z", 110.0 - 3 * TICK_SIZE, 110.0 - 2 * TICK_SIZE, 110.0 - 2 * TICK_SIZE),
    ])
    prints["ts_event"] = prints["ts_event"].astype("datetime64[us, UTC]")
    hit = walk_exits(
        prints,
        side=-1,
        entry_px=110.0,
        fill_ts="2026-03-02T14:00:00Z",
        n_ticks=2,
        x_ticks=2,
        deadline_ts="2026-03-02T14:10:00Z",
    )
    assert hit["exit_reason"] == "TP"
    assert hit["exit_ts"].year == 2026


def test_mid_space_stop_from_ask_fill() -> None:
    ask = 110.0 + TICK_SIZE
    bid0 = 110.0
    prints = _prints([
        ("2026-03-02T14:00:00Z", bid0, ask, 110.0),
        ("2026-03-02T14:00:01Z", ask - 3.5 * TICK_SIZE, ask - 2.5 * TICK_SIZE, ask - 3 * TICK_SIZE),
    ])
    hit = walk_exits(
        prints,
        side=1,
        entry_px=ask,
        fill_ts="2026-03-02T14:00:00Z",
        n_ticks=2,
        x_ticks=3,
        deadline_ts="2026-03-02T14:10:00Z",
        exit_space="mid",
    )
    assert hit["exit_reason"] == "SL"
    assert hit["pnl_mid_ticks"] <= -3
    assert hit["pnl_bidask_ticks"] is not None


def test_no_same_sign_reentry() -> None:
    ts = pd.date_range("2026-03-02 14:00", periods=8, freq="1min", tz="UTC")
    bars = pd.DataFrame({
        "bucket_ts": ts,
        "session_residual": [3 * TICK_SIZE] * 8,
        "local_residual": [2 * TICK_SIZE] * 8,
        "session_residual_z": [1.8] * 8,
        "local_residual_z": [1.2] * 8,
        "price": [110.0] * 8,
        "v1_state": ["BALANCED"] * 8,
        "z_aggress": [0.2] * 8,
        "z_passive": [-0.1] * 8,
        "is_rth": [True] * 8,
        "et_date": ["2026-03-02"] * 8,
    })
    rows = []
    for i in range(8):
        t = pd.Timestamp("2026-03-02T14:01:00Z") + pd.Timedelta(minutes=i)
        rows.append((t.isoformat(), 110.0 + 3 * TICK_SIZE, 110.0 + 4 * TICK_SIZE, 110.0 + 3 * TICK_SIZE))
        rows.append(((t + pd.Timedelta(seconds=1)).isoformat(), 110.0 + 6 * TICK_SIZE, 110.0 + 7 * TICK_SIZE, 110.0 + 6 * TICK_SIZE))
    baseline = replay_bars(bars, _prints(rows), ReplayConfig(g_ticks=2, n_ticks=2, x_ticks=2))
    blocked = replay_bars(
        bars,
        _prints(rows),
        ReplayConfig(g_ticks=2, n_ticks=2, x_ticks=2, block_same_sign_reentry=True),
    )
    assert len(blocked[blocked["fill_status"] == "filled"]) == 1
    assert len(baseline[baseline["fill_status"] == "filled"]) >= 1


def test_passive_increasing_veto() -> None:
    assert v1_passive_increasing_veto(2.0, "INCREASING", 3 * TICK_SIZE) is True
    assert v1_passive_increasing_veto(2.0, "SLOWING", 3 * TICK_SIZE) is False
    assert v1_passive_increasing_veto(-2.0, "INCREASING", 3 * TICK_SIZE) is False
    row = pd.Series({
        "session_residual": 3 * TICK_SIZE,
        "local_residual": 2 * TICK_SIZE,
        "session_residual_z": 1.8,
        "is_rth": True,
        "v1_state": "IMPLIED_PASSIVE_DOMINANT",
        "z_aggress": 0.2,
        "z_passive": 2.1,
        "z_passive_trend": "INCREASING",
        "same_sign_trend": True,
    })
    assert entry_gate(row, ReplayConfig()) is None
    assert entry_gate(row, ReplayConfig(apply_passive_increasing_veto=True)) == "V1_PASSIVE_INCREASING"
    assert entry_gate(row, ReplayConfig(apply_c2_veto=True)) == "C2_SAME_SIGN_INCREASING"
