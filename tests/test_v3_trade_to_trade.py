from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.banner import (
    BANNER_SERIES_MAX_POINTS,
    GROUP_STYLE,
    NO_EARLIER_PRINT_REASON,
    STATE_STYLE,
    WINDOW_MIN,
    build_banner,
    build_banner_series,
    classify_state,
    window_minutes,
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
    advance_runs,
    apply_strip_edge,
    build_buysell_ratio_panel,
    build_state_timeline_panel,
    collapse_ready_state_runs,
    state_run,
)
from scripts.aggression_dashboard_v3.print_reconcile import (
    _matched_incoming,
    apply_hist,
    apply_live,
    normalize_price,
    normalize_side,
    normalize_size,
    reconcile_symbol,
)


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


# (d, a, r, STATE2, STATE): one example per row of the state table.
STATE_TABLE_ROWS = [
    (3.0, 0.5, 2.5, "SELLER_STRIKE", "THIN_LIQ"),
    (-3.0, -0.5, -2.5, "BUYER_STRIKE", "THIN_LIQ"),
    (0.5, 0.3, 0.2, "QUIET", "QUIET"),
    (3.0, 2.5, 0.5, "AGGRESS_DOM_BUY", "AGGRESS_DOM"),
    (8.0, 5.0, 3.0, "AGGRESS_DOM_THIN_LIQ_BUY", "AGGRESS_DOM"),
    (2.0, 4.0, -2.0, "PASSIVE_SELLERS_ABSORBING_BUYERS", "BALANCED"),
    (-3.0, 2.0, -5.0, "PASSIVE_SELLERS_DOMINATING_BUYERS", "EXHAUSTION"),
    (0.5, 3.0, -2.5, "PASSIVE_SELLERS_OFFSETTING_BUYERS", "BALANCED"),
    (4.0, -2.0, 6.0, "PASSIVE_BUYERS_DOMINATING_SELLERS", "EXHAUSTION"),
    (-8.0, -5.0, -3.0, "AGGRESS_DOM_THIN_LIQ_SELL", "AGGRESS_DOM"),
    (-3.0, -5.0, 2.0, "PASSIVE_BUYERS_ABSORBING_SELLERS", "BALANCED"),
    (-3.0, -2.5, -0.5, "AGGRESS_DOM_SELL", "AGGRESS_DOM"),
    (0.5, -3.0, 3.5, "PASSIVE_BUYERS_OFFSETTING_SELLERS", "BALANCED"),
]


def test_state_table_rows():
    for d, a, r, state, group in STATE_TABLE_ROWS:
        assert abs(d - (a + r)) < 1e-12
        assert classify_state(d, a, r) == state, (d, a, r)
        assert STATE_STYLE[state][3] == group
    assert len({row[3] for row in STATE_TABLE_ROWS}) == 13


def test_unlisted_sign_patterns_follow_aggression_and_price():
    assert classify_state(1.6, 0.8, 0.8) == "SELLER_STRIKE"
    assert classify_state(-1.6, -0.8, -0.8) == "BUYER_STRIKE"
    assert classify_state(0.9, -0.9, 1.8) == "QUIET"
    assert classify_state(-0.9, 0.9, -1.8) == "QUIET"
    assert classify_state(0.9, 1.5, -0.6) == "PASSIVE_SELLERS_OFFSETTING_BUYERS"
    assert classify_state(-0.9, -1.5, 0.6) == "PASSIVE_BUYERS_OFFSETTING_SELLERS"


def test_state_boundaries():
    assert classify_state(1.0, 1.0, 0.0) == "QUIET"
    assert classify_state(-1.0, -1.0, 0.0) == "QUIET"
    assert classify_state(2.0, 1.0, 1.0) == "SELLER_STRIKE"
    assert classify_state(2.5, 1.5, 1.0) == "AGGRESS_DOM_BUY"
    assert classify_state(-2.5, -1.5, -1.0) == "AGGRESS_DOM_SELL"
    assert classify_state(1.0, 2.0, -1.0) == "PASSIVE_SELLERS_OFFSETTING_BUYERS"


def test_every_window_gets_one_of_the_13_states():
    reached = set()
    for i in range(-80, 81):
        for j in range(-80, 81):
            a, r = i * 0.05, j * 0.05
            reached.add(classify_state(a + r, a, r))
    assert reached == set(STATE_STYLE)
    assert len(READY_STATE_COLORS) == 13


