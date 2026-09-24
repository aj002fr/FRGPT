"""Compact V3 live snapshot: decimation keeps real extremes, rows round-trip, size stays under 1 MB."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3 import panels as pn


def _frame(n: int, minutes: float, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    end = pd.Timestamp("2026-09-24 14:30", tz="UTC")
    offsets = np.sort(rng.integers(0, int(minutes * 60e9), n))
    idx = pd.DatetimeIndex(end - pd.to_timedelta(int(minutes * 60e9) - offsets, unit="ns"), name="print_ts")
    size = rng.choice([1, 1, 2, 5, 50], n).astype(float)
    side = rng.choice([1.0, -1.0], n)
    buy = np.where(side > 0, size * 1300.0, 0.0)
    sell = np.where(side < 0, size * 1300.0, 0.0)
    price = 110.0 + np.cumsum(rng.choice([-1, 0, 0, 1], n)) / 64.0
    return pd.DataFrame({
        "total_aggress": np.cumsum(buy - sell), "price": price, "buy_aggress": buy, "sell_aggress": sell,
        "buysell_raw": side * 100.0, "implied_price": price + rng.normal(0, 0.01, n), "large_residual": rng.random(n) < 0.02,
    }, index=idx)


def test_keep_positions_keeps_first_last_min_max_of_every_bucket() -> None:
    rng = np.random.default_rng(1)
    values = [rng.normal(size=5000), rng.normal(size=5000)]
    values[1][17] = np.nan
    kept = pn.keep_positions(values, 5000, 50)
    assert np.all(np.diff(kept) > 0)
    edges = np.linspace(0, 5000, 51).astype(int)
    for lo, hi in zip(edges[:-1], edges[1:]):
        assert lo in kept and hi - 1 in kept
        for v in values:
            seg = v[lo:hi]
            assert lo + int(np.nanargmin(seg)) in kept and lo + int(np.nanargmax(seg)) in kept
    assert np.array_equal(pn.keep_positions(values, 150, 50), np.arange(150))


def test_compact_rows_round_trip_and_keep_the_full_tail() -> None:
    frame = _frame(20_000, 120)
    tape = pn._volume_frame(frame)
    tape["implied_price"] = frame["implied_price"]
    positions = pn.compact_positions(tape, pn.TAPE_FIELDS, buckets=200)
    block = pn.compact_block(tape, pn.TAPE_FIELDS, positions)
    rows = pn.block_rows(block)
    assert len(rows) == len(positions) < len(frame)
    tail_start = frame.index[-1] - pn.COMPACT_TAIL
    assert set(np.flatnonzero(frame.index >= tail_start)) <= set(positions.tolist())
    for row, pos in zip(rows[::97], positions[::97]):
        src = tape.iloc[pos]
        assert row["x"] == pn._to_et_iso(tape.index[pos]).ljust(26, "0")[:26]
        assert row["total_aggress"] == round(float(src["total_aggress"]), 1)
        assert row["price"] == round(float(src["price"]), 8)


def test_compact_payload_matches_row_payload_where_kept_and_is_small() -> None:
    frame = _frame(80_000, 120)
    path = frame[["price", "implied_price", "large_residual"]]
    meta = {"flow": {"trade_count": len(frame)}}
    compact = pn.build_compact_dashboard_payload(frame, path, path.iloc[:5000], meta)
    size = len(json.dumps(compact, separators=(",", ":")))
    full = len(json.dumps(pn.build_dashboard_payload(frame, path, path.iloc[:5000], meta), separators=(",", ":")))
    assert size < 1_000_000 < full
    rows = pn.expand_payload(compact)["panels"]
    old = pn.build_dashboard_payload(frame, path, path.iloc[:5000], meta)["panels"]
    by_x = {row["x"].ljust(26, "0")[:26]: row for row in old["p1"]["data"]}
    for row in rows["p1"]["data"][::211]:
        assert by_x[row["x"]]["total_aggress"] == row["total_aggress"] or abs(by_x[row["x"]]["total_aggress"] - row["total_aggress"]) <= 0.05
    assert rows["p1"]["data"][-1]["x"] == old["p1"]["data"][-1]["x"].ljust(26, "0")[:26]
    assert [r["x"] for r in rows["p3"]["data"]] == [r["x"] for r in rows["p1"]["data"]]
    assert rows["p4"]["data"][0]["implied_price"] is not None
    assert len(rows["p2"]["data"]) == len(old["p2"]["data"])
    assert rows["p5"]["data"] and rows["p5"]["large_residuals"]
    assert pn.last_panel_row(compact["panels"]["p2"])["x"] == rows["p2"]["data"][-1]["x"]


def test_compact_tip_rows_follow_the_expanded_history() -> None:
    frame = _frame(3000, 30)
    compact = pn.build_compact_dashboard_payload(frame, frame, pd.DataFrame(), {})
    tip = {"x": "2026-09-24T10:30:00.000001", "total_aggress": 1.0, "price": 110.0}
    compact["panels"]["p1"]["data"].append(tip)
    data = pn.expand_payload(compact)["panels"]["p1"]["data"]
    assert data[-1] == tip and len(data) == len(frame) + 1


def test_slim_banner_series_spacing_fields_and_last_row() -> None:
    base = pd.Timestamp("2026-09-24T10:00:00")
    series = [{"x": (base + pd.Timedelta(seconds=s)).isoformat(), "status": "READY", "state": "QUIET", "label": "L",
               "parameters": {"w": 15}, "contribution_line": f"row {s}"} for s in range(0, 61)]
    slim = pn.slim_banner_series(series)
    stamps = [pd.Timestamp(row["x"]) for row in slim]
    assert all(b - a >= pn.BANNER_SERIES_MIN_GAP for a, b in zip(stamps[:-2], stamps[1:-1]))
    assert slim[-1]["contribution_line"] == "row 60"
    assert all(set(row) <= set(pn.BANNER_HOVER_FIELDS) for row in slim)
