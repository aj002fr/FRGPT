"""Tests for isolated Aggression V2.7 10-minute local fair value."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v27.alerts import (
    GATE_FLAT_TEXT,
    HORIZON_MINUTES,
    MIN_HOLD_MINUTES,
    RESET_Z,
    SESSION_OPEN_HOUR_ET,
    STATS_CUTOVER_ET,
    STORE_DIR as ALERT_STORE,
    TICK_SIZE,
    WATCH_Z,
    AlertStore,
    attach_session_residual,
    backfill_trade_pnl,
    build_trade_signal,
    chart_marks,
    conviction_entry_gate,
    episodes_in_session,
    session_start_et,
    visible_chart_episodes,
    visible_session_start_et,
)
from scripts.aggression_dashboard_v27.app import PORT, STRATEGY_STATUS
from scripts.aggression_dashboard_v27.features import ANCHOR_MIN, FEATURE_SET, add_local_nowcast_columns
from scripts.aggression_dashboard_v27.lifo_store import STORE_DIR as LIFO_STORE
from scripts.aggression_dashboard_v27.model_state import CONSTRUCTION


def _with_session(frame: pd.DataFrame, ticks: float = 3.0) -> pd.DataFrame:
    out = frame.copy()
    resid = pd.to_numeric(out["residual"], errors="coerce")
    sign = np.where(resid.to_numpy() >= 0, 1.0, -1.0)
    out["session_residual"] = sign * ticks * TICK_SIZE
    return out


def test_isolation_and_frozen_sweet_spot() -> None:
    alert = str(ALERT_STORE).replace("\\", "/")
    lifo = str(LIFO_STORE).replace("\\", "/")
    assert alert.endswith("workspace/analysis/aggression_v27_live")
    assert lifo.endswith("workspace/analysis/aggression_v27_live")
    assert "aggression_v2_live" not in alert.replace("aggression_v27_live", "")
    assert PORT == 5063
    assert CONSTRUCTION == "B1_W10"
    assert ANCHOR_MIN == 10
    assert HORIZON_MINUTES == 10
    assert MIN_HOLD_MINUTES == 5
    assert WATCH_Z == 1.5
    assert RESET_Z == 0.5
    assert "agg_30m" not in FEATURE_SET
    assert "minutes_since_open" not in FEATURE_SET
    assert "LOCAL-10M-FV" in STRATEGY_STATUS


def test_local_anchor_has_no_lookahead() -> None:
    n = 20
    df = pd.DataFrame({
        "price": 110.0 + np.arange(n) * (1.0 / 64.0),
        "agg_1m": np.linspace(1.0, 2.0, n),
    })
    out = add_local_nowcast_columns(df)
    assert out["local_anchor"].iloc[:10].isna().all()
    assert abs(float(out["local_anchor"].iloc[10]) - float(df["price"].iloc[0])) < 1e-12
    assert abs(float(out["target_local_delta"].iloc[10]) - float(df["price"].iloc[10] - df["price"].iloc[0])) < 1e-12
    assert float(out["minutes_since_anchor"].iloc[10]) == 10.0


def test_alert_waits_min_hold_then_exits_at_half_z(tmp_path: Path) -> None:
    store = AlertStore(path=tmp_path / "alert_episodes.json")
    idx = pd.date_range("2026-08-31 14:00", periods=12, freq="1min", tz="UTC")
    z = np.concatenate([[2.0] * 3, [0.2] * 9])
    frame = _with_session(pd.DataFrame(
        {
            "price": 110.0 + np.arange(12) * 0.001,
            "fair_value": 110.0,
            "residual": 0.02,
            "residual_z": z,
        },
        index=idx,
    ))
    store.update(frame.iloc[:3])
    open_ep = store._active_episode()
    assert open_ep is not None
    assert open_ep["status"] == "open"
    store.update(frame.iloc[:4])
    still_open = store._active_episode()
    assert still_open is not None
    assert still_open.get("reversion_status") == "pending"
    store.update(frame)
    done = [e for e in store.episodes if e.get("reversion_status") == "resolved"]
    assert done
    assert float(done[0]["minutes_to_close"]) >= 5.0
    assert pd.Timestamp(done[0]["deadline_ts"]) - pd.Timestamp(done[0]["opened_ts"]) == pd.Timedelta(minutes=10)
    assert done[0]["status"] == "finalized"
    assert done[0]["exit_reason"] == "Z_RESET"
    assert done[0]["dir_pnl_trade"] is not None
    assert store._active_episode() is None


def test_each_entry_exit_is_its_own_trade(tmp_path: Path) -> None:
    store = AlertStore(path=tmp_path / "alert_episodes.json")
    idx = pd.date_range("2026-08-31 14:00", periods=16, freq="1min", tz="UTC")
    z = np.array([1.8] * 6 + [0.2] * 2 + [1.8] * 8, dtype=float)
    price = np.array([110.50 + i * 0.015625 for i in range(16)])
    frame = _with_session(pd.DataFrame(
        {"price": price, "fair_value": 110.47, "residual": 0.03, "residual_z": z},
        index=idx,
    ))
    for i in range(len(frame)):
        store.update(frame.iloc[: i + 1])
    assert len(store.episodes) == 1
    first = store.episodes[0]
    assert first["status"] == "finalized"
    expected = -1.0 * (float(first["close_price"]) - float(first["open_price"]))
    assert abs(float(first["dir_pnl_trade"]) - expected) < 1e-12
    assert store._last_gate.get("skip_reason") == "SAME_SIGN_OPEN"


def test_trade_signal_names_entry_and_two_exits(tmp_path: Path) -> None:
    store = AlertStore(path=tmp_path / "alert_episodes.json")
    idx = pd.date_range("2026-08-31 14:00", periods=12, freq="1min", tz="UTC")
    frame = _with_session(pd.DataFrame(
        {
            "price": 110.5,
            "fair_value": 110.47,
            "residual": 0.03,
            "residual_z": np.concatenate([[1.8] * 6, [0.2] * 6]),
        },
        index=idx,
    ))
    store.update(frame.iloc[:1])
    opened = store._active_episode()
    signal = build_trade_signal(opened, store.episodes, idx[2])
    assert signal["phase"] == "IN_TRADE"
    assert signal["entry_action"] == "ENTER SHORT"
    assert "FLIP" in signal["exit_text"]
    assert "10m mark" in signal["exit_text"]
    assert "EXIT 10M" not in signal["exit_text"]
    store.update(frame)
    resolved = [e for e in store.episodes if e.get("reversion_status") == "resolved"][0]
    after = build_trade_signal(resolved if resolved.get("status") == "open" else None, store.episodes, idx[-1])
    if after["phase"] == "Z_EXITED":
        assert after["exit_action"] == "EXIT Z-RESET"
    marks = chart_marks(store.episodes)
    labels = {m["label"] for m in marks}
    assert "ENTER SHORT" in labels
    assert "EXIT Z-RESET" in labels


def test_long_exits_on_opposite_flip_before_half_z(tmp_path: Path) -> None:
    store = AlertStore(path=tmp_path / "alert_episodes.json")
    idx = pd.date_range("2026-09-01 14:00", periods=5, freq="1min", tz="UTC")
    z = np.array([-1.8, -1.6, 1.2, 1.3, 0.2])
    frame = _with_session(pd.DataFrame(
        {"price": 107.5 + np.arange(5) * 0.015625, "fair_value": 107.53, "residual": -0.03, "residual_z": z},
        index=idx,
    ))
    for i in range(len(frame)):
        store.update(frame.iloc[: i + 1])
    done = store.episodes[0]
    assert done["exit_reason"] == "FLIP"
    assert done["status"] == "finalized"
    assert pd.Timestamp(done["closed_ts"]) == idx[2]


def test_ten_minute_mark_does_not_flatten(tmp_path: Path) -> None:
    store = AlertStore(path=tmp_path / "alert_episodes.json")
    idx = pd.date_range("2026-09-02 16:00", periods=16, freq="1min", tz="UTC")
    frame = _with_session(pd.DataFrame(
        {
            "price": 110.5,
            "fair_value": 110.47,
            "residual": 0.03,
            "residual_z": np.full(16, 1.8),
        },
        index=idx,
    ))
    for i in range(len(frame)):
        store.update(frame.iloc[: i + 1])
    open_ep = store._active_episode()
    assert open_ep is not None
    assert open_ep["status"] == "open"
    assert open_ep.get("exit_reason") != "CLOCK_10M"
    assert open_ep.get("horizon_10m_marked") is True
    assert pd.Timestamp(open_ep["deadline_ts"]) - pd.Timestamp(open_ep["opened_ts"]) == pd.Timedelta(minutes=10)
    signal = build_trade_signal(open_ep, store.episodes, idx[-1])
    assert "still open" in signal["now_text"]
    labels = {m["label"] for m in chart_marks([open_ep])}
    assert "10M MARK" in labels
    assert "EXIT 10M" not in labels


def test_backfill_trade_pnl_from_entry_exit_prices() -> None:
    episode = {
        "open_price": 107.640625,
        "close_price": 107.65625,
        "direction_sign": 1.0,
        "reversion_status": "resolved",
        "minutes_to_close": 6.0,
    }
    backfill_trade_pnl(episode)
    assert abs(episode["dir_pnl_trade"] - 0.015625) < 1e-12
    assert abs(episode["dir_pnl_trade_ticks"] - 1.0) < 1e-12


def test_visible_chart_episodes_keep_only_open_and_last() -> None:
    open_ep = {"id": "open", "status": "open"}
    older = {"id": "old", "status": "finalized"}
    last = {"id": "last", "status": "finalized"}
    chosen = visible_chart_episodes([older, last, open_ep], open_ep)
    assert [e["id"] for e in chosen] == ["open", "last"]


def test_trade_signal_markup_is_in_v27_page() -> None:
    html = Path("scripts/aggression_dashboard_v27/templates/index.html").read_text(encoding="utf-8")
    assert 'id="trade-signal"' in html
    assert 'id="trade-entry"' in html
    assert 'id="trade-exit"' in html
    assert 'id="session-day"' in html
    assert 'id="session-ticks"' in html
    assert "session 2–6 ticks" in html
    assert "10m is a mark, not a flatten" in html


def test_session_day_counts_ticks_since_6pm(tmp_path: Path) -> None:
    now = pd.Timestamp("2026-09-01 15:30", tz="America/New_York")
    start = session_start_et(now)
    assert SESSION_OPEN_HOUR_ET == 18
    assert start == pd.Timestamp("2026-08-31 18:00", tz="America/New_York")
    store = AlertStore(path=tmp_path / "alert_episodes.json")
    store.episodes = [
        {
            "opened_ts": "2026-08-31T21:00:00-04:00",
            "status": "finalized",
            "state": "WATCH",
            "dir_pnl_trade_ticks": 2.0,
        },
        {
            "opened_ts": "2026-08-31T17:00:00-04:00",
            "status": "finalized",
            "state": "WATCH",
            "dir_pnl_trade_ticks": 9.0,
        },
        {
            "opened_ts": "2026-09-01T10:00:00-04:00",
            "status": "open",
            "state": "WATCH",
        },
    ]
    stats = store._session_day_stats(store.episodes, now)
    assert stats["n_issued"] == 2
    assert stats["n_closed"] == 1
    assert stats["n_open"] == 1
    assert stats["ticks_captured"] == 2.0
    assert stats["n_win"] == 1
    kept = episodes_in_session(store.episodes, start)
    assert [e["opened_ts"] for e in kept] == [
        "2026-08-31T21:00:00-04:00",
        "2026-09-01T10:00:00-04:00",
    ]
    assert visible_session_start_et(now) == start


def test_session_stats_cutover_this_session_only(tmp_path: Path) -> None:
    now = pd.Timestamp("2026-09-02 12:30", tz="America/New_York")
    assert visible_session_start_et(now) == STATS_CUTOVER_ET
    store = AlertStore(path=tmp_path / "alert_episodes.json")
    store.episodes = [
        {
            "opened_ts": "2026-09-02T10:00:00-04:00",
            "status": "finalized",
            "state": "WATCH",
            "dir_pnl_trade_ticks": 4.0,
        },
        {
            "opened_ts": "2026-09-02T12:15:00-04:00",
            "status": "finalized",
            "state": "WATCH",
            "dir_pnl_trade_ticks": 1.0,
        },
        {
            "opened_ts": "2026-09-02T12:20:00-04:00",
            "status": "open",
            "state": "WATCH",
        },
    ]
    stats = store._session_day_stats(store.episodes, now)
    assert stats["stats_window"] == "gate_cutover"
    assert stats["n_issued"] == 2
    assert stats["n_closed"] == 1
    assert stats["n_open"] == 1
    assert stats["ticks_captured"] == 1.0
    later = pd.Timestamp("2026-09-02 19:00", tz="America/New_York")
    assert visible_session_start_et(later) == pd.Timestamp("2026-09-02 18:00", tz="America/New_York")
    later_stats = store._session_day_stats(store.episodes, later)
    assert later_stats["stats_window"] == "session"
    assert later_stats["n_issued"] == 0


def test_conviction_gate_reasons() -> None:
    local = 3 * TICK_SIZE
    assert conviction_entry_gate(local, None) == "SESSION_UNAVAILABLE"
    assert conviction_entry_gate(local, 1 * TICK_SIZE) == "NOWCAST_ONLY"
    assert conviction_entry_gate(local, 7 * TICK_SIZE) == "REGIME_TAIL"
    assert conviction_entry_gate(local, -3 * TICK_SIZE) == "DISAGREE"
    assert conviction_entry_gate(local, 3 * TICK_SIZE) is None
    assert conviction_entry_gate(local, 3 * TICK_SIZE, last_exit_session_sign=1.0) == "SAME_SIGN_OPEN"
    assert conviction_entry_gate(local, 3 * TICK_SIZE, last_exit_session_sign=-1.0) is None


def test_nowcast_only_and_disagree_do_not_open(tmp_path: Path) -> None:
    store = AlertStore(path=tmp_path / "alert_episodes.json")
    idx = pd.date_range("2026-09-01 14:00", periods=2, freq="1min", tz="UTC")
    hot = pd.DataFrame(
        {"price": 110.5, "fair_value": 110.47, "residual": 0.03, "residual_z": 1.8, "session_residual": 1 * TICK_SIZE},
        index=idx,
    )
    store.update(hot.iloc[:1])
    assert store.episodes == []
    assert store._last_gate["skip_reason"] == "NOWCAST_ONLY"
    disagree = hot.copy()
    disagree["session_residual"] = -3 * TICK_SIZE
    store.update(disagree.iloc[:1])
    assert store.episodes == []
    assert store._last_gate["skip_reason"] == "DISAGREE"
    signal = build_trade_signal(None, store.episodes, idx[0], store._last_gate)
    assert "SKIP DISAGREE" in signal["entry_text"]
    assert GATE_FLAT_TEXT.startswith("FLAT")


def test_regime_tail_skip(tmp_path: Path) -> None:
    store = AlertStore(path=tmp_path / "alert_episodes.json")
    idx = pd.date_range("2026-09-01 14:00", periods=1, freq="1min", tz="UTC")
    frame = pd.DataFrame(
        {"price": 110.5, "fair_value": 110.47, "residual": 0.03, "residual_z": 1.8, "session_residual": 8 * TICK_SIZE},
        index=idx,
    )
    store.update(frame)
    assert store.episodes == []
    assert store._last_gate["skip_reason"] == "REGIME_TAIL"


def test_missing_session_residual_skips(tmp_path: Path) -> None:
    store = AlertStore(path=tmp_path / "alert_episodes.json")
    idx = pd.date_range("2026-09-01 14:00", periods=1, freq="1min", tz="UTC")
    frame = pd.DataFrame(
        {"price": 110.5, "fair_value": 110.47, "residual": 0.03, "residual_z": 1.8},
        index=idx,
    )
    store.update(frame)
    assert store.episodes == []
    assert store._last_gate["skip_reason"] == "SESSION_UNAVAILABLE"


def test_attach_session_residual_keeps_existing() -> None:
    idx = pd.date_range("2026-09-01 14:00", periods=1, freq="1min", tz="UTC")
    frame = pd.DataFrame({"residual": [0.03], "session_residual": [3 * TICK_SIZE]}, index=idx)
    out = attach_session_residual(frame)
    assert abs(float(out["session_residual"].iloc[0]) - 3 * TICK_SIZE) < 1e-12


def test_reentry_after_session_sign_flip(tmp_path: Path) -> None:
    store = AlertStore(path=tmp_path / "alert_episodes.json")
    idx = pd.date_range("2026-08-31 14:00", periods=16, freq="1min", tz="UTC")
    z = np.array([1.8] * 6 + [0.2] * 2 + [-1.8] * 8, dtype=float)
    residual = np.array([0.03] * 8 + [-0.03] * 8)
    frame = pd.DataFrame(
        {
            "price": 110.50 + np.arange(16) * 0.015625,
            "fair_value": 110.47,
            "residual": residual,
            "residual_z": z,
            "session_residual": np.sign(residual) * 3 * TICK_SIZE,
        },
        index=idx,
    )
    for i in range(len(frame)):
        store.update(frame.iloc[: i + 1])
    assert len(store.episodes) == 2
    assert store.episodes[0]["status"] == "finalized"
    assert store.episodes[1]["direction"] == "LONG_CHEAP"