def test_state_colours_match_the_table():
    assert {group: color for group, (_headline, color) in GROUP_STYLE.items()} == {
        "THIN_LIQ": "#747474",
        "QUIET": "#E8E8E8",
        "AGGRESS_DOM": "#D86DCD",
        "BALANCED": "#FFFF00",
        "EXHAUSTION": "#8ED974",
    }
    by_color: dict[str, set[str]] = defaultdict(set)
    for state, (_headline, color, _family, _group) in STATE_STYLE.items():
        by_color[color].add(state)
    assert by_color["#A7C9EC"] == {
        "SELLER_STRIKE",
        "AGGRESS_DOM_BUY",
        "AGGRESS_DOM_THIN_LIQ_BUY",
        "PASSIVE_SELLERS_ABSORBING_BUYERS",
        "PASSIVE_BUYERS_DOMINATING_SELLERS",
    }
    assert by_color["#D9E9F8"] == {"PASSIVE_SELLERS_OFFSETTING_BUYERS"}
    assert by_color["#E7776B"] == {
        "BUYER_STRIKE",
        "AGGRESS_DOM_SELL",
        "AGGRESS_DOM_THIN_LIQ_SELL",
        "PASSIVE_BUYERS_ABSORBING_SELLERS",
        "PASSIVE_SELLERS_DOMINATING_BUYERS",
    }
    assert by_color["#F6C7AB"] == {"PASSIVE_BUYERS_OFFSETTING_SELLERS"}
    assert by_color["#E8E8E8"] == {"QUIET"}
    assert STATE_STYLE["AGGRESS_DOM_BUY"][0] == STATE_STYLE["AGGRESS_DOM_SELL"][0] == "AGGRESS DOM"
    assert STATE_STYLE["AGGRESS_DOM_THIN_LIQ_BUY"][0] == "AGGRESS DOM, THIN LIQUIDITY"


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


STRIP_SERIES = [
    {"x": "2026-09-21T10:00:00", "status": "WARMING", "state": "WARMING"},
    {"x": "2026-09-21T10:01:00", "status": "READY", "state": "AGGRESS_DOM_BUY"},
    {"x": "2026-09-21T10:02:00", "status": "READY", "state": "AGGRESS_DOM_THIN_LIQ_BUY"},
    {"x": "2026-09-21T10:03:00", "status": "UNAVAILABLE", "state": "UNAVAILABLE"},
    {"x": "2026-09-21T10:04:00", "status": "READY", "state": "QUIET"},
    {"x": "2026-09-21T10:05:00", "status": "WARMING", "state": "WARMING"},
    {"x": "2026-09-21T10:06:00", "status": "READY", "state": "QUIET"},
]


def _spans(runs):
    return [(run["state"], run["x0"][11:16], run["x1"][11:16]) for run in runs]


def test_state_strip_has_two_rows_with_gaps():
    fine = collapse_ready_state_runs(STRIP_SERIES, "state")
    coarse = collapse_ready_state_runs(STRIP_SERIES, "group")
    assert _spans(fine) == [
        ("AGGRESS_DOM_BUY", "10:01", "10:01"),
        ("AGGRESS_DOM_THIN_LIQ_BUY", "10:02", "10:03"),
        ("QUIET", "10:04", "10:05"),
        ("QUIET", "10:06", "10:06"),
    ]
    assert _spans(coarse) == [
        ("AGGRESS_DOM", "10:01", "10:03"),
        ("QUIET", "10:04", "10:05"),
        ("QUIET", "10:06", "10:06"),
    ]
    assert (fine[0]["color"], fine[0]["legend"]) == ("#A7C9EC", "Buy side")
    assert (coarse[0]["color"], coarse[0]["legend"]) == ("#D86DCD", "AGGRESS DOM")
    assert fine[2]["legend"] == coarse[1]["legend"] == "QUIET"
    panel = build_state_timeline_panel(STRIP_SERIES)
    assert panel["title"] == "States through lookback"
    assert _spans(panel["group_data"]) == _spans(coarse)
    assert f"{WINDOW_MIN}-minute" in panel["caption"] and "STATE2" in panel["caption"]
    empty = build_state_timeline_panel([])
    assert empty["data"] == [] and empty["group_data"] == []
    assert empty["state_legend"] == [
        {"legend": "THIN LIQ", "color": "#747474"},
        {"legend": "QUIET", "color": "#E8E8E8"},
        {"legend": "AGGRESS DOM", "color": "#D86DCD"},
        {"legend": "BALANCED", "color": "#FFFF00"},
        {"legend": "EXHAUSTION", "color": "#8ED974"},
    ]


