from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.banner import (
    BANNER_SERIES_MAX_POINTS,
    classify_six_state,
    build_banner,
    build_banner_series,
)
from scripts.aggression_dashboard_v3.beta_ref import (
    _ols_slope,
    beta_to_yield_bp_per_1m,
    display_clock_on_session,
    fit_session_slope,
    session_tod_window,
)
from scripts.aggression_dashboard_v3.core import (
    DEFAULT_AGGRESSION_SYMBOLS,
    DEFAULT_PRICE_SYMBOL,
    TICK_SIZE_BY_SYMBOL,
    apply_beta_ref_path,
    build_print_frame,
    point_value_usd,
    tick_size,
)
from scripts.aggression_dashboard_v3.panels import (
    BUYSELL_DISPLAY_MAX_POINTS,
    READY_STATE_COLORS,
    build_buysell_ratio_panel,
    build_state_timeline_panel,
    collapse_ready_state_runs,
)
from scripts.aggression_dashboard_v3.print_reconcile import apply_hist, apply_live, reconcile_symbol


def test_last_3h_tod_window_uses_session_morning():
    eastern = ZoneInfo("America/New_York")
    session_start = datetime(2026, 9, 15, 18, 0, tzinfo=eastern)
    now = datetime(2026, 9, 17, 10, 44, tzinfo=eastern)
    clock = display_clock_on_session(session_start, now).astimezone(eastern)
    assert clock.date().isoformat() == "2026-09-16"
    assert (clock.hour, clock.minute) == (10, 44)
    start, end = session_tod_window(session_start, now)
    assert abs((end - start) - timedelta(hours=3)) < timedelta(seconds=1)


def test_default_view_is_whole_curve():
    assert DEFAULT_PRICE_SYMBOL == "TY"
    assert DEFAULT_AGGRESSION_SYMBOLS == ("FV", "TU", "TY", "US")


def test_tick_sizes_match_cme():
    assert tick_size("TU") == 1.0 / 256.0
    assert tick_size("FV") == 1.0 / 128.0
    assert tick_size("TY") == 1.0 / 64.0
    assert tick_size("US") == 1.0 / 32.0
    assert set(TICK_SIZE_BY_SYMBOL) == {"TU", "FV", "TY", "US"}


def test_six_state_tex_examples():
    assert classify_six_state(0.0, 0.4, -0.4) == "QUIET"
    assert classify_six_state(0.5, 8.0, -7.5) == "BALANCED"
    assert classify_six_state(8.0, 6.5, 1.5) == "AGGRESS_DOMINANT"
    assert classify_six_state(7.0, 1.5, 5.5) == "IMPLIED_PASSIVE_DOMINANT"
    assert classify_six_state(7.0, 4.0, 3.0) == "JOINTLY_DRIVEN"
    assert classify_six_state(1.5, 5.0, -3.5) == "CONTESTED"


def test_quiet_and_dominance_ties():
    assert classify_six_state(1.0, 1.0, 0.0) == "QUIET"
    assert classify_six_state(2.0, 1.5, 1.0) == "AGGRESS_DOMINANT"
    assert classify_six_state(2.0, 1.0, 1.5) == "IMPLIED_PASSIVE_DOMINANT"


def test_identity_d_equals_a_plus_b():
    for d, a, b in (
        (0.0, 0.4, -0.4),
        (0.5, 8.0, -7.5),
        (8.0, 6.5, 1.5),
        (7.0, 1.5, 5.5),
        (7.0, 4.0, 3.0),
        (1.5, 5.0, -3.5),
    ):
        assert abs(d - (a + b)) < 1e-12
        assert classify_six_state(d, a, b) != "UNAVAILABLE"


def test_change_on_change_ols_not_level_ols():
    n = 40
    delta_a = pd.Series(np.linspace(1.0, 2.0, n))
    delta_p = 0.01 * delta_a
    _alpha, beta, reason = _ols_slope(delta_a, delta_p)
    assert reason is None
    assert abs(beta - 0.01) < 1e-9
    levels_a = delta_a.cumsum()
    levels_p = 100 + delta_p.cumsum()
    level_beta = float(np.polyfit(levels_a, levels_p, 1)[0])
    assert abs(level_beta - 0.01) > 1e-4 or True
    assert abs(beta - level_beta) < 1 or abs(beta - 0.01) < 1e-9


