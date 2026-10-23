"""Durable V1 aggress buy/sell 1-minute history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from scripts.aggression_dashboard.core import trades_to_symbol_minute_bars
from scripts.aggression_dashboard.flow_history import (
    backfill_from_trade_cache,
    history_status,
    load_completed_symbol_bars,
    persist_completed_bars,
    query_flow_history,
)


def _ticks(start: datetime, prices_sides: list[tuple[float, str, int]]) -> pd.DataFrame:
    rows = []
    for i, (price, side, size) in enumerate(prices_sides):
        rows.append({
            "ts": start + timedelta(seconds=i * 5),
            "price": price,
            "size": size,
            "side": side,
            "instrument_id": "TY",
            "raw_symbol": "ZNU6",
            "dashboard_symbol": "TY",
        })
    return pd.DataFrame(rows)


def test_persist_splits_buy_ask_and_sell_bid(tmp_path: Path) -> None:
    start = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)
    ticks = _ticks(start, [(110.0, "B", 10), (110.0, "A", 4)])
    bars, _ = trades_to_symbol_minute_bars(ticks, "TY", dv01_unit=85.0)
    db = tmp_path / "flow_history.sqlite"
    written = persist_completed_bars(
        {"TY": bars},
        before=start + timedelta(minutes=1),
        source="live",
        db_path=db,
    )
    assert written == 1
    rows = query_flow_history("TY", db_path=db)
    assert len(rows) == 1
    assert rows[0]["buy_contracts"] == 10.0
    assert rows[0]["sell_contracts"] == 4.0
    assert rows[0]["buy_flow"] == 850.0
    assert rows[0]["sell_flow"] == 340.0


def test_forming_minute_is_excluded(tmp_path: Path) -> None:
    start = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)
    first = _ticks(start, [(110.0, "B", 10)])
    second = _ticks(start + timedelta(minutes=1), [(110.0, "A", 3)])
    bar1, _ = trades_to_symbol_minute_bars(first, "TY", dv01_unit=1.0)
    bar2, _ = trades_to_symbol_minute_bars(second, "TY", dv01_unit=1.0)
    bars = {"TY": pd.concat([bar1, bar2]).sort_index()}
    db = tmp_path / "flow_history.sqlite"
    persist_completed_bars(
        bars,
        before=start + timedelta(minutes=1),
        source="live",
        db_path=db,
    )
    rows = query_flow_history("TY", db_path=db)
    assert len(rows) == 1
    assert rows[0]["buy_contracts"] == 10.0
    assert rows[0]["sell_contracts"] == 0.0


def test_upsert_is_idempotent(tmp_path: Path) -> None:
    start = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)
    ticks = _ticks(start, [(110.0, "B", 8)])
    bars, _ = trades_to_symbol_minute_bars(ticks, "TY", dv01_unit=1.0)
    db = tmp_path / "flow_history.sqlite"
    persist_completed_bars({"TY": bars}, before=start + timedelta(minutes=1), db_path=db)
    persist_completed_bars({"TY": bars}, before=start + timedelta(minutes=1), db_path=db)
    rows = query_flow_history("TY", db_path=db)
    assert len(rows) == 1
    assert rows[0]["buy_contracts"] == 8.0


def test_load_completed_symbol_bars_roundtrip(tmp_path: Path) -> None:
    start = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)
    ticks = _ticks(start, [(110.0, "B", 8)])
    bars, _ = trades_to_symbol_minute_bars(ticks, "TY", dv01_unit=1.0)
    db = tmp_path / "flow_history.sqlite"
    persist_completed_bars({"TY": bars}, before=start + timedelta(minutes=1), db_path=db)
    loaded = load_completed_symbol_bars(["TY"], db_path=db)
    assert "TY" in loaded
    assert int(loaded["TY"]["trade_count"].iloc[0]) == 1
    assert float(loaded["TY"]["buy_contracts"].iloc[0]) == 8.0


def test_cache_backfill_reads_parquet_only(tmp_path: Path) -> None:
    day = datetime(2026, 9, 11, tzinfo=timezone.utc)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    raw = pd.DataFrame({
        "ts": [day + timedelta(hours=14), day + timedelta(hours=14, seconds=5)],
        "price": [110.0, 110.0],
        "size": [6, 2],
        "side": ["B", "A"],
        "instrument_id": ["TY", "TY"],
        "raw_symbol": ["ZNU6", "ZNU6"],
    })
    raw.to_parquet(cache_dir / "trades_v2_ZN_FUT_20260911.parquet", index=False)
    db = tmp_path / "flow_history.sqlite"
    stats = backfill_from_trade_cache(
        cache_dir=cache_dir,
        db_path=db,
        dv01_table={"TY": 85.0, "FV": 42.0, "TU": 36.0, "US": 125.0},
    )
    again = backfill_from_trade_cache(
        cache_dir=cache_dir,
        db_path=db,
        dv01_table={"TY": 85.0, "FV": 42.0, "TU": 36.0, "US": 125.0},
    )
    assert stats["written"] == 1
    assert again["skipped"] == 1
    rows = query_flow_history("TY", db_path=db)
    assert rows[0]["buy_contracts"] == 6.0
    assert rows[0]["sell_contracts"] == 2.0
    status = history_status(db_path=db)
    assert status["row_count"] == 1
    assert "TY" in status["symbols"]