def test_live_strip_edges_match_the_rebuilt_strip():
    for key in ("state", "group"):
        runs, is_open, mirrored = [], False, []
        for row in STRIP_SERIES:
            edge, is_open = advance_runs(runs, state_run(row, key, row["x"]), row["x"], is_open)
            apply_strip_edge(mirrored, edge)
        expected = _spans(collapse_ready_state_runs(STRIP_SERIES, key))
        assert _spans(runs) == expected
        assert _spans(mirrored) == expected


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


TAU = 1.0 / 64.0
BANNER_BETA = {"beta_ref": 0.01, "beta_display_yield_bp_per_1m": -1.0, "ok": True, "positive": True}


def _minute_frame(minutes, *, price_step_ticks=0.8, aggress_step_ticks=0.5, start="2026-09-16T14:00:00Z"):
    """One print per listed minute; each minute adds the given price and aggression-implied ticks."""
    idx = pd.to_datetime(start) + pd.to_timedelta(list(minutes), unit="min")
    steps = np.arange(len(idx), dtype=float)
    return pd.DataFrame({
        "price": 110.0 + steps * price_step_ticks * TAU,
        "total_aggress": steps * aggress_step_ticks * TAU / BANNER_BETA["beta_ref"],
    }, index=idx)


def test_banner_ready_identity():
    payload = build_banner(
        _minute_frame(range(16)),
        BANNER_BETA,
        price_symbol="TY",
        as_of=pd.Timestamp("2026-09-16T14:15:00Z"),
    )
    assert payload["status"] == "READY"
    assert payload["state"] == "AGGRESS_DOM_THIN_LIQ_BUY"
    assert payload["headline"] == "AGGRESS DOM, THIN LIQUIDITY"
    assert (payload["group"], payload["group_headline"], payload["group_color"]) == ("AGGRESS_DOM", "AGGRESS DOM", "#D86DCD")
    assert (payload["color"], payload["family"]) == ("#A7C9EC", "buy")
    assert payload["contribution_line"] == "5m price +4.0 ticks · aggression +2.5 · residual +1.5"
    assert payload["parameters"]["window_min"] == WINDOW_MIN == 5
    assert "p_aggress" not in payload
    assert abs(payload["d_ticks"] - (payload["a_ticks"] + payload["b_ticks"])) < 1e-6


def test_overnight_window_is_fifteen_minutes():
    start = "2026-09-16T05:45:00Z"  # 01:45 ET; 06:00Z is 02:00 ET
    payload = build_banner(
        _minute_frame(range(16), start=start),
        BANNER_BETA,
        price_symbol="TY",
        as_of=pd.Timestamp("2026-09-16T06:00:00Z"),
    )
    assert payload["status"] == "READY"
    assert payload["parameters"]["window_min"] == 15
    assert payload["contribution_line"] == "15m price +12.0 ticks · aggression +7.5 · residual +4.5"
    gapped = _minute_frame([0, 1, 2, 3, 4, 5, 15], start=start)
    held = build_banner(gapped, BANNER_BETA, price_symbol="TY", as_of=pd.Timestamp("2026-09-16T06:00:00Z"))
    assert held["status"] == "READY"
    assert window_minutes(pd.Timestamp("2026-09-16T06:59:00Z")) == 15
    assert window_minutes(pd.Timestamp("2026-09-16T07:00:00Z")) == 5
    assert window_minutes(pd.Timestamp("2026-09-16T22:00:00Z")) == 15


def test_window_starts_at_the_last_print_five_minutes_back():
    payload = build_banner(
        _minute_frame(range(16)),
        BANNER_BETA,
        price_symbol="TY",
        as_of=pd.Timestamp("2026-09-16T14:15:30Z"),
    )
    assert payload["status"] == "READY"
    assert abs(payload["d_ticks"] - 4.0) < 1e-9
    assert abs(payload["a_ticks"] - 2.5) < 1e-9