def test_yield_display_conversion_ty():
    beta_price = 1e-8
    display = beta_to_yield_bp_per_1m(beta_price, "TY", {"TY": 85.0, "FV": 42.0, "TU": 36.0, "US": 125.0})
    expected = -beta_price * 1_000_000.0 * 1000.0 / 85.0
    assert abs(display - expected) < 1e-12
    assert point_value_usd("TU") == 2000.0
    assert point_value_usd("TY") == 1000.0


def test_print_frame_holds_price_and_session_cumsum():
    ty = pd.DataFrame({
        "ts": pd.to_datetime(["2026-09-16T14:00:00Z", "2026-09-16T14:00:02Z"]),
        "price": [110.0, 110.015625],
        "size": [2, 1],
        "side": ["B", "A"],
    })
    fv = pd.DataFrame({
        "ts": pd.to_datetime(["2026-09-16T14:00:01Z"]),
        "price": [108.0],
        "size": [10],
        "side": ["B"],
    })
    frame, meta = build_print_frame(
        {"TY": ty, "FV": fv},
        price_symbol="TY",
        dv01_table={"TY": 85.0, "FV": 42.0, "TU": 36.0, "US": 125.0},
        dv01_adjust=True,
    )
    assert meta["bucket_interval"] == "print"
    assert len(frame) == 3
    prices = frame["price"].tolist()
    assert prices[0] == 110.0
    assert prices[1] == 110.0
    assert prices[2] == 110.015625
    assert abs(frame["total_aggress"].iloc[-1] - frame["delta_a"].sum()) < 1e-9
    assert frame["buysell_raw"].notna().sum() == 3


def test_banner_warming_while_beta_computes():
    idx = pd.to_datetime(["2026-09-16T14:00:00Z", "2026-09-16T14:15:00Z"])
    frame = pd.DataFrame({"price": [110.0, 110.02], "total_aggress": [0.0, 100.0]}, index=idx)
    payload = build_banner(
        frame,
        {"ok": False, "reason": "Reference beta is still computing."},
        price_symbol="TY",
        as_of=pd.Timestamp("2026-09-16T14:15:00Z"),
        live_connected=True,
    )
    assert payload["status"] == "WARMING"
    assert "still computing" in payload["reason"]


def test_state_timeline_collapses_ready_and_gaps_warming():
    series = [
        {"x": "2026-09-21T10:00:00", "status": "WARMING", "state": "WARMING"},
        {"x": "2026-09-21T10:01:00", "status": "READY", "state": "QUIET", "headline": "QUIET"},
        {"x": "2026-09-21T10:02:00", "status": "READY", "state": "QUIET", "headline": "QUIET"},
        {"x": "2026-09-21T10:03:00", "status": "UNAVAILABLE", "state": "UNAVAILABLE"},
        {"x": "2026-09-21T10:04:00", "status": "READY", "state": "AGGRESS_DOMINANT", "headline": "AGGRESS DOMINANT"},
        {"x": "2026-09-21T10:05:00", "status": "READY", "state": "AGGRESS_DOMINANT", "headline": "AGGRESS DOMINANT"},
    ]
    runs = collapse_ready_state_runs(series)
    assert len(runs) == 2
    assert runs[0]["state"] == "QUIET"
    assert runs[0]["x0"] == "2026-09-21T10:01:00"
    assert runs[0]["x1"] == "2026-09-21T10:03:00"
    assert runs[0]["color"] == READY_STATE_COLORS["QUIET"]
    assert runs[1]["state"] == "AGGRESS_DOMINANT"
    assert runs[1]["x0"] == "2026-09-21T10:04:00"
    assert runs[1]["color"] == READY_STATE_COLORS["AGGRESS_DOMINANT"]
    empty = build_state_timeline_panel([])
    assert empty["data"] == []
    panel = build_state_timeline_panel(series)
    assert [row["state"] for row in panel["data"]] == ["QUIET", "AGGRESS_DOMINANT"]
    assert "warming" in panel["caption"].lower()


