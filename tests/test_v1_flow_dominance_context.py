"""Focused tests for the descriptive Aggression V1 flow-dominance banner."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from scripts.aggression_dashboard.flow_dominance_context import (
    DYNAMICS_DEADBAND,
    DOMINANCE_RATIO,
    LABEL,
    QUIET_FLOOR_TICKS,
    TICK_SIZE,
    WINDOW_MIN,
    build_flow_dominance_context,
    build_flow_dominance_series,
    classify_dominance_state,
    classify_strength_change,
    clip_flow_dominance_series,
)
from scripts.aggression_dashboard.panels import _to_et_iso


def _model_frame() -> pd.DataFrame:
    rng = np.random.default_rng(17)
    n = 110
    aggress_1m = rng.normal(0.0, 0.4, n)
    passive_1m = rng.normal(0.0, 0.4, n)
    aggress_1m[55:76] = 0.05
    passive_1m[55:76] = 2.5
    aggress_1m[86:] = 2.5
    passive_1m[86:] = 0.05
    total_aggress = np.cumsum(aggress_1m)
    residual = np.cumsum(passive_1m)
    index = pd.date_range("2026-08-17 13:00:00", periods=n, freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "total_aggress": total_aggress,
            "residual": residual,
            "price": 110.0 + total_aggress + residual,
        },
        index=index,
    )


def _meta() -> dict[str, object]:
    return {
        "ok": True,
        "model": "intraday OLS fit once per session",
        "beta": 1.0,
        "fit_ts": "2026-08-17T13:30:00+00:00",
    }


def test_fixed_descriptive_contract_and_all_ready_states() -> None:
    assert WINDOW_MIN == 15
    assert DOMINANCE_RATIO == 1.5
    assert QUIET_FLOOR_TICKS == 2.0
    assert TICK_SIZE == 1.0 / 64.0
    assert "NON-DIRECTIONAL" in LABEL
    assert "NOT RANGE-TREND VALIDATED" in LABEL

    tick = TICK_SIZE
    assert classify_dominance_state(8 * tick, 1 * tick, 8 * tick) == "AGGRESS_DOM"
    assert classify_dominance_state(1 * tick, 8 * tick, 8 * tick) == "PASSIVE_DOM"
    assert classify_dominance_state(4 * tick, 4 * tick, 4 * tick) == "NEITHER"
    assert classify_dominance_state(1 * tick, 1 * tick, 0.0) == "QUIET"


def test_strength_change_uses_documented_deadband() -> None:
    assert classify_strength_change(4 * TICK_SIZE, 2 * TICK_SIZE)[0] == "INCREASING"
    assert classify_strength_change(2 * TICK_SIZE, 4 * TICK_SIZE)[0] == "SLOWING"
    assert classify_strength_change(4 * TICK_SIZE, 4 * TICK_SIZE + DYNAMICS_DEADBAND / 2)[0] == "STEADY"
    assert classify_strength_change(np.nan, 1.0) == ("WARMING", None)


def test_unavailable_and_warming_fail_closed() -> None:
    unavailable = build_flow_dominance_context(pd.DataFrame(), {}, as_of="2026-08-17T14:00:00Z")
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["non_directional"] is True
    assert unavailable["predictive"] is False

    short = _model_frame().iloc[:10]
    as_of = short.index[-1] + pd.Timedelta(minutes=1)
    warming = build_flow_dominance_context(short, _meta(), as_of=as_of)
    assert warming["status"] == "WARMING"
    assert warming["state"] == "WARMING"
    assert warming["observations"]["ready_bars"] == 0


def test_ready_payload_reports_transition_and_leg_dynamics() -> None:
    frame = _model_frame()
    as_of = frame.index[-1] + pd.Timedelta(minutes=1)
    payload = build_flow_dominance_context(frame, _meta(), as_of=as_of)

    assert payload["status"] == "READY"
    assert payload["state"] == "AGGRESS_DOMINANT"
    assert payload["transition"]["shifted"] is True
    assert payload["transition"]["from_state"] in {
        "IMPLIED_PASSIVE_DOMINANT",
        "BALANCED",
        "QUIET",
    }
    assert payload["aggress"]["trend"] in {"INCREASING", "SLOWING", "STEADY"}
    assert payload["implied_passive"]["trend"] in {"INCREASING", "SLOWING", "STEADY"}
    assert payload["aggress"]["ticks"] is not None
    assert payload["implied_passive"]["ticks"] is not None
    assert payload["as_of"] == frame.index[-1].isoformat()
    json.dumps(payload, allow_nan=False)


def test_quiet_on_sub_two_tick_contributions() -> None:
    assert classify_dominance_state(1.9 * TICK_SIZE, 1.9 * TICK_SIZE, 0.0) == "QUIET"
    assert classify_dominance_state(2.0 * TICK_SIZE, 0.0, 2.0 * TICK_SIZE) == "AGGRESS_DOM"


def test_persistent_aggress_stays_dominant() -> None:
    n = 90
    aggress_1m = np.full(n, 4.0 * TICK_SIZE)
    residual_1m = np.full(n, 0.1 * TICK_SIZE)
    index = pd.date_range("2026-08-17 13:00:00", periods=n, freq="1min", tz="UTC")
    frame = pd.DataFrame(
        {
            "total_aggress": np.cumsum(aggress_1m),
            "residual": np.cumsum(residual_1m),
            "price": 110.0 + np.cumsum(aggress_1m) + np.cumsum(residual_1m),
        },
        index=index,
    )
    as_of = frame.index[-1] + pd.Timedelta(minutes=1)
    payload = build_flow_dominance_context(frame, _meta(), as_of=as_of)
    series = build_flow_dominance_series(frame, _meta(), as_of=as_of)
    ready = [row for row in series if row["status"] == "READY"]
    assert payload["state"] == "AGGRESS_DOMINANT"
    assert ready
    assert all(row["state"] == "AGGRESS_DOMINANT" for row in ready[-30:])


def test_forming_bar_is_excluded_from_state() -> None:
    frame = _model_frame()
    as_of = frame.index[-1] + pd.Timedelta(minutes=1)
    reference = build_flow_dominance_context(frame, _meta(), as_of=as_of)
    forming = pd.DataFrame(
        {
            "total_aggress": [frame["total_aggress"].iloc[-1]],
            "residual": [frame["residual"].iloc[-1] + 1000.0],
            "price": [frame["price"].iloc[-1] + 1000.0],
        },
        index=pd.DatetimeIndex([as_of]),
    )
    with_forming = build_flow_dominance_context(
        pd.concat([frame, forming]),
        _meta(),
        as_of=as_of,
    )
    assert with_forming["forming_bar_excluded"] is True
    assert with_forming["state"] == reference["state"]
    assert with_forming["as_of"] == reference["as_of"]


def test_series_last_point_matches_latest_banner() -> None:
    frame = _model_frame()
    as_of = frame.index[-1] + pd.Timedelta(minutes=1)
    latest = build_flow_dominance_context(frame, _meta(), as_of=as_of)
    series = build_flow_dominance_series(frame, _meta(), as_of=as_of)

    assert series
    last = series[-1]
    assert last["x"] == _to_et_iso(frame.index[-1])
    assert last["status"] == latest["status"]
    assert last["state"] == latest["state"]
    assert last["reason"] == latest["reason"]
    assert last["as_of"] == latest["as_of"]
    assert last["aggress"] == latest["aggress"]
    assert last["implied_passive"] == latest["implied_passive"]
    assert last["transition"] == latest["transition"]


def test_series_excludes_forming_bar() -> None:
    frame = _model_frame()
    as_of = frame.index[-1] + pd.Timedelta(minutes=1)
    forming = pd.DataFrame(
        {
            "total_aggress": [frame["total_aggress"].iloc[-1]],
            "residual": [frame["residual"].iloc[-1] + 1000.0],
            "price": [frame["price"].iloc[-1] + 1000.0],
        },
        index=pd.DatetimeIndex([as_of]),
    )
    series = build_flow_dominance_series(pd.concat([frame, forming]), _meta(), as_of=as_of)
    forming_as_of = pd.Timestamp(as_of).isoformat()
    assert all(row["as_of"] != forming_as_of for row in series)
    assert series[-1]["as_of"] == frame.index[-1].isoformat()


def test_lookback_clip_keeps_full_history_z() -> None:
    frame = _model_frame()
    as_of = frame.index[-1] + pd.Timedelta(minutes=1)
    series = build_flow_dominance_series(frame, _meta(), as_of=as_of)
    ready = [row for row in series if row["status"] == "READY"]
    assert ready
    early = ready[0]
    reconstructed = build_flow_dominance_context(
        frame,
        _meta(),
        as_of=pd.Timestamp(early["as_of"]) + pd.Timedelta(minutes=1),
    )
    cutoff = pd.Timestamp(early["as_of"]) - pd.Timedelta(minutes=10)
    clipped = clip_flow_dominance_series(series, cutoff)
    match = next(row for row in clipped if row["as_of"] == early["as_of"])
    assert match["state"] == reconstructed["state"]
    assert match["aggress"] == reconstructed["aggress"]
    assert match["implied_passive"] == reconstructed["implied_passive"]
    assert match["transition"] == reconstructed["transition"]
    assert clip_flow_dominance_series(series, pd.Timestamp(series[-1]["as_of"]) + pd.Timedelta(minutes=1)) == []


def test_aggression_api_exposes_top_level_flow_dominance(monkeypatch) -> None:
    from scripts.aggression_dashboard import app as app_module

    model = _model_frame()
    as_of = model.index[-1] + pd.Timedelta(minutes=1)
    flow = model[["total_aggress"]].copy()
    price = model[["price"]].copy()
    meta = {
        "request": {"end": as_of.isoformat(), "mode": "live"},
        "live_status": {"connected": True},
        "fair_value_models": {"intraday": {"data": model, "meta": _meta()}},
    }
    monkeypatch.setattr(
        app_module,
        "build_live_aggression_dataset",
        lambda request: (flow, price, meta),
    )
    monkeypatch.setattr(
        app_module,
        "_apply_display_lookback",
        lambda request, flow_df, price_df, response_meta: (
            flow_df,
            price_df,
            response_meta,
        ),
    )
    monkeypatch.setattr(
        app_module,
        "build_dashboard_payload",
        lambda flow_df, price_df, response_meta: {
            "success": True,
            "panels": {},
            "meta": {},
        },
    )

    response = app_module.app.test_client().post(
        "/api/aggression",
        json={
            "mode": "live",
            "start_iso": "2026-08-17T13:00:00+00:00",
            "end_iso": as_of.isoformat(),
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["flow_dominance"]["status"] == "READY"
    assert payload["flow_dominance"]["state"] == "AGGRESS_DOMINANT"
    assert payload["flow_dominance"]["aggress"]["ticks"] is not None
    assert "z" not in payload["flow_dominance"]["aggress"]
    assert isinstance(payload["flow_dominance_series"], list)
    assert payload["flow_dominance_series"]
    assert payload["flow_dominance_series"][-1]["state"] == payload["flow_dominance"]["state"]
    assert payload["flow_dominance_series"][-1]["as_of"] == payload["flow_dominance"]["as_of"]
    page = app_module.app.test_client().get("/")
    assert page.status_code == 200
    assert b'id="flow-dominance-banner"' in page.data
    assert b'id="banner-on-hover"' in page.data
    assert b'id="flow-dominance-hover"' in page.data
    assert LABEL.encode("utf-8") in page.data

    meta["live_status"] = {"connected": False}
    disconnected = app_module._latest_flow_dominance(meta)
    assert disconnected["status"] == "UNAVAILABLE"
    assert "disconnected" in disconnected["reason"].lower()
