"""Flow-effort exhaustion/breakout regime helpers for offline research.

This module used to live under ``scripts.aggression_dashboard_v3``. The live
V3 page is now the trade-to-trade six-state monitor; research scripts keep
the same public names from this path.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ECONOMIC_EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
EASTERN = ZoneInfo("America/New_York")
TICK_SIZE = 1.0 / 64.0
EPS = 1e-9
CURVE_SYMBOLS = ("FV", "TU", "US")


@dataclass(frozen=True)
class FlowEffortConfig:
    bucket: str = "1min"
    effort_window: int = 5
    range_window: int = 30
    range_max_ticks: float = 4.0
    post_break_window: int = 10
    gap_window: int = 10
    high_effort_ratio: float = 1.5
    effort_ratio_breakout: float = 0.6
    false_break_reentry_minutes: int = 15
    min_warmup_minutes: int = 30
    regime_dwell_buckets: int = 3
    event_blackout_minutes: int = 10
    stale_feed_seconds: float = 30.0
    cross_tenor_vote_threshold: int = 2
    min_range_effort: float = 1.0


@dataclass
class RegimeTracker:
    """Apply simple dwell-time hysteresis to live regime labels."""

    confirmed_regime: str = "WARMUP"
    confirmed_since: pd.Timestamp | None = None
    candidate_regime: str | None = None
    candidate_count: int = 0

    def update(self, raw_regime: str, ts: pd.Timestamp, dwell_buckets: int) -> tuple[str, float]:
        if raw_regime == self.confirmed_regime:
            self.candidate_regime = None
            self.candidate_count = 0
        elif raw_regime == self.candidate_regime:
            self.candidate_count += 1
        else:
            self.candidate_regime = raw_regime
            self.candidate_count = 1

        if raw_regime != self.confirmed_regime and self.candidate_count >= max(1, dwell_buckets):
            self.confirmed_regime = raw_regime
            self.confirmed_since = ts
            self.candidate_regime = None
            self.candidate_count = 0
        elif self.confirmed_since is None:
            self.confirmed_since = ts

        dwell = 0.0
        if self.confirmed_since is not None:
            dwell = max(0.0, (ts - self.confirmed_since).total_seconds() / 60.0)
        return self.confirmed_regime, dwell


def _finite(value: Any) -> Any:
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        if not np.isfinite(float(value)):
            return None
        return float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _safe_div(num: pd.Series, denom: pd.Series | float) -> pd.Series:
    return num / pd.Series(denom, index=num.index).replace(0.0, np.nan)


def _session_expanding_median(series: pd.Series, min_periods: int = 10) -> pd.Series:
    return series.groupby(series.index.tz_convert(EASTERN).date).transform(
        lambda x: x.expanding(min_periods=min_periods).median()
    )


def _session_expanding_quantile(series: pd.Series, q: float, min_periods: int = 10) -> pd.Series:
    return series.groupby(series.index.tz_convert(EASTERN).date).transform(
        lambda x: x.expanding(min_periods=min_periods).quantile(q)
    )


def _session_z(series: pd.Series, min_periods: int = 10) -> pd.Series:
    def _zscore(day: pd.Series) -> pd.Series:
        mean = day.expanding(min_periods=min_periods).mean()
        std = day.expanding(min_periods=min_periods).std(ddof=1).replace(0.0, np.nan)
        return (day - mean) / std

    return series.groupby(series.index.tz_convert(EASTERN).date).transform(_zscore)


def _as_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    out.index = idx.tz_convert("UTC")
    return out.sort_index()


def annotate_flow_effort(
    features: pd.DataFrame,
    model_df: pd.DataFrame,
    config: FlowEffortConfig | None = None,
) -> pd.DataFrame:
    """Return feature/model rows annotated with flow-effort regime ingredients."""

    config = config or FlowEffortConfig()
    if features.empty or model_df.empty:
        return pd.DataFrame()

    feat = _as_utc_index(features)
    model = _as_utc_index(model_df)
    model_cols = [col for col in ("fair_value", "residual", "residual_z") if col in model.columns]
    data = feat.join(model[model_cols], how="inner")
    data = data.dropna(subset=["price", "agg_cum_daily"]).copy()
    if data.empty:
        return data

    agg_1m = data["agg_1m"] if "agg_1m" in data else data["agg_cum_daily"].diff().fillna(0.0)
    data["delta_aggress"] = data["agg_cum_daily"].diff().fillna(0.0)
    data["delta_price"] = data["price"].diff().fillna(0.0)
    data["delta_price_ticks"] = data["delta_price"] / TICK_SIZE

    roll_flow = agg_1m.rolling(config.effort_window, min_periods=1).sum().abs()
    roll_ticks = data["delta_price_ticks"].rolling(config.effort_window, min_periods=1).sum().abs()
    data["effort_per_tick"] = roll_flow / roll_ticks.clip(lower=1.0)

    aggress_step = data["agg_cum_daily"].diff(config.gap_window).fillna(0.0)
    price_step_ticks = data["price"].diff(config.gap_window).fillna(0.0) / TICK_SIZE
    aggress_scale = _session_expanding_median(aggress_step.abs(), min_periods=5).clip(lower=EPS)
    price_scale = _session_expanding_median(price_step_ticks.abs(), min_periods=5).clip(lower=EPS)
    data["flow_price_gap"] = (aggress_step / aggress_scale) - (price_step_ticks / price_scale)
    data["gap_widening"] = data["flow_price_gap"].diff(5).fillna(0.0) > 0.0

    data["agg_rate"] = agg_1m
    data["agg_accel"] = agg_1m.diff().fillna(0.0)
    data["flow_decelerating"] = (
        (data["agg_rate"].abs() > EPS)
        & (data["agg_accel"].abs() > EPS)
        & (np.sign(data["agg_rate"]) != np.sign(data["agg_accel"]))
    )

    data["abs_agg_5m_z"] = _session_z(data.get("agg_5m", roll_flow)).abs()
    data["abs_move_5m_ticks"] = data["price"].diff(5).abs() / TICK_SIZE
    move_median = _session_expanding_median(data["abs_move_5m_ticks"], min_periods=10)
    data["absorption"] = (data["abs_agg_5m_z"] >= 1.0) & (
        data["abs_move_5m_ticks"] <= move_median.fillna(data["abs_move_5m_ticks"])
    )

    ty_sign = np.sign(data.get("agg_5m", roll_flow)).fillna(0.0)
    curve_vote = pd.Series(0.0, index=data.index)
    for symbol in CURVE_SYMBOLS:
        curve_vote = curve_vote + np.sign(data.get(f"{symbol}_agg_5m", pd.Series(0.0, index=data.index))).fillna(0.0)
    data["cross_tenor_aligned"] = (
        (ty_sign != 0)
        & (np.sign(curve_vote) == ty_sign)
        & (curve_vote.abs() >= config.cross_tenor_vote_threshold)
    )
    data["cross_tenor_disagrees"] = (ty_sign != 0) & (curve_vote != 0) & (np.sign(curve_vote) != ty_sign)

    session_effort_median = _session_expanding_median(data["effort_per_tick"], min_periods=10)
    data["high_effort"] = data["effort_per_tick"] >= (session_effort_median * config.high_effort_ratio)
    data["absorption"] = data["absorption"] | (data["high_effort"] & (data["abs_move_5m_ticks"] <= 1.0))
    data["range_volume_high"] = data.get("vol_per_min", pd.Series(0.0, index=data.index)) >= _session_expanding_median(
        data.get("vol_per_min", pd.Series(0.0, index=data.index)), min_periods=10
    )
    data["flow_price_aligned"] = (
        (np.sign(data["delta_aggress"]) == np.sign(data["delta_price"]))
        & (data["delta_aggress"].abs() > EPS)
        & (data["delta_price_ticks"].abs() >= 0.25)
    )
    spread = data.get("spread_mean", pd.Series(np.nan, index=data.index))
    data["wide_spread"] = spread >= _session_expanding_quantile(spread, 0.67, min_periods=10)

    return _apply_range_state_machine(data, config)


def _apply_range_state_machine(data: pd.DataFrame, config: FlowEffortConfig) -> pd.DataFrame:
    out = data.copy()
    defaults: dict[str, Any] = {
        "in_range": False,
        "range_high": np.nan,
        "range_low": np.nan,
        "range_ticks": np.nan,
        "frozen_range_high": np.nan,
        "frozen_range_low": np.nan,
        "range_effort": np.nan,
        "post_break_effort": np.nan,
        "effort_ratio": np.nan,
        "range_break": False,
        "false_break": False,
        "break_direction": 0,
        "post_break_active": False,
    }
    for col, default in defaults.items():
        out[col] = default

    for _, session in out.groupby(out.index.tz_convert(EASTERN).date, sort=False):
        mode = "search"
        frozen_high = np.nan
        frozen_low = np.nan
        frozen_effort = np.nan
        break_direction = 0
        break_i = -1
        break_ts: pd.Timestamp | None = None
        false_break_until = -1

        for loc, (ts, row) in enumerate(session.iterrows()):
            idx = ts
            start = max(0, loc - config.range_window + 1)
            history = session.iloc[start : loc + 1]
            range_high = float(history["price"].max())
            range_low = float(history["price"].min())
            range_ticks = (range_high - range_low) / TICK_SIZE
            in_range_now = len(history) >= 5 and range_ticks <= config.range_max_ticks

            if mode == "break":
                out.at[idx, "post_break_active"] = True
                if loc <= false_break_until:
                    out.at[idx, "false_break"] = True
                out.at[idx, "frozen_range_high"] = frozen_high
                out.at[idx, "frozen_range_low"] = frozen_low
                out.at[idx, "range_effort"] = frozen_effort
                out.at[idx, "break_direction"] = break_direction
                post = session.iloc[break_i : loc + 1]
                post_effort = float(post["effort_per_tick"].median()) if not post.empty else np.nan
                out.at[idx, "post_break_effort"] = post_effort
                out.at[idx, "effort_ratio"] = post_effort / frozen_effort if frozen_effort > 0 else np.nan
                price = float(row["price"])
                elapsed = (ts - break_ts).total_seconds() / 60.0 if break_ts is not None else 0.0
                reentered = frozen_low <= price <= frozen_high
                too_much_effort = pd.notna(out.at[idx, "effort_ratio"]) and float(out.at[idx, "effort_ratio"]) >= 0.8
                if reentered and elapsed <= config.false_break_reentry_minutes:
                    out.at[idx, "false_break"] = True
                    false_break_until = loc + config.regime_dwell_buckets
                    mode = "search"
                elif (loc - break_i + 1) >= config.post_break_window:
                    if too_much_effort:
                        out.at[idx, "false_break"] = True
                        false_break_until = loc + config.regime_dwell_buckets
                    mode = "search"
                continue

            if loc <= false_break_until:
                out.at[idx, "false_break"] = True

            out.at[idx, "in_range"] = bool(in_range_now)
            out.at[idx, "range_high"] = range_high
            out.at[idx, "range_low"] = range_low
            out.at[idx, "range_ticks"] = range_ticks
            if in_range_now:
                effort_window = history.loc[history["effort_per_tick"].notna(), "effort_per_tick"]
                range_effort = float(effort_window.median()) if not effort_window.empty else np.nan
                out.at[idx, "range_effort"] = range_effort
                if loc + 1 < len(session):
                    continue

            prev = session.iloc[loc - 1] if loc > 0 else None
            if prev is None:
                continue
            prev_high = out.at[prev.name, "range_high"]
            prev_low = out.at[prev.name, "range_low"]
            prev_effort = out.at[prev.name, "range_effort"]
            if pd.isna(prev_high) or pd.isna(prev_low) or pd.isna(prev_effort):
                continue
            price = float(row["price"])
            broke_up = price > float(prev_high)
            broke_down = price < float(prev_low)
            if broke_up or broke_down:
                frozen_high = float(prev_high)
                frozen_low = float(prev_low)
                frozen_effort = max(float(prev_effort), config.min_range_effort)
                break_direction = 1 if broke_up else -1
                break_i = loc
                break_ts = ts
                mode = "break"
                out.at[idx, "range_break"] = True
                out.at[idx, "post_break_active"] = True
                out.at[idx, "frozen_range_high"] = frozen_high
                out.at[idx, "frozen_range_low"] = frozen_low
                out.at[idx, "range_effort"] = frozen_effort
                out.at[idx, "post_break_effort"] = float(row["effort_per_tick"])
                out.at[idx, "effort_ratio"] = float(row["effort_per_tick"]) / frozen_effort if frozen_effort > 0 else np.nan
                out.at[idx, "break_direction"] = break_direction

    return out


def _tier_down(tier: str) -> str:
    order = ["NONE", "C", "B", "A", "A+"]
    if tier not in order:
        return tier
    return order[max(0, order.index(tier) - 1)]


def _exhaustion_tier(row: pd.Series) -> str:
    if not (bool(row.get("in_range")) and bool(row.get("high_effort")) and bool(row.get("gap_widening"))):
        return "NONE"
    absorption = bool(row.get("absorption"))
    decel = bool(row.get("flow_decelerating"))
    residual_z = row.get("residual_z")
    abs_z = abs(float(residual_z)) if pd.notna(residual_z) else 0.0
    if absorption and decel and abs_z >= 1.5:
        if bool(row.get("cross_tenor_disagrees")) and bool(row.get("range_volume_high")):
            return "A+"
        return "A"
    if absorption or decel:
        return "B"
    return "C"


def _breakout_tier(row: pd.Series, config: FlowEffortConfig) -> str:
    if not bool(row.get("range_break")) and not bool(row.get("post_break_active")):
        return "NONE"
    effort_ratio = row.get("effort_ratio")
    if pd.isna(effort_ratio) or float(effort_ratio) >= config.effort_ratio_breakout:
        return "NONE"
    if bool(row.get("cross_tenor_aligned")) and not bool(row.get("wide_spread")):
        return "A"
    if bool(row.get("cross_tenor_aligned")):
        return "B"
    return "C"


def _conviction_parts(row: pd.Series, exhaustion_tier: str, breakout_tier: str) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []

    def add(name: str, value: float, reason: str) -> None:
        if value:
            parts.append({"name": name, "value": float(value), "reason": reason})

    tier_scores = {"C": 25.0, "B": 45.0, "A": 65.0, "A+": 80.0}
    if exhaustion_tier != "NONE":
        add(f"exhaustion tier {exhaustion_tier}", tier_scores.get(exhaustion_tier, 0.0), "range effort and gap divergence")
    if breakout_tier != "NONE":
        add(f"breakout tier {breakout_tier}", {"C": 25.0, "B": 45.0, "A": 70.0}.get(breakout_tier, 0.0), "range break with lower effort")
    add("absorption", 8.0 if bool(row.get("absorption")) else 0.0, "high flow with muted price response")
    add("flow deceleration", 5.0 if bool(row.get("flow_decelerating")) else 0.0, "aggressive flow losing momentum")
    residual_z = row.get("residual_z")
    add("residual z", 7.0 if pd.notna(residual_z) and abs(float(residual_z)) >= 1.5 else 0.0, "fair-value stretch confirms context")
    add("cross-tenor disagreement", 5.0 if bool(row.get("cross_tenor_disagrees")) and exhaustion_tier != "NONE" else 0.0, "rest of curve does not confirm")
    add("wide spread", -8.0 if bool(row.get("wide_spread")) else 0.0, "liquidity caution")
    add(
        "trend-follow suppression",
        -15.0 if bool(row.get("flow_price_aligned")) and exhaustion_tier != "NONE" else 0.0,
        "price and flow are moving together",
    )
    return parts


def _conviction(parts: list[dict[str, Any]]) -> float:
    return float(np.clip(sum(float(part["value"]) for part in parts), 0.0, 100.0))


@lru_cache(maxsize=16)
def _event_times_for_date(et_date: str) -> tuple[pd.Timestamp, ...]:
    if not ECONOMIC_EVENTS_DB.exists():
        return tuple()
    try:
        with sqlite3.connect(ECONOMIC_EVENTS_DB) as conn:
            rows = conn.execute(
                """
                SELECT event_date
                FROM economic_events
                WHERE substr(event_date, 1, 10) = ?
                  AND (country LIKE '%United States%' OR country = 'US')
                """,
                (et_date,),
            ).fetchall()
    except sqlite3.Error:
        return tuple()
    out: list[pd.Timestamp] = []
    for (raw,) in rows:
        try:
            ts = pd.Timestamp(str(raw))
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.tz_localize(EASTERN)
        out.append(ts.tz_convert(EASTERN))
    return tuple(out)


def _event_blackout(ts: pd.Timestamp, minutes: int) -> bool:
    et_ts = ts.tz_convert(EASTERN)
    for event_ts in _event_times_for_date(et_ts.date().isoformat()):
        if abs((et_ts - event_ts).total_seconds()) <= minutes * 60:
            return True
    return False


def _regime_message(regime: str, tier: str, row: pd.Series) -> str:
    effort = row.get("effort_per_tick")
    ratio = row.get("effort_ratio")
    effort_text = "n/a" if pd.isna(effort) else f"{float(effort):,.0f}"
    ratio_text = "n/a" if pd.isna(ratio) else f"{float(ratio):.2f}"
    if regime == "EXHAUSTION":
        return f"Exhaustion {tier}: high effort with a widening flow/price gap (5m effort/tick {effort_text})."
    if regime == "BREAKOUT":
        return f"Breakout {tier}: price left the frozen range with lower effort (effort ratio {ratio_text})."
    if regime == "FALSE_BREAK":
        return "False break: price left the range but re-entered or required too much effort."
    if regime == "TREND_FOLLOW":
        return "Trend follow: flow, price, and cross-tenor pressure are aligned."
    if regime == "WARMUP":
        return "Warmup: waiting for enough same-session rows before labeling regimes."
    if regime == "UNKNOWN":
        return "Unknown: live feed is stale or regime inputs are unavailable."
    return "Quiet: no active exhaustion or breakout regime detected."


def evaluate_regime(
    features: pd.DataFrame,
    model_df: pd.DataFrame,
    config: FlowEffortConfig | None = None,
    *,
    feed_status: dict[str, Any] | None = None,
    tracker: RegimeTracker | None = None,
) -> dict[str, Any]:
    """Return the latest display-only flow-effort regime payload."""

    config = config or FlowEffortConfig()
    if feed_status and not feed_status.get("connected", True):
        return {"ok": False, "regime": "UNKNOWN", "reason": feed_status.get("error") or "feed disconnected"}
    if feed_status and feed_status.get("last_tick_age_sec") is not None:
        try:
            if float(feed_status["last_tick_age_sec"]) > config.stale_feed_seconds:
                return {"ok": False, "regime": "UNKNOWN", "reason": "feed stale"}
        except (TypeError, ValueError):
            pass

    annotated = annotate_flow_effort(features, model_df, config)
    if annotated.empty:
        return {"ok": False, "regime": "UNKNOWN", "reason": "insufficient flow-effort rows"}

    row = annotated.iloc[-1]
    ts = pd.Timestamp(row.name)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    session_rows = annotated[annotated.index.tz_convert(EASTERN).date == ts.tz_convert(EASTERN).date()]
    if len(session_rows) < config.min_warmup_minutes:
        regime = "WARMUP"
        exhaustion_tier = "NONE"
        breakout_tier = "NONE"
    elif bool(row.get("false_break")):
        regime = "FALSE_BREAK"
        exhaustion_tier = "NONE"
        breakout_tier = "NONE"
    else:
        exhaustion_tier = _exhaustion_tier(row)
        breakout_tier = _breakout_tier(row, config)
        if bool(row.get("flow_price_aligned")) and bool(row.get("cross_tenor_aligned")):
            if exhaustion_tier in {"C", "B"}:
                exhaustion_tier = "NONE"
            trend_follow = True
        else:
            trend_follow = False
        blackout = _event_blackout(ts, config.event_blackout_minutes)
        if blackout:
            exhaustion_tier = _tier_down(exhaustion_tier)
            breakout_tier = _tier_down(breakout_tier)
        if breakout_tier != "NONE":
            regime = "BREAKOUT"
        elif exhaustion_tier != "NONE":
            regime = "EXHAUSTION"
        elif trend_follow:
            regime = "TREND_FOLLOW"
        else:
            regime = "QUIET"

    confirmed_regime = regime
    dwell_minutes = 0.0
    if tracker is not None:
        confirmed_regime, dwell_minutes = tracker.update(regime, ts, config.regime_dwell_buckets)
    parts = _conviction_parts(row, exhaustion_tier, breakout_tier)
    latest = {
        key: _finite(row.get(key))
        for key in (
            "price",
            "agg_cum_daily",
            "effort_per_tick",
            "range_effort",
            "post_break_effort",
            "effort_ratio",
            "flow_price_gap",
            "range_high",
            "range_low",
            "frozen_range_high",
            "frozen_range_low",
            "range_ticks",
            "residual_z",
            "break_direction",
        )
    }
    flags = {
        key: bool(row.get(key))
        for key in (
            "in_range",
            "high_effort",
            "gap_widening",
            "absorption",
            "flow_decelerating",
            "cross_tenor_aligned",
            "cross_tenor_disagrees",
            "range_break",
            "false_break",
            "range_volume_high",
            "flow_price_aligned",
            "wide_spread",
        )
    }
    flags["event_blackout"] = _event_blackout(ts, config.event_blackout_minutes)
    series = annotated.tail(180).copy()
    series["x"] = pd.DatetimeIndex(series.index).tz_convert(EASTERN).tz_localize(None).astype(str)
    cols = [
        "x",
        "price",
        "agg_cum_daily",
        "effort_per_tick",
        "flow_price_gap",
        "range_high",
        "range_low",
        "frozen_range_high",
        "frozen_range_low",
        "range_break",
        "false_break",
    ]
    return {
        "ok": True,
        "regime": confirmed_regime,
        "raw_regime": regime,
        "primary_tier": breakout_tier if regime == "BREAKOUT" else exhaustion_tier if regime == "EXHAUSTION" else "NONE",
        "exhaustion_tier": exhaustion_tier,
        "breakout_tier": breakout_tier,
        "conviction": _conviction(parts),
        "conviction_parts": parts,
        "dwell_minutes": dwell_minutes,
        "message": _regime_message(confirmed_regime, breakout_tier if confirmed_regime == "BREAKOUT" else exhaustion_tier, row),
        "latest": latest,
        "flags": flags,
        "series": [
            {key: _finite(value) for key, value in rec.items()}
            for rec in series[cols].replace({np.nan: None}).to_dict(orient="records")
        ],
        "notes": [
            "Research-only display labels; not validated for automated trading.",
            "Moved off the live V3 package; V1/V2 apps are not modified.",
        ],
    }