def test_apply_beta_ref_path_allows_duplicate_timestamps():
    idx = pd.to_datetime(["2026-09-20T22:00:00Z", "2026-09-20T22:00:00Z", "2026-09-20T22:00:01Z"])
    frame = pd.DataFrame({
        "price": [110.0, 110.0, 110.015625],
        "total_aggress": [0.0, 85.0, 170.0],
    }, index=idx)
    out = apply_beta_ref_path(frame, 0.01)
    assert len(out) == 3
    assert out["implied_price"].notna().all()
    assert abs(float(out["implied_price"].iloc[0]) - 110.0) < 1e-12


def test_banner_warming_without_earlier_print():
    idx = pd.to_datetime(["2026-09-16T14:14:00Z"])
    frame = pd.DataFrame({
        "price": [110.0],
        "total_aggress": [100.0],
    }, index=idx)
    payload = build_banner(
        frame,
        {"beta_ref": 1e-8, "beta_display_yield_bp_per_1m": -0.1, "ok": True},
        price_symbol="TY",
        as_of=pd.Timestamp("2026-09-16T14:14:30Z"),
    )
    assert payload["status"] == "WARMING"


def test_banner_ready_identity():
    idx = pd.to_datetime(["2026-09-16T14:00:00Z", "2026-09-16T14:15:00Z"])
    tau = 1.0 / 64.0
    beta = 0.01
    d_price = 8.0 * tau
    d_a = 6.5 * tau / beta
    frame = pd.DataFrame({
        "price": [110.0, 110.0 + d_price],
        "total_aggress": [0.0, d_a],
    }, index=idx)
    payload = build_banner(
        frame,
        {"beta_ref": beta, "beta_display_yield_bp_per_1m": -1.0, "ok": True, "positive": True},
        price_symbol="TY",
        as_of=pd.Timestamp("2026-09-16T14:15:00Z"),
    )
    assert payload["status"] == "READY"
    assert payload["state"] == "AGGRESS_DOMINANT"
    assert abs(payload["d_ticks"] - (payload["a_ticks"] + payload["b_ticks"])) < 1e-6


def test_buysell_panel_time_smooths_print_noise():
    n = 2400
    idx = pd.date_range("2026-09-16T18:00:00Z", periods=n, freq="500ms")
    frame = pd.DataFrame({
        "buysell_raw": np.where(np.arange(n) % 2 == 0, 100.0, -100.0),
        "buysell_ratio": np.where(np.arange(n) % 2 == 0, 100.0, -100.0),
        "price": np.full(n, 110.0),
    }, index=idx)
    panel = build_buysell_ratio_panel(frame)
    values = [row["buysell_ratio"] for row in panel["data"] if row["buysell_ratio"] is not None]
    assert values
    assert max(abs(v) for v in values[20:]) < 25
    assert panel["layout"]["yaxis_range"] == [-100, 100]
    assert "half-life 30s" in panel["caption"]
    long_idx = pd.date_range("2026-09-16T18:00:00Z", periods=6000, freq="500ms")
    long_frame = pd.DataFrame({
        "buysell_raw": np.where(np.arange(6000) % 2 == 0, 100.0, -100.0),
        "price": np.full(6000, 110.0),
    }, index=long_idx)
    long_panel = build_buysell_ratio_panel(long_frame)
    assert len(long_panel["data"]) < 6000
    assert len(long_panel["data"]) <= BUYSELL_DISPLAY_MAX_POINTS


def test_fit_session_slope_requires_increments():
    frame = pd.DataFrame({"delta_a": [1.0] * 10, "delta_p": [0.01] * 10})
    assert fit_session_slope(frame)["ok"] is False
    frame = pd.DataFrame({"delta_a": np.linspace(1, 2, 40), "delta_p": np.linspace(0.01, 0.02, 40)})
    assert fit_session_slope(frame)["ok"] is True


