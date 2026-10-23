"""Causal V3 backfill: no lookahead, separate betas, curve beta on the state rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from scripts.aggression_dashboard_v3.banner import STATUS_READY
from scripts.aggression_dashboard_v3.core import LIVE_STORE_DIR, build_print_frame
from scripts.aggression_v3_causal_backfill import (
    backfill_root,
    iter_sessions,
    label_prints,
    second_status,
)
from scripts.aggression_v3_historical_states import session_start_for_close_date, walk_forward_tag_beta


def _points():
    return [
        {"date": "2010-06-08", "tag": "REGULAR", "x_net": 1.0, "y_bps": 1.0, "detail": ""},
        {"date": "2010-06-09", "tag": "REGULAR", "x_net": 2.0, "y_bps": 2.0, "detail": ""},
        {"date": "2010-06-10", "tag": "REGULAR", "x_net": 4.0, "y_bps": 3.0, "detail": ""},
    ]


def test_future_day_cannot_change_an_earlier_beta():
    early = walk_forward_tag_beta(_points(), "2010-06-10", {})
    later = _points() + [
        {"date": "2010-06-11", "tag": "REGULAR", "x_net": 500.0, "y_bps": -200.0, "detail": ""},
    ]
    again = walk_forward_tag_beta(later, "2010-06-10", {})
    assert early["beta_ref"] == again["beta_ref"]
    assert early["fit_dates"] == ["2010-06-08", "2010-06-09"]
    assert all(day < "2010-06-10" for day in early["fit_dates"])


def test_other_tags_are_not_borrowed():
    points = _points() + [
        {"date": "2010-06-07", "tag": "10Y", "x_net": 1000.0, "y_bps": -1000.0, "detail": "10Y"},
    ]
    fit = walk_forward_tag_beta(points, "2010-06-10", {})
    assert "2010-06-07" not in fit["fit_dates"]
    assert fit["tag"] == "REGULAR"


def test_sparse_and_nonpositive_tags_stay_unavailable():
    one = walk_forward_tag_beta(_points()[:1], "2010-06-09", {})
    assert one["beta_ref"] is None
    negative = [
        {"date": "2010-06-08", "tag": "REGULAR", "x_net": 1.0, "y_bps": -1.0, "detail": ""},
        {"date": "2010-06-09", "tag": "REGULAR", "x_net": 2.0, "y_bps": -2.0, "detail": ""},
    ]
    fit = walk_forward_tag_beta(negative, "2010-06-10", {})
    assert fit["beta_ref"] is None


def test_beta_is_constant_on_the_session_and_states_follow_the_curve_beta():
    start = datetime(2010, 6, 8, 14, 0, tzinfo=timezone.utc)
    stamps = [start + timedelta(seconds=i) for i in range(400)]
    trades = {
        "TY": pd.DataFrame({
            "ts": stamps,
            "price": [110.0 + i / 64.0 for i in range(len(stamps))],
            "size": [2.0] * len(stamps),
            "side": ["B"] * len(stamps),
        })
    }
    frame, _meta = build_print_frame(
        trades,
        price_symbol="TY",
        dv01_table={"TY": 85.0},
        dv01_adjust=True,
        session_start=session_start_for_close_date("2010-06-08"),
    )
    low = label_prints(frame, 1e-6, 1.0 / 64.0)
    high = label_prints(frame, 2e-6, 1.0 / 64.0)
    ready = low["status"] == STATUS_READY
    assert int(ready.sum()) > 0
    ratio = high.loc[ready, "a"].to_numpy() / low.loc[ready, "a"].to_numpy()
    assert np.allclose(ratio, 2.0)
    assert low["t"].is_monotonic_increasing
    assert len(low) == len(frame)


def test_second_grain_keeps_the_later_print():
    rows = pd.DataFrame({
        "t": [1_000_000_000, 1_400_000_000, 2_000_000_000],
        "status": [1, 2, 3],
    })
    kept = second_status(rows)
    assert kept["status"].tolist() == [2, 3]


def test_weekend_close_is_not_a_session():
    sessions = iter_sessions(
        __import__("datetime").date(2010, 6, 11),
        __import__("datetime").date(2010, 6, 14),
    )
    close_dates = [item[0].isoformat() for item in sessions]
    assert "2010-06-12" not in close_dates  # Saturday
    assert "2010-06-13" not in close_dates  # Sunday
    assert "2010-06-14" in close_dates  # Monday, opened Sunday


def test_backfill_directory_is_not_the_live_root(tmp_path):
    with pytest.raises(RuntimeError):
        backfill_root(LIVE_STORE_DIR)
    created = backfill_root(tmp_path / "historical_backfill")
    assert created.name == "historical_backfill"
