"""Tests for offline LIFO-zone labels and isolation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.aggression_v2_lifo_zone_research import (
    CONTAIN_TICKS,
    MEAT_TICKS,
    OUTPUT_DIR,
    TICK_SIZE,
    add_lifo_labels,
    fade_path_hit,
    first_reversal,
    label_forward_window,
    three_before_two,
)


def test_isolation_output_dir() -> None:
    text = str(OUTPUT_DIR).replace("\\", "/")
    assert text.endswith("workspace/analysis/aggression_v2_lifo_zone")
    assert "aggression_v2_live" not in text
    assert "aggression_v22_live" not in text
    assert "aggression_v27_live" not in text


def test_one_way_spike_is_not_two_way() -> None:
    path = np.array([0.0, 1.0, 2.0, 3.5, 4.0, 3.8])
    assert first_reversal(path, meat=3.0) is False
    lab = label_forward_window(110.0 + path * TICK_SIZE, meat=3.0, contain=8.0)
    assert lab["meat"] == 1.0
    assert lab["two_way"] == 0.0
    assert lab["zone"] == 0.0


def test_up_then_retrace_is_two_way_zone() -> None:
    path = np.array([0.0, 2.0, 4.0, 1.0, 0.5])
    assert first_reversal(path, meat=3.0) is True
    prices = 110.0 + path * TICK_SIZE
    lab = label_forward_window(prices, meat=3.0, contain=8.0)
    assert lab["meat"] == 1.0
    assert lab["contained"] == 1.0
    assert lab["two_way"] == 1.0
    assert lab["zone"] == 1.0


def test_wide_one_way_is_not_contained() -> None:
    path = np.array([0.0, 4.0, 8.0, 12.0, 9.0])
    lab = label_forward_window(110.0 + path * TICK_SIZE, meat=3.0, contain=8.0)
    assert lab["two_way"] == 1.0
    assert lab["contained"] == 0.0
    assert lab["zone"] == 0.0


def test_dead_tape_has_no_meat() -> None:
    path = np.array([0.0, 0.5, -0.5, 0.2, 0.0])
    lab = label_forward_window(110.0 + path * TICK_SIZE, meat=3.0, contain=8.0)
    assert lab["meat"] == 0.0
    assert lab["zone"] == 0.0
    assert three_before_two(path, meat=3.0, adverse=2.0) is False


def test_three_before_two_up() -> None:
    path = np.array([0.0, 1.0, 3.2, 2.0])
    assert three_before_two(path, meat=3.0, adverse=2.0) is True
    blocked = np.array([0.0, -2.0, 3.5])
    assert three_before_two(blocked, meat=3.0, adverse=2.0) is False


def test_fade_path_hit() -> None:
    up = 110.0 + np.array([0.0, 1.0, 3.2]) * TICK_SIZE
    assert fade_path_hit(up, 1.0) == 1.0
    fail = 110.0 + np.array([0.0, -2.1, 3.0]) * TICK_SIZE
    assert fade_path_hit(fail, 1.0) == 0.0


def test_forward_labels_censor_day_end() -> None:
    ts = pd.date_range("2026-01-05 14:00", periods=20, freq="1min", tz="UTC")
    price = 110.0 + np.array([0, 2, 4, 1, 0] * 4, dtype=float) * TICK_SIZE
    df = pd.DataFrame({"price": price, "et_date": "2026-01-05"}, index=ts)
    out = add_lifo_labels(df, horizons=(15,), meat=MEAT_TICKS, contain=CONTAIN_TICKS)
    assert out["lifo_zone_15m"].iloc[-15:].isna().all()
    assert out["lifo_zone_15m"].iloc[:-15].notna().any()


def test_script_does_not_import_live_apps() -> None:
    text = Path("scripts/aggression_v2_lifo_zone_research.py").read_text(encoding="utf-8")
    assert "aggression_dashboard_v22" not in text
    assert "aggression_dashboard_v27" not in text
    assert "from scripts.aggression_dashboard" not in text