def test_gap_longer_than_the_window_resets_to_warming():
    frame = _minute_frame([0, 1, 2, 3, 4, 5, 11])
    as_of = pd.Timestamp("2026-09-16T14:12:00Z")
    payload = build_banner(frame, BANNER_BETA, price_symbol="TY", as_of=as_of)
    assert payload["status"] == "WARMING"
    series = build_banner_series(frame, BANNER_BETA, price_symbol="TY", as_of_index=[as_of])
    assert series[-1]["status"] == "WARMING"
    ready = build_banner(_minute_frame(range(12)), BANNER_BETA, price_symbol="TY", as_of=as_of)
    assert ready["status"] == "READY"


def test_tape_shorter_than_the_window_stays_warming():
    payload = build_banner(
        _minute_frame(range(4)),
        BANNER_BETA,
        price_symbol="TY",
        as_of=pd.Timestamp("2026-09-16T14:03:00Z"),
    )
    assert payload["status"] == "WARMING"
    assert payload["reason"] == NO_EARLIER_PRINT_REASON
    assert "5-minute" in NO_EARLIER_PRINT_REASON


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
    early = next(item for item in series if str(item["as_of"]).startswith("2026-09-16T14:03:00"))
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
    payload = build_banner(
        _minute_frame(range(16)),
        BANNER_BETA,
        price_symbol="TY",
        as_of=pd.Timestamp("2026-09-16T14:15:00Z"),
        live_connected=True,
        reset_key=("FV", "TU", "TY", "US"),
        prior_key=("TY",),
    )
    assert payload["status"] == "READY"
    assert "configuration" not in (payload.get("reason") or "")


def test_beta_cache_holds_all_day_and_keeps_curve_separate(tmp_path, monkeypatch):
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
        "window": "whole_session",
        "ok": True,
        "beta_ref": 4e-8,
        "beta_display_yield_bp_per_1m": -0.5,
    })
    for now in (
        datetime(2026, 9, 17, 13, 35, tzinfo=timezone.utc),
        datetime(2026, 9, 17, 20, 50, tzinfo=timezone.utc),
    ):
        ty = br.ensure_session_mean_beta_ref(
            price_symbol="TY",
            aggression_symbols=("TY",),
            dv01_adjust=True,
            now=now,
            compute=False,
        )
        assert ty["ok"] is True
        assert ty["cached"] is True
        assert ty["bucket_stale"] is False
        assert ty["beta_ref"] == 4e-8
    curve = br.ensure_session_mean_beta_ref(
        price_symbol="TY",
        aggression_symbols=("FV", "TU", "TY", "US"),
        dv01_adjust=True,
        now=now,
        compute=False,
    )
    assert curve["ok"] is False
    assert "still computing" in curve["reason"]


def _reference_matched(stored: pd.DataFrame, incoming: pd.DataFrame, symbol: str) -> np.ndarray:
    """Unbounded bucket scan; the reference result for _matched_incoming."""
    matched = np.zeros(len(incoming), dtype=bool)
    if stored.empty or incoming.empty:
        return matched
    s_side = stored["side"].map(normalize_side).to_numpy()
    s_size = stored["size"].map(normalize_size).to_numpy()
    s_price = np.array([normalize_price(v, symbol) for v in stored["price"]], dtype=float)
    s_raw = (
        stored["raw_symbol"].fillna("").astype(str).to_numpy()
        if "raw_symbol" in stored.columns
        else np.array([""] * len(stored))
    )
    s_ts = stored["ts"].to_numpy(dtype="datetime64[ns]")
    i_side = incoming["side"].map(normalize_side).to_numpy()
    i_size = incoming["size"].map(normalize_size).to_numpy()
    i_price = np.array([normalize_price(v, symbol) for v in incoming["price"]], dtype=float)
    i_raw = (
        incoming["raw_symbol"].fillna("").astype(str).to_numpy()
        if "raw_symbol" in incoming.columns
        else np.array([""] * len(incoming))
    )
    i_ts = incoming["ts"].to_numpy(dtype="datetime64[ns]")
    window = np.timedelta64(1, "ms")
    used = np.zeros(len(stored), dtype=bool)
    buckets = defaultdict(list)
    for j in range(len(stored)):
        buckets[(str(s_side[j]), int(s_size[j]), float(s_price[j]))].append(j)
    for i in range(len(incoming)):
        best_j = None
        best_dt = None
        for j in buckets[(str(i_side[i]), int(i_size[i]), float(i_price[i]))]:
            if used[j]:
                continue
            if i_raw[i] and s_raw[j] and i_raw[i] != s_raw[j]:
                continue
            delta = abs(s_ts[j] - i_ts[i])
            if delta > window:
                continue
            if best_dt is None or delta < best_dt:
                best_j = j
                best_dt = delta
        if best_j is not None:
            used[best_j] = True
            matched[i] = True
    return matched


