"""Descriptive aggress versus implied-passive context for Aggression V1."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping

import numpy as np
import pandas as pd

LABEL = "DESCRIPTIVE / IMPLIED PASSIVE / NON-DIRECTIONAL / NOT RANGE-TREND VALIDATED"
WINDOW_MIN = 15
DOMINANCE_RATIO = 1.5
TICK_SIZE = 1.0 / 64.0
QUIET_FLOOR_TICKS = 2.0
QUIET_FLOOR = QUIET_FLOOR_TICKS * TICK_SIZE
Z_MIN_PERIODS = 20
DYNAMICS_LOOKBACK_MIN = 5
DYNAMICS_DEADBAND_TICKS = 1.0
DYNAMICS_DEADBAND = DYNAMICS_DEADBAND_TICKS * TICK_SIZE
EASTERN = "America/New_York"

RAW_TO_DISPLAY = {
    "AGGRESS_DOM": "AGGRESS_DOMINANT",
    "PASSIVE_DOM": "IMPLIED_PASSIVE_DOMINANT",
    "NEITHER": "BALANCED",
    "QUIET": "QUIET",
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _utc_timestamp(value: datetime | pd.Timestamp | None) -> pd.Timestamp:
    stamp = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _base_payload(status: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "state": status,
        "research_state": None,
        "label": LABEL,
        "non_directional": True,
        "predictive": False,
        "reason": reason,
        "as_of": None,
        "forming_bar_excluded": False,
        "parameters": {
            "window_min": WINDOW_MIN,
            "dominance_ratio": DOMINANCE_RATIO,
            "tick_size": TICK_SIZE,
            "quiet_floor_ticks": QUIET_FLOOR_TICKS,
            "dynamics_lookback_min": DYNAMICS_LOOKBACK_MIN,
            "dynamics_deadband_ticks": DYNAMICS_DEADBAND_TICKS,
        },
        "aggress": {"ticks": None, "trend": "WARMING", "delta_abs_ticks": None},
        "implied_passive": {"ticks": None, "trend": "WARMING", "delta_abs_ticks": None},
        "transition": {
            "shifted": False,
            "from_state": None,
            "to_state": None,
            "at": None,
            "minutes_in_state": None,
        },
        "model": {"name": None, "beta": None, "fit_ts": None},
        "observations": {"completed_bars": 0, "ready_bars": 0},
    }


def classify_dominance_state(
    aggress_15m: float,
    passive_15m: float,
    delta_price: float,
) -> str:
    """Classify one completed 15-minute window using local price contributions."""
    values = (_finite(aggress_15m), _finite(passive_15m), _finite(delta_price))
    if any(value is None for value in values):
        return "WARMING"
    contrib_a, contrib_p, d_p = (float(value) for value in values)
    abs_a, abs_p = abs(contrib_a), abs(contrib_p)
    if abs_a < QUIET_FLOOR and abs_p < QUIET_FLOOR:
        return "QUIET"
    aggress_dom = (
        np.sign(contrib_a) != 0
        and np.sign(contrib_a) == np.sign(d_p)
        and abs_a >= DOMINANCE_RATIO * abs_p
    )
    passive_dom = (
        abs_p >= DOMINANCE_RATIO * abs_a
        and (
            (np.sign(contrib_p) != 0 and np.sign(contrib_a) != 0 and np.sign(contrib_p) != np.sign(contrib_a))
            or (np.sign(contrib_p) != 0 and np.sign(contrib_p) == np.sign(d_p))
        )
    )
    if aggress_dom:
        return "AGGRESS_DOM"
    if passive_dom:
        return "PASSIVE_DOM"
    return "NEITHER"


def classify_strength_change(current_value: float, prior_value: float) -> tuple[str, float | None]:
    """Describe whether absolute 15-minute contribution rose over the lookback."""
    current = _finite(current_value)
    prior = _finite(prior_value)
    if current is None or prior is None:
        return "WARMING", None
    delta = abs(current) - abs(prior)
    if delta > DYNAMICS_DEADBAND:
        return "INCREASING", delta
    if delta < -DYNAMICS_DEADBAND:
        return "SLOWING", delta
    return "STEADY", delta


def _to_ticks(value: Any) -> float | None:
    number = _finite(value)
    if number is None:
        return None
    return number / TICK_SIZE


def _session_expanding_z(series: pd.Series) -> pd.Series:
    history = series.shift(1)
    mean = history.expanding(min_periods=Z_MIN_PERIODS).mean()
    std = history.expanding(min_periods=Z_MIN_PERIODS).std(ddof=1).replace(0.0, np.nan)
    return (series - mean) / std


def _completed_frame(
    model_df: pd.DataFrame,
    as_of: datetime | pd.Timestamp | None,
) -> tuple[pd.DataFrame, bool]:
    frame = model_df.copy()
    index = pd.DatetimeIndex(frame.index)
    frame.index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    cutoff = _utc_timestamp(as_of).floor("min")
    completed = frame.loc[frame.index < cutoff].copy()
    return completed, len(completed) < len(frame)


def _annotate_contributions(frame: pd.DataFrame, beta: float) -> pd.DataFrame:
    out = frame.copy()
    out["et_date"] = out.index.tz_convert(EASTERN).date.astype(str)
    grouped = out.groupby("et_date", sort=False)
    out["aggress_contrib_1m"] = beta * grouped["total_aggress"].diff()
    out["passive_contrib_1m"] = grouped["residual"].diff()
    out["delta_price_1m"] = grouped["price"].diff()
    for source, target in (
        ("aggress_contrib_1m", "aggress_window"),
        ("passive_contrib_1m", "passive_window"),
        ("delta_price_1m", "delta_price_window"),
    ):
        out[target] = grouped[source].transform(
            lambda series: series.rolling(WINDOW_MIN, min_periods=WINDOW_MIN).sum()
        )
    out["z_aggress"] = out.groupby("et_date", sort=False)["aggress_window"].transform(
        _session_expanding_z
    )
    out["z_passive"] = out.groupby("et_date", sort=False)["passive_window"].transform(
        _session_expanding_z
    )
    out["research_state"] = [
        classify_dominance_state(contrib_a, contrib_p, d_p)
        for contrib_a, contrib_p, d_p in zip(
            out["aggress_window"], out["passive_window"], out["delta_price_window"]
        )
    ]
    return out


def _leg_context(series: pd.Series, current_ts: pd.Timestamp) -> dict[str, Any]:
    current = series.get(current_ts)
    target = current_ts - pd.Timedelta(minutes=DYNAMICS_LOOKBACK_MIN)
    prior_values = series.loc[:target].dropna()
    prior = prior_values.iloc[-1] if not prior_values.empty else np.nan
    trend, delta = classify_strength_change(current, prior)
    return {
        "ticks": _to_ticks(current),
        "trend": trend,
        "delta_abs_ticks": _to_ticks(delta),
    }


def _transition_context(states: pd.Series) -> dict[str, Any]:
    valid = states[states.isin(RAW_TO_DISPLAY)].copy()
    if valid.empty:
        return _base_payload("WARMING")["transition"]
    current = str(valid.iloc[-1])
    start_pos = len(valid) - 1
    while start_pos > 0 and str(valid.iloc[start_pos - 1]) == current:
        start_pos -= 1
    started_at = pd.Timestamp(valid.index[start_pos])
    previous = str(valid.iloc[start_pos - 1]) if start_pos > 0 else None
    age = max(0, int((pd.Timestamp(valid.index[-1]) - started_at) / pd.Timedelta(minutes=1)))
    return {
        "shifted": previous is not None,
        "from_state": RAW_TO_DISPLAY.get(previous) if previous else None,
        "to_state": RAW_TO_DISPLAY[current],
        "at": started_at.isoformat(),
        "minutes_in_state": age,
    }


def _to_et_iso(ts: datetime | pd.Timestamp) -> str:
    """Match chart panel x: timezone-naive Eastern ISO text."""
    value = pd.Timestamp(ts)
    if value.tzinfo is None:
        value = value.tz_localize("UTC")
    return value.tz_convert(EASTERN).tz_localize(None).isoformat()


def _bar_snapshot(day: pd.DataFrame, current_ts: pd.Timestamp) -> dict[str, Any]:
    """Banner fields as of one completed bar, using only that session prefix."""
    prefix = day.loc[:current_ts]
    raw_state = str(prefix.loc[current_ts, "research_state"])
    as_of = pd.Timestamp(current_ts).isoformat()
    snapshot = {
        "x": _to_et_iso(current_ts),
        "status": "WARMING",
        "state": "WARMING",
        "reason": "Waiting for a completed 15-minute contribution window.",
        "aggress": {"ticks": None, "trend": "WARMING", "delta_abs_ticks": None},
        "implied_passive": {"ticks": None, "trend": "WARMING", "delta_abs_ticks": None},
        "transition": _base_payload("WARMING")["transition"],
        "as_of": as_of,
        "research_state": None,
    }
    if raw_state == "WARMING":
        return snapshot
    snapshot.update(
        status="READY",
        state=RAW_TO_DISPLAY[raw_state],
        research_state=raw_state,
        reason=None,
        aggress=_leg_context(prefix["aggress_window"], current_ts),
        implied_passive=_leg_context(prefix["passive_window"], current_ts),
        transition=_transition_context(prefix["research_state"]),
    )
    return snapshot


def _compact_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "x": snapshot["x"],
        "status": snapshot["status"],
        "state": snapshot["state"],
        "reason": snapshot["reason"],
        "aggress": snapshot["aggress"],
        "implied_passive": snapshot["implied_passive"],
        "transition": snapshot["transition"],
        "as_of": snapshot["as_of"],
    }


def _apply_snapshot(payload: dict[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    payload.update(
        status=snapshot["status"],
        state=snapshot["state"],
        research_state=snapshot.get("research_state"),
        reason=snapshot["reason"],
        aggress=snapshot["aggress"],
        implied_passive=snapshot["implied_passive"],
        transition=snapshot["transition"],
        as_of=snapshot["as_of"],
    )
    return payload


def _unavailable_payload(
    model_meta: Mapping[str, Any] | None,
    reason: str | None = None,
) -> dict[str, Any]:
    meta = dict(model_meta or {})
    payload = _base_payload(
        "UNAVAILABLE",
        reason or "Intraday fair-value model is unavailable.",
    )
    payload["model"] = {
        "name": meta.get("model"),
        "beta": _finite(meta.get("beta")),
        "fit_ts": meta.get("fit_ts"),
    }
    return payload


def _annotate_completed_model(
    model_df: pd.DataFrame,
    model_meta: Mapping[str, Any] | None,
    *,
    as_of: datetime | pd.Timestamp | None = None,
) -> tuple[dict[str, Any], pd.DataFrame | None]:
    """Return the latest-style payload shell plus the completed annotated frame."""
    meta = dict(model_meta or {})
    payload = _unavailable_payload(meta)
    beta = payload["model"]["beta"]
    if not meta.get("ok") or beta is None or model_df is None or model_df.empty:
        return payload, None
    required = {"total_aggress", "residual", "price"}
    missing = sorted(required - set(model_df.columns))
    if missing:
        payload["reason"] = f"Intraday model data missing: {', '.join(missing)}."
        return payload, None

    frame, excluded = _completed_frame(model_df, as_of)
    payload["forming_bar_excluded"] = excluded
    payload["observations"]["completed_bars"] = int(len(frame))
    if frame.empty:
        payload.update(status="WARMING", state="WARMING", reason="No completed one-minute bars yet.")
        return payload, None
    return payload, _annotate_contributions(frame, float(beta))


def build_flow_dominance_context_and_series(
    model_df: pd.DataFrame,
    model_meta: Mapping[str, Any] | None,
    *,
    as_of: datetime | pd.Timestamp | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Latest banner payload plus one snapshot per completed bar."""
    payload, annotated = _annotate_completed_model(model_df, model_meta, as_of=as_of)
    if annotated is None or annotated.empty:
        return payload, []

    series: list[dict[str, Any]] = []
    for _, day in annotated.groupby("et_date", sort=False):
        for ts in day.index:
            series.append(_compact_snapshot(_bar_snapshot(day, pd.Timestamp(ts))))

    latest_date = str(annotated["et_date"].iloc[-1])
    day = annotated.loc[annotated["et_date"] == latest_date]
    ready = day.loc[day["research_state"] != "WARMING"]
    payload["observations"]["ready_bars"] = int(len(ready))
    latest = _bar_snapshot(day, pd.Timestamp(day.index[-1]))
    return _apply_snapshot(payload, latest), series


