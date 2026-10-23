"""Tests for E02 price-only fade policies."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.simulation.policies import (
    entry_first_1tick_retrace,
    entry_immediate,
    entry_nonpositive_velocity,
    simulate_fade_from_entry,
)


def _panel_with_retrace() -> tuple[pd.DataFrame, dict]:
    idx = pd.date_range("2026-04-01 10:00", periods=40, freq="1min", tz="UTC")
    price = np.array([100.0 + i * (1 / 64) for i in range(10)] + [100.0 + 8 * (1 / 64)] * 5)
    # then 2-tick retrace then flat
    top = 100.0 + 9 * (1 / 64)
    price = np.concatenate(
        [
            np.array([100.0 + i * (1 / 64) for i in range(10)]),
            np.array([top, top - 1 / 64, top - 2 / 64]),
            np.full(27, top - 2 / 64),
        ]
    )
    panel = pd.DataFrame(
        {
            "bucket_ts": idx,
            "et_date": "2026-04-01",
            "price": price[:40],
        }
    )
    event = {
        "event_id": "test",
        "et_date": "2026-04-01",
        "event_start_time": idx[5].isoformat(),
        "event_direction": 1,
        "symbol": "TY",
    }
    return panel, event


def test_immediate_entry_at_event_start() -> None:
    panel, event = _panel_with_retrace()
    ts = entry_immediate(panel, event, 1)
    assert ts == pd.Timestamp(event["event_start_time"])


def test_retrace_entry_after_one_tick() -> None:
    panel, event = _panel_with_retrace()
    ts = entry_first_1tick_retrace(panel, event, 1)
    assert ts is not None
    assert ts > pd.Timestamp(event["event_start_time"])


def test_nonpositive_velocity_finds_pause() -> None:
    panel, event = _panel_with_retrace()
    ts = entry_nonpositive_velocity(panel, event, 1)
    assert ts is not None


def test_simulate_fade_returns_net_ticks() -> None:
    panel, event = _panel_with_retrace()
    out = simulate_fade_from_entry(
        panel,
        entry_ts=pd.Timestamp(event["event_start_time"]),
        event_direction=1,
        R_ticks=2,
        C_ticks=8,
        H_minutes=15,
    )
    assert out["entered"] is True
    assert "net_ticks" in out
