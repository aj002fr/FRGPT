"""Panel 1 hover values stay session-absolute across lookback clips."""

from __future__ import annotations

import pandas as pd

from scripts.aggression_dashboard.hf_model import align_flow_and_price
from scripts.aggression_dashboard.panels import build_total_aggress_panel


def _session_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    idx = pd.date_range("2026-09-11 12:00", periods=180, freq="min", tz="UTC")
    flow = pd.DataFrame(
        {"total_aggress": [float(i * 1000) for i in range(len(idx))]},
        index=idx,
    )
    price = pd.DataFrame({"price": [110.0 + i * 0.01 for i in range(len(idx))]}, index=idx)
    return flow, price, idx[120]


def test_panel1_lookback_keeps_same_hover_value() -> None:
    flow, price, stamp = _session_frames()
    full = build_total_aggress_panel(flow, price)
    clipped = build_total_aggress_panel(flow.loc[stamp:], price.loc[stamp:])
    full_y = next(row["total_aggress"] for row in full["data"] if row["x"].startswith("2026-09-11T10:00:00"))
    clip_y = next(row["total_aggress"] for row in clipped["data"] if row["x"].startswith("2026-09-11T10:00:00"))
    assert full_y == clip_y == 120_000.0


def test_align_rezero_false_preserves_session_level() -> None:
    flow, price, stamp = _session_frames()
    aligned = align_flow_and_price(flow.loc[stamp:], price.loc[stamp:], rezero=False)
    assert float(aligned.loc[stamp, "total_aggress"]) == 120_000.0


def test_panel1_uses_last_trade_price_on_print_clock() -> None:
    idx = pd.to_datetime(["2026-09-15 07:00:00", "2026-09-15 07:00:01"], utc=True)
    flow = pd.DataFrame(
        {
            "total_aggress": [0.0, 85.0],
            "last_trade_price": [110.0, 110.015625],
        },
        index=idx,
    )
    price = pd.DataFrame({"price": [999.0]}, index=idx[:1])
    panel = build_total_aggress_panel(flow, price)
    assert [row["price"] for row in panel["data"]] == [110.0, 110.015625]
    assert [row["total_aggress"] for row in panel["data"]] == [0.0, 85.0]
    assert panel["traces"][0]["connectgaps"] is True
    assert panel["traces"][0]["shape"] == "linear"
    assert panel["traces"][1]["name"] == "Last Traded Price"