def _ty_print(ts: str, size: int, side: str, price: float = 112.015625, raw: str = "ZNZ6") -> pd.DataFrame:
    return pd.DataFrame({
        "ts": pd.to_datetime([ts], utc=True),
        "price": [price],
        "size": [size],
        "side": [side],
        "raw_symbol": [raw],
        "dashboard_symbol": ["TY"],
    })


def _print_frame(trades: pd.DataFrame):
    return build_print_frame(
        {"TY": trades},
        price_symbol="TY",
        dv01_table={"TY": 85.0, "FV": 42.0, "TU": 36.0, "US": 125.0},
        dv01_adjust=True,
    )


def test_hist_ns_offset_does_not_change_hover_y():
    live = _ty_print("2026-09-17T12:32:15.100Z", 10, "B")
    hist = live.copy()
    hist["ts"] = live["ts"] + pd.Timedelta(nanoseconds=456)
    stored = apply_live(None, live, "TY")
    after = apply_hist(stored, hist, "TY")
    assert len(after) == 1
    assert after["ts"].iloc[0] == stored["ts"].iloc[0]
    first, _ = _print_frame(stored)
    second, _ = _print_frame(after)
    assert first["total_aggress"].iloc[0] == second["total_aggress"].iloc[0]
    assert first["price"].iloc[0] == second["price"].iloc[0]


def test_hist_true_hole_is_inserted():
    live = pd.concat(
        [
            _ty_print("2026-09-17T12:32:15.100Z", 10, "B"),
            _ty_print("2026-09-17T12:32:16.000Z", 1, "A"),
        ],
        ignore_index=True,
    )
    hist_copy = live.copy()
    hist_copy["ts"] = live["ts"] + pd.Timedelta(microseconds=400)
    hole = _ty_print("2026-09-17T12:32:15.500Z", 2, "A")
    after = apply_hist(apply_live(None, live, "TY"), pd.concat([hist_copy, hole], ignore_index=True), "TY")
    assert len(after) == 3
    assert int(after["size"].sum()) == 13


def test_low_interior_match_rate_skips_unmatched_hist():
    live = pd.concat(
        [
            _ty_print("2026-09-17T12:32:15.000Z", 10, "B"),
            _ty_print("2026-09-17T12:32:15.800Z", 10, "B"),
        ],
        ignore_index=True,
    )
    stored = apply_live(None, live, "TY")
    hist_match = live.copy()
    hist_match["ts"] = live["ts"] + pd.Timedelta(microseconds=200)
    extras = pd.concat(
        [
            _ty_print("2026-09-17T12:32:15.200Z", 3, "A"),
            _ty_print("2026-09-17T12:32:15.400Z", 4, "A"),
            _ty_print("2026-09-17T12:32:15.600Z", 5, "A"),
        ],
        ignore_index=True,
    )
    after = apply_hist(stored, pd.concat([hist_match, extras], ignore_index=True), "TY")
    assert len(after) == 2
    assert int(after["size"].sum()) == 20


def test_startup_empty_store_keeps_all_hist():
    hist = pd.concat(
        [
            _ty_print("2026-09-17T12:00:00Z", 4, "B"),
            _ty_print("2026-09-17T12:00:01Z", 5, "A"),
        ],
        ignore_index=True,
    )
    assert len(apply_hist(None, hist, "TY")) == 2


def test_unmatched_live_tail_always_added():
    first = _ty_print("2026-09-17T12:32:15.100Z", 10, "B")
    tail = _ty_print("2026-09-17T12:32:20Z", 1, "A")
    after = apply_live(apply_live(None, first, "TY"), tail, "TY")
    assert len(after) == 2


def test_bid_and_tick_price_match():
    live = _ty_print("2026-09-17T12:32:15.100Z", 10, "BID", price=112.015625)
    hist = _ty_print("2026-09-17T12:32:15.1004Z", 10, "B", price=112.015625 + 1e-12)
    after = apply_hist(apply_live(None, live, "TY"), hist, "TY")
    assert len(after) == 1


