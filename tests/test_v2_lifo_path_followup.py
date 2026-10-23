"""Tests for offline LIFO path-follow-up helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.aggression_v1_flow_price_dispersion_research import TICK_SIZE
from scripts.aggression_v2_lifo_path_followup_research import (
    OUTPUT_DIR,
    add_run_end_flags,
    add_trail_path_labels,
    event_distance_flags,
    load_event_times,
)
from scripts.aggression_v2_lifo_zone_research import add_lifo_labels


def test_isolation_output_dir() -> None:
    text = str(OUTPUT_DIR).replace("\\", "/")
    assert text.endswith("workspace/analysis/aggression_v2_lifo_path_followup")
    assert "aggression_v2_live" not in text
    assert "aggression_v27_live" not in text
    assert "aggression_v2_lifo_zone/" not in text + "/"
    assert "aggression_v2_lifo_literature/" not in text + "/"


def test_script_does_not_import_live_apps() -> None:
    text = Path("scripts/aggression_v2_lifo_path_followup_research.py").read_text(encoding="utf-8")
    assert "aggression_dashboard_v22" not in text
    assert "aggression_dashboard_v27" not in text
    assert "from scripts.aggression_dashboard" not in text


def test_trail_zone_on_completed_two_way() -> None:
    n = 40
    ts = pd.date_range("2026-01-05 10:00", periods=n, freq="1min", tz="UTC")
    price = np.full(n, 110.0)
    # Last 15m ending at bar 30: up 4 ticks then back to start.
    price[20:26] = 110.0 + np.linspace(0, 4 * TICK_SIZE, 6)
    price[26:31] = 110.0 + np.linspace(4 * TICK_SIZE, 0, 5)
    df = pd.DataFrame({"price": price, "et_date": "2026-01-05"}, index=ts)
    out = add_trail_path_labels(df, horizon=15)
    assert bool(out["trail_zone_15m"].iloc[30])
    assert bool(out["trail_meat_win"].iloc[30])
    assert bool(out["trail_twoway_15m"].iloc[30])
    assert not bool(out["trail_zone_15m"].iloc[10])


def test_run_just_ended_on_flip_after_ten() -> None:
    n = 16
    ts = pd.date_range("2026-01-05 10:00", periods=n, freq="1min", tz="UTC")
    agg = np.r_[np.full(10, 1.0), [-1.0], np.full(5, 1.0)]
    df = pd.DataFrame(
        {
            "agg_1m": agg,
            "price": np.full(n, 110.0),
            "et_date": "2026-01-05",
            "last_push_3t": False,
            "trail_meat_15m": False,
            "intensity_lo": False,
        },
        index=ts,
    )
    out = add_run_end_flags(df)
    assert bool(out["run_just_ended"].iloc[10])
    assert not bool(out["run_just_ended"].iloc[9])
    assert bool(out["after_run_end_5m"].iloc[12])
    assert not bool(out["after_run_end_5m"].iloc[9])


def test_event_shoulder_excludes_plus_minus_30() -> None:
    index = pd.date_range("2026-01-05 14:00", periods=120, freq="1min", tz="UTC")
    event = np.array(["2026-01-05T15:00:00"], dtype="datetime64[ns]")
    near, post, shoulder = event_distance_flags(index, event)
    at_event = index.get_loc(pd.Timestamp("2026-01-05 15:00", tz="UTC"))
    assert bool(near.iloc[at_event])
    assert not bool(post.iloc[at_event])
    assert not bool(shoulder.iloc[at_event])
    plus_45 = index.get_loc(pd.Timestamp("2026-01-05 15:45", tz="UTC"))
    assert not bool(near.iloc[plus_45])
    assert bool(post.iloc[plus_45])
    assert bool(shoulder.iloc[plus_45])
    minus_45 = index.get_loc(pd.Timestamp("2026-01-05 14:15", tz="UTC"))
    assert not bool(near.iloc[minus_45])
    assert not bool(post.iloc[minus_45])
    assert bool(shoulder.iloc[minus_45])


def test_load_event_times_reads_us_rows(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "events.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE economic_events (event_date TEXT, country TEXT)"
    )
    conn.execute(
        "INSERT INTO economic_events VALUES ('2026-04-10T08:30:00', 'United States')"
    )
    conn.commit()
    conn.close()
    times = load_event_times(db)
    assert len(times) == 1


def test_forward_labels_still_censor_incomplete() -> None:
    n = 20
    ts = pd.date_range("2026-01-05 10:00", periods=n, freq="1min", tz="UTC")
    df = pd.DataFrame({"price": np.linspace(110.0, 110.1, n), "et_date": "2026-01-05"}, index=ts)
    out = add_lifo_labels(df, horizons=(15,))
    assert pd.isna(out["lifo_zone_15m"].iloc[-1])
    assert out["lifo_zone_15m"].iloc[0] == out["lifo_zone_15m"].iloc[0]