def build_flow_dominance_series(
    model_df: pd.DataFrame,
    model_meta: Mapping[str, Any] | None,
    *,
    as_of: datetime | pd.Timestamp | None = None,
) -> list[dict[str, Any]]:
    """Per-bar banner snapshots using the same completed-bar rules as the live banner."""
    _, series = build_flow_dominance_context_and_series(model_df, model_meta, as_of=as_of)
    return series


def clip_flow_dominance_series(
    series: list[Mapping[str, Any]] | None,
    cutoff: datetime | pd.Timestamp | None,
) -> list[dict[str, Any]]:
    """Keep snapshots whose completed-bar timestamp is inside the chart lookback."""
    rows = [dict(row) for row in (series or [])]
    if cutoff is None or not rows:
        return rows
    cut = _utc_timestamp(cutoff)
    clipped: list[dict[str, Any]] = []
    for row in rows:
        as_of = row.get("as_of")
        if as_of is None:
            continue
        if _utc_timestamp(as_of) >= cut:
            clipped.append(row)
    return clipped


def build_flow_dominance_context(
    model_df: pd.DataFrame,
    model_meta: Mapping[str, Any] | None,
    *,
    as_of: datetime | pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Build the latest completed-bar, non-predictive dominance payload."""
    payload, _ = build_flow_dominance_context_and_series(model_df, model_meta, as_of=as_of)
    return payload
