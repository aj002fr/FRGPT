"""V1 session 1-minute store: incremental bars, 10-day tick drop, live snapshot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from scripts.aggression_dashboard.core import (
    AggressionRequest,
    aggregate_curve_aggression,
    combine_symbol_minute_bars,
    printed_minute_bars,
    trades_to_symbol_minute_bars,
    upsert_minute_bars,
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


def test_incremental_bars_match_one_shot_aggregate() -> None:
    start = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    first = _ticks(start, [(110.0, "B", 10), (110.0, "A", 4)])
    second = _ticks(start + timedelta(minutes=1), [(110.02, "B", 8)])
    both = pd.concat([first, second], ignore_index=True)
    dv01 = {"TY": 85.0}
    one_shot, meta = aggregate_curve_aggression(
        {"TY": both},
        dv01_table=dv01,
        dv01_adjust=True,
        reset="none",
    )
    bar1, _ = trades_to_symbol_minute_bars(first, "TY", dv01_unit=85.0)
    bar2, _ = trades_to_symbol_minute_bars(second, "TY", dv01_unit=85.0)
    incremental = upsert_minute_bars(bar1, bar2)
    combined, _ = combine_symbol_minute_bars(
        {"TY": incremental},
        dv01_adjust=True,
        reset="none",
    )
    pd.testing.assert_series_equal(
        one_shot["total_aggress"],
        combined["total_aggress"],
        check_names=False,
    )
    assert meta["trade_count"] == 3
    assert int(incremental["trade_count"].sum()) == 3


def test_upsert_replaces_overlapping_minute() -> None:
    start = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    first = _ticks(start, [(110.0, "B", 10)])
    more_same_minute = _ticks(start + timedelta(seconds=20), [(110.0, "B", 10), (110.0, "A", 5)])
    bar1, _ = trades_to_symbol_minute_bars(first, "TY", dv01_unit=1.0)
    bar2, _ = trades_to_symbol_minute_bars(
        pd.concat([first, more_same_minute], ignore_index=True),
        "TY",
        dv01_unit=1.0,
    )
    merged = upsert_minute_bars(bar1, bar2)
    assert len(merged) == 1
    assert float(merged["signed_contracts"].iloc[0]) == 15.0


def test_upsert_keeps_closed_bars_not_in_incoming() -> None:
    early = datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc)
    late = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    bar_early, _ = trades_to_symbol_minute_bars(
        _ticks(early, [(110.0, "B", 10)]),
        "TY",
        dv01_unit=1.0,
    )
    bar_mid, _ = trades_to_symbol_minute_bars(
        _ticks(early + timedelta(hours=2), [(110.0, "A", 6)]),
        "TY",
        dv01_unit=1.0,
    )
    bar_late, _ = trades_to_symbol_minute_bars(
        _ticks(late, [(110.0, "B", 3)]),
        "TY",
        dv01_unit=1.0,
    )
    existing = upsert_minute_bars(bar_early, bar_mid)
    merged = upsert_minute_bars(existing, bar_late)
    assert len(merged) == 3
    assert float(merged.loc[pd.Timestamp(early + timedelta(hours=2)), "signed_contracts"]) == -6.0


def test_gappy_resample_does_not_zero_closed_bars() -> None:
    early = datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc)
    late = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    mid = _ticks(early + timedelta(hours=2), [(110.0, "A", 8)])
    existing, _ = trades_to_symbol_minute_bars(mid, "TY", dv01_unit=1.0)
    gappy = pd.concat([
        _ticks(early, [(110.0, "B", 1)]),
        _ticks(late, [(110.0, "B", 1)]),
    ], ignore_index=True)
    incoming, _ = trades_to_symbol_minute_bars(gappy, "TY", dv01_unit=1.0)
    merged = upsert_minute_bars(existing, printed_minute_bars(incoming))
    mid_ts = pd.Timestamp(early + timedelta(hours=2)).floor("min")
    assert float(merged.loc[mid_ts, "signed_contracts"]) == -8.0
    assert int(merged.loc[mid_ts, "trade_count"]) == 1


def test_tenday_refresh_keeps_bars_and_drops_ticks(monkeypatch) -> None:
    from scripts.aggression_dashboard import app as app_module

    start = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    trades = {"TY": _ticks(start, [(110.0, "B", 2)])}

    monkeypatch.setattr(app_module, "fetch_curve_trades", lambda *args, **kwargs: (trades, {}))
    monkeypatch.setattr(app_module, "load_dv01_table", lambda: {"TY": 85.0, "FV": 42.0, "TU": 36.0, "US": 125.0})
    monkeypatch.setattr(app_module, "persist_completed_bars", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        app_module,
        "_tenday_window",
        lambda now=None: (start, start + timedelta(hours=1), ["2026-09-10"]),
    )

    calls = {"n": 0}

    def fake_sleep(_sec):
        calls["n"] += 1
        if calls["n"] >= 1:
            raise RuntimeError("stop")

    monkeypatch.setattr(app_module.time_module, "sleep", fake_sleep)
    with app_module._TENDAY_LOCK:
        app_module._TENDAY.update({"fit_date": None, "trades": {}, "bars": {}, "models": {}})

    try:
        app_module._tenday_refresher()
    except RuntimeError:
        pass

    with app_module._TENDAY_LOCK:
        assert app_module._TENDAY["trades"] == {}
        assert "TY" in app_module._TENDAY["bars"]
        assert not app_module._TENDAY["bars"]["TY"].empty
        assert app_module._TENDAY["fit_date"]


def test_live_dataset_uses_session_bars_when_ready(monkeypatch) -> None:
    from scripts.aggression_dashboard import app as app_module

    start = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    ticks = _ticks(start, [(110.0, "B", 6)])
    bars, stats = trades_to_symbol_minute_bars(ticks, "TY", dv01_unit=85.0)
    with app_module._SESSION_1M_LOCK:
        app_module._SESSION_1M.update({
            "session_start": start,
            "rebuild_from": start,
            "bars": {"TY": bars, "FV": pd.DataFrame(), "TU": pd.DataFrame(), "US": pd.DataFrame()},
            "per_symbol": {"TY": stats},
            "front_contracts": {},
            "ready": True,
            "error": None,
        })

    monkeypatch.setattr(app_module, "_ensure_live_buffer", lambda: None)
    monkeypatch.setattr(app_module, "default_start_time", lambda now=None: start)
    monkeypatch.setattr(
        app_module.LIVE_BUFFER,
        "get_mid_series",
        lambda symbols, a, b: {sym: pd.DataFrame(columns=["price"]) for sym in symbols},
    )
    monkeypatch.setattr(
        app_module,
        "_live_status",
        lambda mode, historical_lag_sec=None: {"source": "accessapi", "connected": True},
    )
    monkeypatch.setattr(
        app_module,
        "_build_fair_value_models",
        lambda req, flow_df, price_df, include_tenday=True: {"intraday": {"data": pd.DataFrame(), "meta": {"ok": False}}},
    )
    called = {"ticks": 0}

    def forbid_tick_merge(*args, **kwargs):
        called["ticks"] += 1
        raise AssertionError("live path should not merge session ticks when 1m bars are ready")

    monkeypatch.setattr(app_module, "_merge_historical_and_live", forbid_tick_merge)

    req = AggressionRequest(
        aggression_symbols=("TY",),
        price_symbol="TY",
        start=start,
        end=start + timedelta(hours=1),
    )
    flow_df, _price_df, meta = app_module.build_live_aggression_dataset(req)
    assert called["ticks"] == 0
    assert not flow_df.empty
    assert meta["backfill"]["source"] == "session_1m_bars"
    assert meta["flow"]["trade_count"] == 1


def test_heal_session_bars_from_history(tmp_path, monkeypatch) -> None:
    from scripts.aggression_dashboard import app as app_module
    from scripts.aggression_dashboard.flow_history import persist_completed_bars, load_completed_symbol_bars

    start = datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc)
    ticks = _ticks(start, [(110.0, "B", 10)])
    bars, _ = trades_to_symbol_minute_bars(ticks, "TY", dv01_unit=1.0)
    db = tmp_path / "heal.sqlite"
    persist_completed_bars({"TY": bars}, before=start + timedelta(minutes=1), db_path=db)
    monkeypatch.setattr(
        app_module,
        "load_completed_symbol_bars",
        lambda symbols, start=None, end=None: load_completed_symbol_bars(
            symbols, start=start, end=end, db_path=db
        ),
    )
    empty = {symbol: pd.DataFrame() for symbol in ("TY", "FV", "TU", "US")}
    healed = app_module._heal_session_bars_from_history(
        empty,
        session_start=start,
        forming_start=start + timedelta(minutes=1),
        symbols=("TY",),
    )
    assert not healed["TY"].empty
    assert float(healed["TY"]["buy_contracts"].iloc[0]) == 10.0


def test_p1_axis_deadband_holds_small_moves() -> None:
    from scripts.aggression_dashboard import app as app_module

    key = ("deadband-test",)
    with app_module._P1_AXIS_LOCK:
        app_module._P1_AXIS_LAST.pop(key, None)
    first = {"panels": {"p1": {"axis_align": {"aggress_range": [0.0, 100.0]}}}}
    app_module._stabilize_p1_axis(first, key)
    second = {"panels": {"p1": {"axis_align": {"aggress_range": [0.5, 100.5]}}}}
    held = app_module._stabilize_p1_axis(second, key)
    assert held["panels"]["p1"]["axis_align"]["aggress_range"] == [0.0, 100.0]
    jump = {"panels": {"p1": {"axis_align": {"aggress_range": [20.0, 140.0]}}}}
    moved = app_module._stabilize_p1_axis(jump, key)
    assert moved["panels"]["p1"]["axis_align"]["aggress_range"] == [20.0, 140.0]