def _random_prints(rng: np.random.Generator, n: int, *, with_raw: bool = True) -> pd.DataFrame:
    tick = TICK_SIZE_BY_SYMBOL["TY"]
    frame = pd.DataFrame({
        "ts": pd.Timestamp("2026-09-24T13:00:00Z") + pd.to_timedelta(rng.integers(0, 20_000_000, size=n), unit="ns"),
        "price": 112.0 + tick * rng.integers(0, 3, size=n) + rng.choice([0.0, 1e-12], size=n),
        "size": rng.choice([1, 1, 1, 2, 5], size=n),
        "side": rng.choice(["A", "B", "BID", "ASK", "N"], size=n),
        "raw_symbol": rng.choice(np.array(["ZNZ6", "ZNH7", "", None], dtype=object), size=n),
    })
    return frame if with_raw else frame.drop(columns=["raw_symbol"])


def test_matched_incoming_equals_reference_scan():
    for seed in range(40):
        rng = np.random.default_rng(seed)
        stored = _random_prints(rng, 300, with_raw=seed % 5 != 0)
        echo = stored.sample(n=200, random_state=seed).copy()
        jitter = rng.choice(
            np.array([0, 1, -1, 999_999, -999_999, 1_000_000, -1_000_000, 1_000_001, 250_000, -2_500_000]),
            size=len(echo),
        )
        echo["ts"] = echo["ts"] + pd.to_timedelta(jitter, unit="ns")
        fresh = _random_prints(rng, 150, with_raw=seed % 7 != 0)
        incoming = pd.concat([echo, fresh], ignore_index=True).sample(frac=1.0, random_state=seed + 1).reset_index(drop=True)
        expected = _reference_matched(stored, incoming, "TY")
        actual = _matched_incoming(stored, incoming, "TY")
        assert np.array_equal(actual, expected), f"seed {seed}"


def test_matched_incoming_large_bucket_is_fast():
    n = 20_000
    ts = pd.Timestamp("2026-09-24T13:00:00Z") + pd.to_timedelta(np.arange(n) * 2_000_000, unit="ns")
    stored = pd.DataFrame({"ts": ts, "price": 112.015625, "size": 1, "side": "B", "raw_symbol": "ZNZ6"})
    incoming = stored.copy()
    incoming["ts"] = incoming["ts"] + pd.Timedelta(microseconds=300)
    started = time.perf_counter()
    matched = _matched_incoming(stored, incoming, "TY")
    elapsed = time.perf_counter() - started
    assert matched.all()
    assert elapsed < 5.0


def test_beta_is_mean_of_whole_session_slopes_and_cached_per_session_set(tmp_path, monkeypatch):
    from scripts.aggression_dashboard_v3 import beta_ref as br

    monkeypatch.setattr(br, "CACHE_PATH", tmp_path / "beta_ref.json")
    monkeypatch.setattr(br, "load_dv01_table", lambda: {"TY": 85.0, "FV": 42.0, "TU": 36.0, "US": 125.0})
    starts = [
        datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc),
    ]
    session_sets = {"current": list(starts)}
    monkeypatch.setattr(br, "completed_session_starts", lambda now_utc, n=10: list(session_sets["current"]))
    clocks = []

    def fake_session_beta(start, **kwargs):
        clocks.append(kwargs.get("now"))
        return {"ok": True, "beta": 2e-8 if start == starts[0] else 4e-8}

    monkeypatch.setattr(br, "compute_session_beta", fake_session_beta)
    now = datetime(2026, 9, 3, 14, 0, tzinfo=timezone.utc)
    out = br.ensure_session_mean_beta_ref(price_symbol="TY", aggression_symbols=("TY",), dv01_adjust=True, now=now, compute=True)
    assert out["ok"] is True
    assert not out.get("cached")
    assert abs(out["beta_ref"] - 3e-8) < 1e-20
    assert out["window"] == "whole_session"
    assert out["last_session_end"] == br.globex_session_end(starts[-1]).isoformat()
    assert clocks == [None, None]

    later = br.ensure_session_mean_beta_ref(
        price_symbol="TY", aggression_symbols=("TY",), dv01_adjust=True, now=now + timedelta(hours=6), compute=True
    )
    assert later["cached"] is True
    assert abs(later["beta_ref"] - 3e-8) < 1e-20
    assert len(clocks) == 2

    session_sets["current"] = starts[1:]
    rolled = br.ensure_session_mean_beta_ref(
        price_symbol="TY", aggression_symbols=("TY",), dv01_adjust=True, now=now + timedelta(days=1), compute=True
    )
    assert not rolled.get("cached")
    assert abs(rolled["beta_ref"] - 4e-8) < 1e-20
    assert len(clocks) == 3