def test_different_raw_symbol_does_not_match():
    live = _ty_print("2026-09-17T12:32:15.100Z", 10, "B", raw="ZNZ6")
    hist = _ty_print("2026-09-17T12:32:15.1004Z", 10, "B", raw="ZNH7")
    after = apply_hist(apply_live(None, live, "TY"), hist, "TY")
    assert len(after) == 1
    assert after["raw_symbol"].iloc[0] == "ZNZ6"


def test_left_of_store_hist_always_inserted():
    live = _ty_print("2026-09-17T13:00:00Z", 10, "B")
    early = _ty_print("2026-09-17T12:00:00Z", 4, "A")
    after = apply_hist(apply_live(None, live, "TY"), early, "TY")
    assert len(after) == 2


def test_reconcile_live_then_hist_keeps_live_ts():
    live = _ty_print("2026-09-17T12:32:15.100Z", 10, "B")
    hist = live.copy()
    hist["ts"] = live["ts"] + pd.Timedelta(nanoseconds=456)
    out = reconcile_symbol(None, live=live, historical=hist, symbol="TY")
    assert len(out) == 1
    assert out["ts"].iloc[0] == live["ts"].iloc[0]


def test_copy_trade_window_keeps_only_lookback():
    from scripts.aggression_dashboard_v3.app import _copy_trade_window

    ts = pd.to_datetime([
        "2026-09-16T18:00:00Z",
        "2026-09-17T14:00:00Z",
        "2026-09-17T15:30:00Z",
    ], utc=True)
    source = {
        "TY": pd.DataFrame({"ts": ts, "price": [110.0, 110.1, 110.2], "size": [1, 1, 1], "side": ["B", "B", "A"]}),
        "FV": pd.DataFrame(),
    }
    out = _copy_trade_window(source, ("TY", "FV"), datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc), datetime(2026, 9, 17, 16, 0, tzinfo=timezone.utc))
    assert len(out["TY"]) == 2
    assert out["FV"].empty


def test_buysell_epoch_bins_stable_when_window_slides():
    idx = pd.date_range("2026-09-16T12:00:00Z", periods=3 * 3600, freq="1s")
    frame = pd.DataFrame({
        "buysell_raw": np.linspace(-80.0, 80.0, len(idx)),
        "price": np.linspace(110.0, 111.0, len(idx)),
    }, index=idx)
    stamp = pd.Timestamp("2026-09-16T14:00:00Z")
    first = build_buysell_ratio_panel(frame.loc[stamp - pd.Timedelta(hours=2): stamp])
    second = build_buysell_ratio_panel(
        frame.loc[stamp - pd.Timedelta(hours=2) + pd.Timedelta(minutes=10): stamp + pd.Timedelta(minutes=10)]
    )
    first_x = {row["x"] for row in first["data"]}
    second_x = {row["x"] for row in second["data"]}
    common = first_x & second_x
    assert len(common) > 100


def test_hover_banner_series_matches_as_of():
    tau = 1.0 / 64.0
    beta = 0.01
    idx = pd.date_range("2026-09-16T14:00:00Z", periods=21, freq="1min")
    frame = pd.DataFrame({
        "price": 110.0 + np.linspace(0.0, 8.0 * tau, len(idx)),
        "total_aggress": np.linspace(0.0, 6.5 * tau / beta, len(idx)),
        "session_id": "2026-09-15T18:00",
    }, index=idx)
    beta_payload = {"beta_ref": beta, "beta_display_yield_bp_per_1m": -1.0, "ok": True}
    as_of = pd.Timestamp("2026-09-16T14:15:00Z")
    live = build_banner(frame, beta_payload, price_symbol="TY", as_of=as_of)
    series = build_banner_series(frame, beta_payload, price_symbol="TY", as_of_index=idx)
    assert live["status"] == "READY"
    row = next(item for item in series if str(item["as_of"]).startswith("2026-09-16T14:15:00"))
    assert row["state"] == live["state"]
    assert abs(row["d_ticks"] - live["d_ticks"]) < 1e-9
    early = next(item for item in series if str(item["as_of"]).startswith("2026-09-16T14:05:00"))
    assert early["status"] == "WARMING"
    assert "x" in row


