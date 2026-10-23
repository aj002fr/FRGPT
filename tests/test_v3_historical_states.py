"""Live V3 state labels on a synthetic trade tape. No shard read and no live-store write."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scripts.aggression_dashboard_v3.banner import STATE_STYLE, STATUS_READY, classify_state
from scripts.aggression_dashboard_v3.core import LIVE_STORE_DIR, build_print_frame
from scripts.aggression_v3_historical_states import (
    NS,
    _reject_live_store,
    _timestamp_ns,
    label_print_frame,
    session_start_for_close_date,
)


def _tape(start: datetime, seconds: int, *, price_step: float = 0.0) -> dict[str, pd.DataFrame]:
    stamps = [start + timedelta(seconds=i) for i in range(seconds)]
    # Two prints in the first second, so the labeled row must keep the later one.
    stamps = [start, start + timedelta(milliseconds=400)] + stamps[1:]
    prices = [110.0 + price_step * i for i in range(len(stamps))]
    frame = pd.DataFrame({
        "ts": stamps,
        "price": prices,
        "size": [1.0] * len(stamps),
        "side": ["B"] * len(stamps),
    })
    return {"TY": frame}


def test_daytime_labels_match_live_classifier():
    start = datetime(2024, 7, 2, 14, 0, tzinfo=timezone.utc)  # 10:00 ET, 5-minute window
    trades = _tape(start, 400, price_step=1.0 / 64.0)
    frame, _meta = build_print_frame(
        trades,
        price_symbol="TY",
        dv01_table={"TY": 85.0},
        dv01_adjust=True,
        session_start=session_start_for_close_date("2024-07-02"),
    )
    labeled = label_print_frame(frame, beta=1e-6, tau=1.0 / 64.0)
    assert len(labeled) < len(frame)
    ready = labeled.loc[labeled["status"] == STATUS_READY]
    assert len(ready) > 0
    open_ns = int(_timestamp_ns(pd.DatetimeIndex([start]))[0])
    before = labeled.loc[labeled["t"] < open_ns + 5 * 60 * NS]
    assert len(before) > 0
    assert (before["status"] != STATUS_READY).all()
    after = labeled.loc[labeled["t"] >= open_ns + 5 * 60 * NS + NS]
    assert (after["status"] == STATUS_READY).any()
    warming = labeled.loc[labeled["status_name"] == "WARMING"]
    assert len(warming) > 0
    assert (warming["state2"].astype(str) == "").all()
    for row in ready.itertuples(index=False):
        expected = classify_state(float(row.d), float(row.a), float(row.r))
        assert row.state2 == expected
        assert row.state == STATE_STYLE[expected][3]
        assert row.state2_headline == STATE_STYLE[expected][0]


def test_same_second_keeps_the_later_print():
    start = datetime(2024, 7, 2, 14, 0, tzinfo=timezone.utc)
    trades = _tape(start, 2)
    frame, _meta = build_print_frame(
        trades,
        price_symbol="TY",
        dv01_table={"TY": 85.0},
        dv01_adjust=True,
        session_start=session_start_for_close_date("2024-07-02"),
    )
    labeled = label_print_frame(frame, beta=1e-6, tau=1.0 / 64.0)
    first_ns = int(pd.Timestamp(frame.index[1]).value)
    assert int(labeled["t"].iloc[0]) == first_ns


def test_refuses_live_store(tmp_path):
    with pytest.raises(RuntimeError):
        _reject_live_store(LIVE_STORE_DIR)
    with pytest.raises(RuntimeError):
        _reject_live_store(LIVE_STORE_DIR / "history")
    assert _reject_live_store(tmp_path) == tmp_path.resolve()