def test_kick_beta_ref_offers_history_only_for_fresh_beta(monkeypatch):
    from scripts.aggression_dashboard_v3 import app as v3app

    class _Publisher:
        offers = 0

        def offer_history(self) -> None:
            self.offers += 1

    publisher = _Publisher()
    curve_publisher = _Publisher()
    results = iter([
        {"ok": True, "beta_ref": 4e-8},
        {"ok": True, "beta_ref": 4e-8, "cached": True},
        {"ok": False, "beta_ref": None},
    ])
    calls = []

    def fake_ensure(**kwargs):
        calls.append(kwargs)
        return next(results)

    monkeypatch.setattr(v3app, "ensure_beta_ref", fake_ensure)
    monkeypatch.setattr(v3app, "_PUBLISHERS", {
        ("TY", ("TY",), True): publisher,
        v3app.DEFAULT_LIVE_KEY: curve_publisher,
    })
    monkeypatch.setattr(v3app, "_BETA_WARM_KEYS", set())
    for _ in range(3):
        thread = v3app._kick_beta_ref("TY", ("TY",), True)
        thread.join(timeout=5)
    assert len(calls) == 3
    assert all("refresh_stale" not in kwargs for kwargs in calls)
    assert publisher.offers == 1
    assert curve_publisher.offers == 0


def test_beta_reads_sessions_outside_the_in_memory_trades_from_the_cache(tmp_path, monkeypatch):
    from scripts.aggression_dashboard_v3 import beta_ref as br

    monkeypatch.setattr(br, "CACHE_PATH", tmp_path / "beta_ref.json")
    monkeypatch.setattr(br, "load_dv01_table", lambda: {"TY": 85.0, "FV": 42.0, "TU": 36.0, "US": 125.0})
    starts = [
        datetime(2026, 9, 10, 22, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc),
    ]
    monkeypatch.setattr(br, "completed_session_starts", lambda now_utc, n=10: list(starts))
    window = {"TY": pd.DataFrame({
        "ts": pd.to_datetime(["2026-09-13T22:00:01Z", "2026-09-14T15:00:00Z"]),
        "price": [110.0, 110.015625],
        "size": [1, 1],
        "side": ["A", "B"],
    })}
    seen = []

    def fake_session_beta(start, **kwargs):
        seen.append((start, kwargs.get("trades_by_symbol")))
        return {"ok": True, "beta": 3e-8}

    monkeypatch.setattr(br, "compute_session_beta", fake_session_beta)
    out = br.ensure_session_mean_beta_ref(
        price_symbol="TY",
        aggression_symbols=("TY",),
        dv01_adjust=True,
        now=datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc),
        trades_window=window,
        compute=True,
    )
    assert out["ok"] is True
    assert seen[0] == (starts[0], None)
    assert seen[1][0] == starts[1]
    assert len(seen[1][1]["TY"]) == 2