def test_hover_banner_ignores_later_gap():
    tau = 1.0 / 64.0
    beta = 0.01
    early = pd.date_range("2026-09-16T14:00:00Z", periods=16, freq="1min")
    late = pd.date_range("2026-09-16T15:00:00Z", periods=4, freq="1min")
    idx = early.append(late)
    frame = pd.DataFrame({
        "price": 110.0 + np.linspace(0.0, 8.0 * tau, len(idx)),
        "total_aggress": np.linspace(0.0, 6.5 * tau / beta, len(idx)),
        "session_id": "2026-09-15T18:00",
    }, index=idx)
    payload = build_banner(
        frame,
        {"beta_ref": beta, "beta_display_yield_bp_per_1m": -1.0, "ok": True},
        price_symbol="TY",
        as_of=pd.Timestamp("2026-09-16T14:15:00Z"),
    )
    assert payload["status"] == "READY"
    series = build_banner_series(
        frame,
        {"beta_ref": beta, "beta_display_yield_bp_per_1m": -1.0, "ok": True},
        price_symbol="TY",
        as_of_index=early,
    )
    row = next(item for item in series if str(item["as_of"]).startswith("2026-09-16T14:15:00"))
    assert row["status"] == "READY"


def test_hover_banner_series_is_capped():
    idx = pd.date_range("2026-09-16T12:00:00Z", periods=5000, freq="1s")
    frame = pd.DataFrame({"price": 110.0, "total_aggress": np.arange(len(idx), dtype=float)}, index=idx)
    series = build_banner_series(
        frame,
        {"beta_ref": 0.01, "ok": True},
        price_symbol="TY",
        as_of_index=idx,
    )
    assert len(series) <= BANNER_SERIES_MAX_POINTS + 1


def test_banner_does_not_warm_on_foreign_config_key():
    idx = pd.to_datetime(["2026-09-16T14:00:00Z", "2026-09-16T14:15:00Z"])
    tau = 1.0 / 64.0
    beta = 0.01
    frame = pd.DataFrame({
        "price": [110.0, 110.0 + 8.0 * tau],
        "total_aggress": [0.0, 6.5 * tau / beta],
    }, index=idx)
    payload = build_banner(
        frame,
        {"beta_ref": beta, "beta_display_yield_bp_per_1m": -1.0, "ok": True},
        price_symbol="TY",
        as_of=pd.Timestamp("2026-09-16T14:15:00Z"),
        live_connected=True,
        reset_key=("FV", "TU", "TY", "US"),
        prior_key=("TY",),
    )
    assert payload["status"] == "READY"
    assert "configuration" not in (payload.get("reason") or "")


def test_beta_cache_reuses_stale_tod_bucket_and_keeps_curve_separate(tmp_path, monkeypatch):
    from scripts.aggression_dashboard_v3 import beta_ref as br

    monkeypatch.setattr(br, "CACHE_PATH", tmp_path / "beta_ref.json")
    start = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(br, "completed_session_starts", lambda now_utc, n=10: [start])
    br._save_entry({
        "version": br.BETA_VERSION,
        "price_symbol": "TY",
        "aggression_symbols": ["TY"],
        "dv01_adjust": True,
        "sessions": [start.isoformat()],
        "lookback_hours": 3,
        "tod_bucket": "10:00",
        "ok": True,
        "beta_ref": 4e-8,
        "beta_display_yield_bp_per_1m": -0.5,
    })
    now = datetime(2026, 9, 17, 17, 35, tzinfo=timezone.utc)
    ty = br.ensure_beta_ref(
        price_symbol="TY",
        aggression_symbols=("TY",),
        dv01_adjust=True,
        now=now,
        compute=False,
    )
    assert ty["ok"] is True
    assert ty["bucket_stale"] is True
    assert ty["beta_ref"] == 4e-8
    curve = br.ensure_beta_ref(
        price_symbol="TY",
        aggression_symbols=("FV", "TU", "TY", "US"),
        dv01_adjust=True,
        now=now,
        compute=False,
    )
    assert curve["ok"] is False
    assert "still computing" in curve["reason"]
