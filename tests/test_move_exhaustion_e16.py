"""Tests for E16 session / scheduled-event regime tags."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.move_exhaustion.features.session_regimes import (
    is_calendar_roll_period,
    session_regime_features,
)


def _panel(idx: pd.DatetimeIndex, **cols) -> pd.DataFrame:
    base = {
        "bucket_ts": idx,
        "et_date": "2026-04-04",
        "price": 110.0 + np.arange(len(idx)) * (1 / 64),
        "move_z_5m": 3.0,
        "flow_z_5m": 1.5,
        "residual_z": 2.0,
        "move_ticks_5m": 4.0,
    }
    base.update(cols)
    return pd.DataFrame(base)


def test_opening_and_late_flags() -> None:
    # 01:30 ET = 05:30 UTC (EDT)
    idx = pd.date_range("2026-04-04 05:25", periods=6, freq="1min", tz="UTC")
    panel = _panel(idx)
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[-1].isoformat(),
        "event_direction": 1,
    }
    empty = pd.DataFrame(columns=["event_ts_utc", "event_name", "et_date"])
    feats = session_regime_features(
        panel, event, macro_events=empty, auction_events=empty, high_vol_threshold=2.5
    )
    assert feats["is_opening_period"] is True
    assert feats["primary_regime"] in ("opening_period", "high_volatility")

    # 15:30 ET = 19:30 UTC
    idx2 = pd.date_range("2026-04-04 19:25", periods=6, freq="1min", tz="UTC")
    panel2 = _panel(idx2)
    event2 = {
        "et_date": "2026-04-04",
        "event_start_time": idx2[-1].isoformat(),
        "event_direction": 1,
    }
    feats2 = session_regime_features(
        panel2, event2, macro_events=empty, auction_events=empty, high_vol_threshold=99.0
    )
    assert feats2["is_late_session"] is True
    assert feats2["primary_regime"] == "late_session"


def test_macro_window() -> None:
    idx = pd.date_range("2026-04-04 12:25", periods=6, freq="1min", tz="UTC")
    panel = _panel(idx, move_z_5m=0.5)
    event = {
        "et_date": "2026-04-04",
        "event_start_time": idx[-1].isoformat(),
        "event_direction": 1,
    }
    macro = pd.DataFrame(
        {
            "event_ts_utc": [pd.Timestamp("2026-04-04 12:30:00", tz="UTC")],
            "event_name": ["Non Farm Payrolls"],
            "et_date": ["2026-04-04"],
        }
    )
    empty = pd.DataFrame(columns=["event_ts_utc", "event_name", "et_date"])
    feats = session_regime_features(
        panel, event, macro_events=macro, auction_events=empty, high_vol_threshold=99.0
    )
    assert feats["is_scheduled_macro"] is True
    assert feats["primary_regime"] == "scheduled_macro"


def test_roll_calendar_proxy() -> None:
    assert is_calendar_roll_period("2026-03-05") is True
    assert is_calendar_roll_period("2026-03-20") is False
    assert is_calendar_roll_period("2026-04-04") is False