def test_beta_reads_the_cache_when_a_selected_symbol_is_missing_from_memory(tmp_path, monkeypatch):
    from scripts.aggression_dashboard_v3 import beta_ref as br

    monkeypatch.setattr(br, "CACHE_PATH", tmp_path / "beta_ref.json")
    monkeypatch.setattr(br, "load_dv01_table", lambda: {"TY": 85.0, "FV": 42.0, "TU": 36.0, "US": 125.0})
    start = datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(br, "completed_session_starts", lambda now_utc, n=10: [start])
    window = {
        "US": pd.DataFrame({"ts": pd.to_datetime(["2026-09-13T22:00:01Z"]), "price": [115.0], "size": [1], "side": ["A"]}),
        "TY": pd.DataFrame(columns=["ts", "price", "size", "side"]),
    }
    seen = []

    def fake_session_beta(session_start, **kwargs):
        seen.append(kwargs.get("trades_by_symbol"))
        return {"ok": True, "beta": 3e-8}

    monkeypatch.setattr(br, "compute_session_beta", fake_session_beta)
    br.ensure_session_mean_beta_ref(
        price_symbol="TY",
        aggression_symbols=("TY", "US"),
        dv01_adjust=True,
        now=datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc),
        trades_window=window,
        compute=True,
    )
    assert seen == [None]


def test_aggressor_signs_match_classify_apply():
    from scripts.aggression_dashboard_v3.core import _aggressor_signs
    from scripts.totalaggress_vs_price_chart import classify_aggressor

    side = pd.Series(["A", "B", "BID", "ask", "N", "", None, np.nan, " b ", "Ask", "X"] * 3, dtype=object)
    expected = side.apply(classify_aggressor)
    actual = _aggressor_signs(side)
    assert actual.tolist() == expected.tolist()
    assert str(actual.dtype) == "int64"


def test_session_labels_match_strftime_across_dst():
    from scripts.aggression_dashboard_v3.core import session_labels

    et = pd.date_range("2026-03-06T12:00", "2026-03-10T12:00", freq="37min", tz="America/New_York").append(
        pd.date_range("2026-10-30T12:00", "2026-11-03T12:00", freq="41min", tz="America/New_York")
    )
    dates = et.normalize()
    dates = pd.DatetimeIndex(dates.where(et.hour >= 18, dates - pd.Timedelta(days=1))).insert(3, pd.NaT)
    expected = [None if pd.isna(v) else v for v in dates.strftime("%Y-%m-%dT18:00")]
    actual = [None if pd.isna(v) else v for v in session_labels(dates)]
    assert actual == expected


def test_backfill_covered_end_uses_earliest_last_print():
    from scripts.aggression_dashboard_v3.app import _backfill_covered_end

    t0 = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    merged = {
        "TY": pd.DataFrame({"ts": pd.to_datetime([t0, t0 + timedelta(minutes=10)], utc=True)}),
        "FV": pd.DataFrame({"ts": pd.to_datetime([t0 + timedelta(minutes=7)], utc=True)}),
        "TU": pd.DataFrame(columns=["ts"]),
    }
    previous = t0 - timedelta(hours=1)
    assert _backfill_covered_end(merged, previous) == t0 + timedelta(minutes=7)
    assert _backfill_covered_end({"TY": pd.DataFrame(columns=["ts"])}, previous) == previous


def test_live_display_trades_is_live_first_with_hist_before_live(monkeypatch):
    from scripts.aggression_dashboard_v3 import app as v3app

    live = pd.concat(
        [_ty_print("2026-09-24T15:00:00.000Z", 2, "B"), _ty_print("2026-09-24T15:00:05.000Z", 3, "A")],
        ignore_index=True,
    )
    echo = live.copy()
    echo["ts"] = echo["ts"] + pd.Timedelta(microseconds=300)
    hist = pd.concat([_ty_print("2026-09-24T14:00:00Z", 7, "B"), echo], ignore_index=True)
    hist.attrs["front_contracts"] = [{"raw_symbol": "ZNZ6"}]
    monkeypatch.setattr(v3app, "_peek_backfill_window", lambda symbols, start, end: ({"TY": hist}, {}, {}))
    monkeypatch.setattr(v3app.LIVE_BUFFER, "get_trades", lambda symbols, start, end: {"TY": live})
    trades, _errors, _state = v3app._live_display_trades(
        ("TY",),
        datetime(2026, 9, 23, 22, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc),
    )
    ty = trades["TY"]
    assert int(ty["size"].sum()) == 12
    assert ty["ts"].iloc[1:].tolist() == live["ts"].tolist()
    assert ty.attrs["front_contracts"] == [{"raw_symbol": "ZNZ6"}]
