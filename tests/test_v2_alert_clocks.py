"""Focused tests for offline V2.7 alert-clock helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v27.alerts import TICK_SIZE as LIVE_TICK
from scripts.aggression_v2_alert_clock_research import (
    TICK_SIZE,
    Clock,
    deadline_index,
    frozen_clocks,
    locked_holdout_dates,
    price_share,
    rank_holdout,
    replay_day,
    resolve_index,
)


def _minute_day(n: int = 30, prints: float = 10.0, z_pattern: str = "open_then_reset") -> pd.DataFrame:
    ts = pd.date_range("2026-03-02 14:00", periods=n, freq="1min", tz="UTC")
    z = np.zeros(n)
    residual = np.zeros(n)
    price = np.full(n, 110.5)
    if z_pattern == "open_then_reset":
        z[:6] = 1.8
        z[6:] = 0.2
        residual[:6] = 2 * TICK_SIZE
        residual[6:] = 0.2 * TICK_SIZE
        price[2] = 110.5 + 2 * TICK_SIZE
        price[6:] = 110.5 - TICK_SIZE
    elif z_pattern == "never_reset":
        z[:] = 1.8
        residual[:] = 2 * TICK_SIZE
    fv = price - residual
    return pd.DataFrame({
        "bucket_ts": ts,
        "et_date": "2026-03-02",
        "price": price,
        "fair_value": fv,
        "residual": residual,
        "residual_z": z,
        "prints": prints,
        "volume": prints * 4.0,
        "price_tick": np.where(np.concatenate([[0.0], np.abs(np.diff(price)) >= 0.5 * TICK_SIZE]), 1.0, 0.0),
        "tod_minute": np.arange(n) + 9 * 60,
    })


def test_tick_size_matches_live_v27() -> None:
    assert TICK_SIZE == LIVE_TICK == 1.0 / 64.0


def test_wall_deadline_is_exact_minutes() -> None:
    day = _minute_day()
    ts = day["bucket_ts"].to_numpy(dtype="datetime64[ns]")
    prints = day["prints"].to_numpy(dtype=float)
    clock = Clock("wall_10m", "wall", "wall", 10.0)
    assert deadline_index(clock, ts, prints, prints, prints, day["tod_minute"].to_numpy(), 0, {}) == 10


def test_print_deadline_skips_open_bar() -> None:
    day = _minute_day(prints=10.0)
    ts = day["bucket_ts"].to_numpy(dtype="datetime64[ns]")
    prints = day["prints"].to_numpy(dtype=float)
    clock = Clock("prints_50", "prints", "prints", 50.0)
    # 10 prints/min after the open bar -> 5 bars
    assert deadline_index(clock, ts, prints, prints, prints, day["tod_minute"].to_numpy(), 0, {}) == 5


def test_resolve_respects_five_minute_min_hold() -> None:
    day = _minute_day()
    ts = day["bucket_ts"].to_numpy(dtype="datetime64[ns]")
    abs_z = np.abs(day["residual_z"].to_numpy(dtype=float))
    # z drops at minute 6, after the 5-minute hold
    assert resolve_index(abs_z, ts, 0) == 6


def test_replay_blocks_until_deadline_not_resolve() -> None:
    day = _minute_day(n=25)
    day.loc[day.index >= 8, "residual_z"] = 1.8
    clock = Clock("wall_10m", "wall", "wall", 10.0)
    episodes = replay_day(day, clock, {})
    assert len(episodes) >= 2
    assert episodes[0]["reversion_status"] == "resolved"
    assert abs(episodes[0]["minutes_to_deadline"] - 10.0) < 1e-9
    assert episodes[0]["opened_ts"].startswith("2026-03-02T14:00:00")
    assert episodes[1]["opened_ts"].startswith("2026-03-02T14:11:00")
    assert episodes[0]["dir_pnl_ticks"] == 1.0
    assert episodes[0]["mae_to_resolve_ticks"] == 2.0
    assert abs(float(episodes[0]["price_share_of_reversion"]) - 0.5) < 1e-9


def test_uncapped_prints_censor_at_day_end() -> None:
    day = _minute_day(n=8, prints=1.0, z_pattern="never_reset")
    clock = Clock("prints_1500", "prints", "prints", 1500.0)
    episodes = replay_day(day, clock, {})
    assert len(episodes) == 1
    assert episodes[0]["reversion_status"] == "censored"
    assert episodes[0].get("dir_pnl") is None


def test_price_share_zero_when_only_fv_moves() -> None:
    assert price_share(0.05, 110.5, 110.5) == 0.0


def test_holdout_is_last_20_percent() -> None:
    dates = [f"2026-01-{i:02d}" for i in range(1, 11)]
    assert locked_holdout_dates(dates) == ["2026-01-09", "2026-01-10"]


def test_frozen_grid_covers_families() -> None:
    clocks = frozen_clocks()
    families = {c.family for c in clocks}
    assert families == {"wall", "prints", "volume", "ticks", "adaptive", "hybrid"}
    assert len(clocks) >= 30
    assert any(c.id == "wall_10m" for c in clocks)


def test_usable_rank_drops_one_minute_print_clocks() -> None:
    rows = [
        {"clock_id": "prints_100", "split": "holdout", "n_resolved": 13, "median_minutes_to_deadline": 1.0, "scored_hit_rate": 0.29, "avg_dir_pnl_ticks": 0.05, "p_adverse_le_3": 0.99},
        {"clock_id": "wall_10m", "split": "holdout", "n_resolved": 340, "median_minutes_to_deadline": 10.0, "scored_hit_rate": 0.41, "avg_dir_pnl_ticks": 0.03, "p_adverse_le_3": 0.93},
        {"clock_id": "tickmins_3", "split": "holdout", "n_resolved": 232, "median_minutes_to_deadline": 5.5, "scored_hit_rate": 0.53, "avg_dir_pnl_ticks": 0.09, "p_adverse_le_3": 0.96},
    ]
    ranked = rank_holdout(rows)
    assert [r["clock_id"] for r in ranked] == ["tickmins_3", "wall_10m"]
