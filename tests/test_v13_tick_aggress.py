"""Isolated V1.3 per-print TotalAggress. Does not import live V1 app."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from scripts.aggression_dashboard_v13.core import aggregate_tick_aggression
from scripts.aggression_dashboard_v13.flow_history import DB_DIR
from scripts.aggression_dashboard_v13.panels import build_dashboard_payload, build_total_aggress_panel


def _prints(start: datetime, rows: list[tuple[str, float, str, int]]) -> dict[str, pd.DataFrame]:
    by_symbol: dict[str, list[dict]] = {}
    for i, (symbol, price, side, size) in enumerate(rows):
        by_symbol.setdefault(symbol, []).append({
            "ts": start + timedelta(seconds=i),
            "price": price,
            "size": size,
            "side": side,
            "dashboard_symbol": symbol,
        })
    return {symbol: pd.DataFrame(items) for symbol, items in by_symbol.items()}


def test_tick_cumsum_jumps_on_each_print() -> None:
    start = datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)
    trades = _prints(start, [
        ("TY", 110.0, "B", 10),
        ("TY", 110.0, "A", 4),
        ("FV", 108.0, "B", 2),
    ])
    tick_df, meta = aggregate_tick_aggression(trades, dv01_table={"TY": 85.0, "FV": 42.0}, dv01_adjust=True)
    assert meta["bucket_interval"] == "print"
    assert meta["trade_count"] == 3
    values = [round(float(v), 6) for v in tick_df["total_aggress"].tolist()]
    assert values == [0.0, -340.0, -256.0]


def test_panel1_uses_tick_flow_not_minute_bars() -> None:
    idx = pd.date_range("2026-09-15 13:00", periods=3, freq="s", tz="UTC")
    tick = pd.DataFrame({"total_aggress": [0.0, 85.0, 40.0], "trade_price": [110.0, 110.01, 110.0]}, index=idx)
    minute = pd.DataFrame(
        {
            "total_aggress": [0.0, 9999.0],
            "signed_flow_bucket": [0.0, 1.0],
            "buysell_ratio": [1.0, 1.1],
            "buy_flow_bucket": [10.0, 12.0],
            "sell_flow_bucket": [8.0, 9.0],
        },
        index=pd.date_range("2026-09-15 13:00", periods=2, freq="min", tz="UTC"),
    )
    price = pd.DataFrame({"price": [110.0, 110.02]}, index=pd.date_range("2026-09-15 13:00", periods=2, freq="min", tz="UTC"))
    payload = build_dashboard_payload(minute, price, {"tick_flow": tick, "fair_value_models": {}})
    ys = [row["total_aggress"] for row in payload["panels"]["p1"]["data"]]
    assert 9999.0 not in ys
    assert ys == [0.0, 85.0, 40.0]
    assert payload["panels"]["p1"]["traces"][0]["shape"] == "linear"


def test_v13_store_is_not_v1_live() -> None:
    assert "aggression_v13_live" in str(DB_DIR)
    assert "aggression_v1_live" not in str(DB_DIR).replace("aggression_v13_live", "")


def test_empty_ticks_panel() -> None:
    panel = build_total_aggress_panel(pd.DataFrame(), pd.DataFrame())
    assert panel["data"] == []
