#!/usr/bin/env python3
"""Read-only TY option skew/vol shock -> futures path event study.

Guardrail: gamma/economic-event DBs are opened mode=ro. No live dashboard
imports, edits, live-store writes, ports, SOD, Tailscale, Telegram, or UI.
Outputs stay under aggression_v2_vol_skew_shock_paths/.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_gate_lib import benjamini_hochberg, bootstrap_ci, one_sided_p  # noqa: E402
from scripts.aggression_v2_skew_leg_events_research import (  # noqa: E402
    EASTERN,
    TICK_SIZE,
    _connect_ro,
    _event_times,
    _front_weight,
    _to_utc_from_et,
    load_leg_panel,
)

GAMMA_DB = Path(r"C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v3.db")
V2_LIVE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_live"
EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_vol_skew_shock_paths"
FORMATION_MINUTES = 15
Z_THRESHOLD = 1.5
COOLDOWN_MINUTES = 30
HORIZONS: tuple[int | str, ...] = (1, 5, 15, 30, 60, 120, 240, 480, 1440, "RTH_CLOSE", "OVERNIGHT")
PRIMARY_HORIZONS = (5, 15, 30, 60, 240, 1440)
ARMS = (
    "ATM_VOL_UP", "ATM_VOL_DOWN", "PUT_IV_UP", "PUT_IV_DOWN",
    "CALL_IV_UP", "CALL_IV_DOWN", "RR50_UP", "RR50_DOWN",
    "SKEW_INDEX_UP", "SKEW_INDEX_DOWN",
)
EXPECTED_SIGN = {
    "ATM_VOL_UP": 0.0, "ATM_VOL_DOWN": 0.0,
    "PUT_IV_UP": -1.0, "PUT_IV_DOWN": 1.0,
    "CALL_IV_UP": 1.0, "CALL_IV_DOWN": -1.0,
    "RR50_UP": 1.0, "RR50_DOWN": -1.0,
    "SKEW_INDEX_UP": 1.0, "SKEW_INDEX_DOWN": -1.0,
}
N_BOOT = 10_000
BH_Q = 0.10
EPS = 1e-12


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def load_summary_metrics(db_path: Path) -> pd.DataFrame:
    with _connect_ro(db_path) as conn:
        data = pd.read_sql_query(
            """
            SELECT s.timestamp, m.expiry, m.backbone, m.skew_index, m.rr50
            FROM summary_metrics_v2 m
            JOIN snapshot_v2 s ON s.id=m.snapshot_id
            """,
            conn,
        )
    data["ts"] = _to_utc_from_et(data["timestamp"])
    data["weight"] = _front_weight(data["expiry"])
    rows = []
    for ts, block in data.dropna(subset=["ts", "weight"]).groupby("ts", sort=False):
        row: dict[str, Any] = {"ts": ts}
        for column in ("backbone", "skew_index", "rr50"):
            valid = block[[column, "weight"]].dropna()
            row[column] = float(np.average(valid[column], weights=valid["weight"])) if len(valid) else np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index("ts").sort_index().resample("1min").last().ffill(limit=2)


def load_price_logs(live_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(glob.glob(str(live_dir / "signal_log_*.csv"))):
        try:
            frames.append(pd.read_csv(path, usecols=["ts", "price", "residual_z"]))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    data = pd.concat(frames, ignore_index=True)
    data["ts"] = pd.to_datetime(data["ts"], errors="coerce", utc=True, format="mixed")
    for column in ("price", "residual_z"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data.dropna(subset=["ts", "price"]).sort_values("ts").drop_duplicates("ts", keep="last").set_index("ts")


def _causal_z(series: pd.Series) -> pd.Series:
    history = series.shift(1)
    mean = history.expanding(min_periods=30).mean()
    std = history.expanding(min_periods=30).std(ddof=1).replace(0.0, np.nan)
    return (series - mean) / std


def _event_conflict(index: pd.DatetimeIndex, event_times: np.ndarray) -> np.ndarray:
    if not len(event_times):
        return np.zeros(len(index), dtype=bool)
    values = index.tz_convert("UTC").tz_localize(None).to_numpy(dtype="datetime64[ns]")
    out = np.zeros(len(values), dtype=bool)
    for i, value in enumerate(values):
        left = value - np.timedelta64(120, "m")
        right = value + np.timedelta64(31, "m")
        pos = np.searchsorted(event_times, left)
        out[i] = pos < len(event_times) and event_times[pos] <= right
    return out


def build_panel(legs: pd.DataFrame, summary: pd.DataFrame, price: pd.DataFrame, events_db: Path) -> pd.DataFrame:
    options = legs.join(summary[["backbone", "skew_index"]], how="outer").sort_index()
    panel = price.join(options, how="left").sort_index()
    panel["et_date"] = panel.index.tz_convert(EASTERN).strftime("%Y-%m-%d")
    et = panel.index.tz_convert(EASTERN)
    minutes = et.hour * 60 + et.minute
    panel["is_rth"] = (minutes >= 8 * 60) & (minutes < 17 * 60)
    panel["event_conflict"] = _event_conflict(panel.index, _event_times(events_db))
    panel["atm_vol"] = (panel["call_iv_50"] + panel["put_iv_50"]) / 2.0
    panel["rr50_calc"] = panel["call_iv_50"] - panel["put_iv_50"]
    channels = ("atm_vol", "put_iv_50", "call_iv_50", "rr50_calc", "skew_index", "backbone")
    parts = []
    for _, day in panel.groupby("et_date", sort=False):
        d = day.copy()
        gap_minutes = d.index.to_series().diff().dt.total_seconds().div(60.0)
        d["formation_max_gap_minutes"] = gap_minutes.rolling(FORMATION_MINUTES, min_periods=FORMATION_MINUTES).max()
        d["formation_valid"] = d["formation_max_gap_minutes"] <= 2.0
        d["price_move_formation_ticks"] = d["price"].diff(FORMATION_MINUTES) / TICK_SIZE
        d["pre_rv30"] = d["price"].diff().rolling(30, min_periods=10).std()
        d["pre_range30_ticks"] = (
            d["price"].rolling(30, min_periods=10).max()
            - d["price"].rolling(30, min_periods=10).min()
        ) / TICK_SIZE
        d["pre_low30"] = d["price"].rolling(30, min_periods=10).min()
        d["pre_high30"] = d["price"].rolling(30, min_periods=10).max()
        for channel in channels:
            d[f"{channel}_chg"] = d[channel].diff(FORMATION_MINUTES)
            d[f"{channel}_z"] = _causal_z(d[f"{channel}_chg"])
        option_z_cols = [f"{channel}_z" for channel in channels]
        d["max_abs_option_z"] = d[option_z_cols].abs().max(axis=1)
        parts.append(d)
    return pd.concat(parts).sort_index()


def _split_dates(panel: pd.DataFrame) -> tuple[list[str], list[str]]:
    active = panel[panel["max_abs_option_z"] >= Z_THRESHOLD]
    dates = sorted(active["et_date"].dropna().unique().tolist())
    cut = max(1, min(len(dates) - 1, int(len(dates) * 0.7))) if len(dates) > 1 else len(dates)
    return dates[:cut], dates[cut:]


def selection_thresholds(panel: pd.DataFrame, selection_dates: list[str]) -> dict[str, Any]:
    selection = panel[panel["et_date"].isin(set(selection_dates))]
    channels = ("atm_vol", "put_iv_50", "call_iv_50", "rr50_calc", "skew_index", "backbone")
    floors = {
        channel: float(selection[f"{channel}_chg"].abs().quantile(0.60))
        for channel in channels
    }
    candidates = selection[selection["max_abs_option_z"] >= Z_THRESHOLD]
    price_abs = candidates["price_move_formation_ticks"].abs().dropna()
    return {
        "absolute_floors": floors,
        "price_move_p50": float(price_abs.quantile(0.50)),
        "price_move_p80": float(price_abs.quantile(0.80)),
    }


def _shock_candidate(row: pd.Series, floors: dict[str, float]) -> tuple[str | None, float, str | None]:
    if not bool(row.get("formation_valid", False)):
        return None, np.nan, None
    call_chg, put_chg = row.get("call_iv_50_chg"), row.get("put_iv_50_chg")
    call_z, put_z = row.get("call_iv_50_z"), row.get("put_iv_50_z")
    backbone_z, atm_z = row.get("backbone_z"), row.get("atm_vol_z")
    if any(pd.isna(value) for value in (call_chg, put_chg, call_z, put_z)):
        return None, np.nan, None
    max_leg = max(abs(float(call_chg)), abs(float(put_chg)), EPS)
    similar = np.sign(call_chg) == np.sign(put_chg) != 0 and abs(float(call_chg) - float(put_chg)) <= 0.25 * max_leg
    if similar and (abs(float(atm_z)) >= Z_THRESHOLD or abs(float(backbone_z)) >= Z_THRESHOLD):
        direction = "UP" if (call_chg + put_chg) > 0 else "DOWN"
        if abs(float(row["atm_vol_chg"])) >= floors["atm_vol"]:
            return f"ATM_VOL_{direction}", max(abs(float(atm_z)), abs(float(backbone_z))), "atm_vol"
    if abs(float(put_z)) >= Z_THRESHOLD and abs(float(put_chg)) > abs(float(call_chg)):
        if abs(float(put_chg)) >= floors["put_iv_50"]:
            return f"PUT_IV_{'UP' if put_chg > 0 else 'DOWN'}", abs(float(put_z)), "put_iv_50"
    if abs(float(call_z)) >= Z_THRESHOLD and abs(float(call_chg)) > abs(float(put_chg)):
        if abs(float(call_chg)) >= floors["call_iv_50"]:
            return f"CALL_IV_{'UP' if call_chg > 0 else 'DOWN'}", abs(float(call_z)), "call_iv_50"
    rr_z, rr_chg = row.get("rr50_calc_z"), row.get("rr50_calc_chg")
    if np.sign(call_chg) != np.sign(put_chg) and abs(float(rr_z)) >= Z_THRESHOLD:
        if abs(float(rr_chg)) >= floors["rr50_calc"]:
            return f"RR50_{'UP' if rr_chg > 0 else 'DOWN'}", abs(float(rr_z)), "rr50_calc"
    skew_z, skew_chg = row.get("skew_index_z"), row.get("skew_index_chg")
    if pd.notna(skew_z) and abs(float(skew_z)) >= Z_THRESHOLD and abs(float(skew_chg)) >= floors["skew_index"]:
        return f"SKEW_INDEX_{'UP' if skew_chg > 0 else 'DOWN'}", abs(float(skew_z)), "skew_index"
    return None, np.nan, None


def detect_events(panel: pd.DataFrame, thresholds: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = []
    for ts, row in panel.iterrows():
        arm, score, channel = _shock_candidate(row, thresholds["absolute_floors"])
        if arm:
            raw.append({"t0": ts, "arm": arm, "score": score, "channel": channel, "et_date": row["et_date"]})
    candidates = pd.DataFrame(raw)
    if candidates.empty:
        return candidates, candidates
    selected, overlaps = [], []
    for _, day in candidates.groupby("et_date", sort=False):
        cluster = []
        for _, event in day.sort_values("t0").iterrows():
            if cluster and event["t0"] - cluster[-1]["t0"] >= pd.Timedelta(minutes=COOLDOWN_MINUTES):
                winner = max(cluster, key=lambda item: item["score"])
                selected.append(winner)
                overlaps.extend([item for item in cluster if item is not winner])
                cluster = []
            cluster.append(event.to_dict())
        if cluster:
            winner = max(cluster, key=lambda item: item["score"])
            selected.append(winner)
            overlaps.extend([item for item in cluster if item is not winner])
    events = pd.DataFrame(selected).sort_values("t0")
    events["entry_ts"] = events["t0"] + pd.Timedelta(minutes=1)
    event_rows = panel.reindex(pd.DatetimeIndex(events["t0"])).reset_index(drop=True)
    events["formation_move_ticks"] = event_rows["price_move_formation_ticks"].to_numpy()
    events["is_rth"] = event_rows["is_rth"].to_numpy()
    events["residual_z"] = event_rows["residual_z"].to_numpy()
    events["pre_rv30"] = event_rows["pre_rv30"].to_numpy()
    events["pre_range30_ticks"] = event_rows["pre_range30_ticks"].to_numpy()
    events["pre_low30"] = event_rows["pre_low30"].to_numpy()
    events["pre_high30"] = event_rows["pre_high30"].to_numpy()
    events["event_conflict"] = event_rows["event_conflict"].to_numpy()
    abs_move = events["formation_move_ticks"].abs()
    events["shock_class"] = np.where(
        (abs_move <= thresholds["price_move_p50"]) & ~events["event_conflict"],
        "PRICE_QUIET_SHOCK",
        np.where(abs_move >= thresholds["price_move_p80"], "PRICE_LED_VOL", "MIXED"),
    )
    return events, pd.DataFrame(overlaps)


def _nearest_price(price: pd.Series, target: pd.Timestamp, tolerance: pd.Timedelta) -> tuple[pd.Timestamp | None, float | None]:
    if price.empty:
        return None, None
    pos = price.index.searchsorted(target)
    choices = []
    for index in (pos - 1, pos):
        if 0 <= index < len(price):
            ts = price.index[index]
            choices.append((abs(ts - target), ts, float(price.iloc[index])))
    if not choices:
        return None, None
    distance, ts, value = min(choices, key=lambda item: item[0])
    return (ts, value) if distance <= tolerance else (None, None)


def _target(entry: pd.Timestamp, horizon: int | str) -> pd.Timestamp | None:
    if isinstance(horizon, int):
        return entry + pd.Timedelta(minutes=horizon)
    entry_et = entry.tz_convert(EASTERN)
    if horizon == "RTH_CLOSE":
        close = entry_et.normalize() + pd.Timedelta(hours=17)
        return close.tz_convert("UTC") if entry_et < close else None
    if horizon == "OVERNIGHT":
        if entry_et.hour >= 16:
            return None
        next_open = entry_et.normalize() + pd.Timedelta(days=1, hours=8)
        return next_open.tz_convert("UTC")
    return None


def outcome(price: pd.Series, entry: pd.Timestamp, horizon: int | str, expected_sign: float, pre_rv: float, pre_low: float, pre_high: float) -> dict[str, Any]:
    target = _target(entry, horizon)
    if target is None:
        return {"valid": False}
    tolerance = pd.Timedelta(seconds=90) if isinstance(horizon, int) and horizon <= 240 else pd.Timedelta(minutes=5)
    entry_ts, entry_price = _nearest_price(price, entry, pd.Timedelta(seconds=90))
    end_ts, end_price = _nearest_price(price, target, tolerance)
    if entry_ts is None or end_ts is None or end_ts <= entry_ts:
        return {"valid": False}
    path = price.loc[(price.index >= entry_ts) & (price.index <= end_ts)].dropna()
    if len(path) < 2:
        return {"valid": False}
    diffs = path.diff().dropna()
    net_ticks = (end_price - entry_price) / TICK_SIZE
    abs_ticks = abs(net_ticks)
    range_ticks = (path.max() - path.min()) / TICK_SIZE
    rv = float(diffs.std(ddof=1)) if len(diffs) >= 2 else np.nan
    efficiency = abs(end_price - entry_price) / max(float(diffs.abs().sum()), EPS)
    signed = expected_sign * net_ticks if expected_sign else np.nan
    directional_path = expected_sign * (path - entry_price) / TICK_SIZE if expected_sign else pd.Series(dtype=float)
    expected_bars = (end_ts - entry_ts).total_seconds() / 60.0 + 1.0
    return {
        "valid": True,
        "entry_ts": entry_ts,
        "end_ts": end_ts,
        "target_ts": target,
        "observed_bars": int(len(path)),
        "coverage_ratio": float(len(path) / max(expected_bars, 1.0)),
        "net_ticks": net_ticks,
        "signed_ticks": signed,
        "after_cost_0_5": signed - 0.5 if np.isfinite(signed) else np.nan,
        "abs_ticks": abs_ticks,
        "range_ticks": range_ticks,
        "realized_vol": rv,
        "rv_ratio": rv / pre_rv if np.isfinite(pre_rv) and pre_rv > EPS and np.isfinite(rv) else np.nan,
        "path_efficiency": efficiency,
        "trend": float(efficiency >= 0.45),
        "chop": float(efficiency <= 0.25),
        "stay_in_pre_range": float(path.min() >= pre_low and path.max() <= pre_high) if np.isfinite(pre_low) and np.isfinite(pre_high) else np.nan,
        "adverse_ticks": float(directional_path.min()) if len(directional_path) else np.nan,
        "move_ge_4": float(abs_ticks >= 4.0),
    }


def expected_sign(arm: str) -> float:
    mapping = {
        "PUT_IV_UP": -1.0, "PUT_IV_DOWN": 1.0,
        "CALL_IV_UP": 1.0, "CALL_IV_DOWN": -1.0,
        "RR50_UP": 1.0, "RR50_DOWN": -1.0,
        "SKEW_INDEX_UP": 1.0, "SKEW_INDEX_DOWN": -1.0,
    }
    return mapping.get(arm, 0.0)


def attach_outcomes(events: pd.DataFrame, price: pd.Series) -> pd.DataFrame:
    rows = []
    for _, event in events.iterrows():
        for horizon in HORIZONS:
            result = outcome(
                price, pd.Timestamp(event["entry_ts"]), horizon, expected_sign(str(event["arm"])),
                float(event.get("pre_rv30", np.nan)), float(event.get("pre_low30", np.nan)), float(event.get("pre_high30", np.nan)),
            )
            rows.append({**event.to_dict(), "horizon": str(horizon), **result})
    return pd.DataFrame(rows)


def match_controls(
    events: pd.DataFrame,
    panel: pd.DataFrame,
    selection_dates: list[str],
    holdout_dates: list[str],
) -> pd.DataFrame:
    shock_by_date = {
        date: pd.DatetimeIndex(block["t0"])
        for date, block in events.groupby("et_date", sort=False)
    }
    quiet = panel[panel["max_abs_option_z"] < 1.0].copy()
    quiet_et = quiet.index.tz_convert(EASTERN)
    quiet["minute_of_day"] = quiet_et.hour * 60 + quiet_et.minute
    rows = []
    for _, event in events.iterrows():
        t0 = pd.Timestamp(event["t0"])
        event_et = t0.tz_convert(EASTERN)
        event_minute = event_et.hour * 60 + event_et.minute
        allowed_dates = set(holdout_dates if event["et_date"] in set(holdout_dates) else selection_dates)
        candidates = quiet[
            quiet["et_date"].isin(allowed_dates)
            & (quiet["et_date"] != event["et_date"])
            & (quiet["is_rth"] == bool(event["is_rth"]))
            & (quiet["event_conflict"] == bool(event["event_conflict"]))
            & quiet["formation_valid"].fillna(False)
        ].copy()
        if candidates.empty:
            continue
        candidates = candidates[(candidates["minute_of_day"] - event_minute).abs() <= 15]
        if candidates.empty:
            continue
        prior_quiet = []
        for ts, candidate in candidates.iterrows():
            prior_shocks = shock_by_date.get(str(candidate["et_date"]), pd.DatetimeIndex([]))
            prior_shocks = prior_shocks[(prior_shocks < ts) & (prior_shocks >= ts - pd.Timedelta(minutes=60))]
            prior_quiet.append(len(prior_shocks) == 0)
        candidates = candidates[prior_quiet].copy()
        if candidates.empty:
            continue
        candidates["score"] = (
            (candidates["minute_of_day"] - event_minute).abs()
            + (pd.to_numeric(candidates["residual_z"], errors="coerce").abs() - abs(float(event.get("residual_z", 0.0)))).abs().fillna(0.0)
            + 100.0 * (pd.to_numeric(candidates["pre_rv30"], errors="coerce") - float(event.get("pre_rv30", 0.0))).abs().fillna(0.0)
        )
        for rank, (control_ts, control) in enumerate(candidates.nsmallest(5, "score").iterrows(), start=1):
            rows.append({
                "event_id": f"{event['arm']}|{pd.Timestamp(event['t0']).isoformat()}",
                "event_t0": event["t0"],
                "arm": event["arm"],
                "et_date": event["et_date"],
                "control_rank": rank,
                "control_t0": control_ts,
                "control_entry_ts": control_ts + pd.Timedelta(minutes=1),
                "pre_rv30": control.get("pre_rv30"),
                "pre_low30": control.get("pre_low30"),
                "pre_high30": control.get("pre_high30"),
            })
    return pd.DataFrame(rows)


def attach_control_outcomes(controls: pd.DataFrame, price: pd.Series) -> pd.DataFrame:
    rows = []
    if controls.empty:
        return pd.DataFrame(columns=["event_id", "horizon", "valid"])
    for _, control in controls.iterrows():
        for horizon in HORIZONS:
            result = outcome(
                price, pd.Timestamp(control["control_entry_ts"]), horizon, expected_sign(str(control["arm"])),
                float(control.get("pre_rv30", np.nan)), float(control.get("pre_low30", np.nan)), float(control.get("pre_high30", np.nan)),
            )
            rows.append({**control.to_dict(), "horizon": str(horizon), **result})
    return pd.DataFrame(rows)


def _delta_stats(values: pd.DataFrame, metric: str, pool: str, arm: str, horizon: str) -> dict[str, Any]:
    data = values[["et_date", metric]].dropna()
    if data.empty:
        return {"pool": pool, "arm": arm, "horizon": horizon, "metric": metric, "n": 0}
    ci = bootstrap_ci(data[metric], n_boot=10_000, groups=data["et_date"])
    daily = data.groupby("et_date")[metric].mean()
    return {
        "pool": pool, "arm": arm, "horizon": horizon, "metric": metric,
        "n": int(len(data)), "cluster_n": int(data["et_date"].nunique()),
        "mean_delta": float(data[metric].mean()), "ci_low": ci["ci_low"], "ci_high": ci["ci_high"],
        "one_sided_lower": ci["one_sided_lower"], "one_sided_p": one_sided_p(daily),
    }


def evaluate(events: pd.DataFrame, event_outcomes: pd.DataFrame, control_outcomes: pd.DataFrame, holdout_dates: list[str]) -> pd.DataFrame:
    primary_ids = {
        f"{row.arm}|{pd.Timestamp(row.t0).isoformat()}"
        for row in events.itertuples()
        if row.shock_class == "PRICE_QUIET_SHOCK" and row.et_date in set(holdout_dates)
    }
    event_data = event_outcomes.copy()
    event_data["event_id"] = event_data.apply(lambda row: f"{row['arm']}|{pd.Timestamp(row['t0']).isoformat()}", axis=1)
    event_data = event_data[event_data["event_id"].isin(primary_ids) & event_data["valid"]].copy()
    if control_outcomes.empty or "event_id" not in control_outcomes:
        controls = pd.DataFrame(columns=["event_id", "horizon"])
    else:
        controls = control_outcomes[control_outcomes["event_id"].isin(primary_ids) & control_outcomes["valid"]].copy()
    control_metrics = ["signed_ticks", "abs_ticks", "range_ticks", "rv_ratio", "trend", "chop", "stay_in_pre_range"]
    control_means = (
        controls.groupby(["event_id", "horizon"])[control_metrics].mean().add_prefix("control_").reset_index()
        if not controls.empty
        else pd.DataFrame(columns=["event_id", "horizon", *[f"control_{metric}" for metric in control_metrics]])
    )
    merged = event_data.merge(control_means, on=["event_id", "horizon"], how="left")
    for metric in control_metrics:
        merged[f"delta_{metric}"] = pd.to_numeric(merged[metric], errors="coerce") - pd.to_numeric(merged[f"control_{metric}"], errors="coerce")
    rows = []
    pool_metrics = {
        "direction": ("delta_signed_ticks",),
        "magnitude": ("delta_abs_ticks", "delta_range_ticks", "delta_rv_ratio"),
        "regime": ("delta_trend", "delta_chop", "delta_stay_in_pre_range"),
    }
    for pool, metrics in pool_metrics.items():
        for arm in ARMS:
            for horizon in map(str, PRIMARY_HORIZONS):
                subset = merged[(merged["arm"] == arm) & (merged["horizon"] == horizon)]
                for metric in metrics:
                    rows.append(_delta_stats(subset, metric, pool, arm, horizon))
    stats = pd.DataFrame(rows)
    stats["bh_adjusted_p"] = np.nan
    stats["bh_significant"] = False
    for pool, indices in stats.groupby("pool").groups.items():
        positions = list(indices)
        bh = benjamini_hochberg(stats.loc[positions, "one_sided_p"], q=BH_Q)
        stats.loc[positions, "bh_adjusted_p"] = bh["adjusted_p"]
        stats.loc[positions, "bh_significant"] = bh["reject"]
    stats["pass"] = (
        (stats["n"] >= 30)
        & (stats["cluster_n"] >= 8)
        & (stats["ci_low"] > 0.0)
        & stats["bh_significant"]
    )
    return stats


def diagnostic_stats(
    events: pd.DataFrame,
    event_outcomes: pd.DataFrame,
    control_outcomes: pd.DataFrame,
) -> pd.DataFrame:
    event_data = event_outcomes[event_outcomes["valid"]].copy()
    event_data["event_id"] = event_data.apply(
        lambda row: f"{row['arm']}|{pd.Timestamp(row['t0']).isoformat()}",
        axis=1,
    )
    controls = control_outcomes[control_outcomes["valid"]].copy()
    metrics = ["signed_ticks", "abs_ticks", "range_ticks", "rv_ratio", "trend", "chop", "stay_in_pre_range"]
    control_means = controls.groupby(["event_id", "horizon"])[metrics].mean().add_prefix("control_").reset_index()
    merged = event_data.merge(control_means, on=["event_id", "horizon"], how="left")
    for metric in metrics:
        merged[f"delta_{metric}"] = pd.to_numeric(merged[metric], errors="coerce") - pd.to_numeric(
            merged[f"control_{metric}"], errors="coerce"
        )
    groups: list[tuple[str, pd.Series]] = [("ALL_SHOCKS", pd.Series(True, index=merged.index))]
    groups.extend(
        (f"CLASS:{shock_class}", merged["shock_class"] == shock_class)
        for shock_class in sorted(merged["shock_class"].dropna().unique())
    )
    groups.extend(
        (f"ARM:{arm}", merged["arm"] == arm)
        for arm in sorted(merged["arm"].dropna().unique())
    )
    rows = []
    for group_name, mask in groups:
        block = merged[mask]
        for horizon in sorted(block["horizon"].dropna().astype(str).unique()):
            horizon_block = block[block["horizon"].astype(str) == horizon]
            for metric in metrics:
                values = pd.to_numeric(horizon_block[f"delta_{metric}"], errors="coerce").dropna()
                if values.empty:
                    continue
                rows.append(
                    {
                        "group": group_name,
                        "horizon": horizon,
                        "metric": metric,
                        "n": int(len(values)),
                        "cluster_n": int(horizon_block.loc[values.index, "et_date"].nunique()),
                        "event_mean": float(pd.to_numeric(horizon_block.loc[values.index, metric], errors="coerce").mean()),
                        "control_mean": float(pd.to_numeric(horizon_block.loc[values.index, f"control_{metric}"], errors="coerce").mean()),
                        "mean_delta": float(values.mean()),
                    }
                )
    return pd.DataFrame(rows)


def write_heatmaps(stats: pd.DataFrame, output_dir: Path) -> str | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    metrics = ("delta_signed_ticks", "delta_abs_ticks", "delta_path_efficiency") if "delta_path_efficiency" in stats["metric"].values else ("delta_signed_ticks", "delta_abs_ticks", "delta_chop")
    fig, axes = plt.subplots(1, len(metrics), figsize=(16, 5))
    for ax, metric in zip(axes, metrics):
        data = stats[stats["metric"] == metric].pivot(index="arm", columns="horizon", values="mean_delta").reindex(index=ARMS)
        image = ax.imshow(data.to_numpy(), aspect="auto", cmap="RdBu_r")
        ax.set_xticks(range(len(data.columns)), data.columns, rotation=45)
        ax.set_yticks(range(len(data.index)), data.index, fontsize=7)
        ax.set_title(metric)
        fig.colorbar(image, ax=ax)
    fig.tight_layout()
    path = output_dir / "shock_path_heatmaps.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    legs, _ = load_leg_panel(Path(args.gamma_db))
    summary = load_summary_metrics(Path(args.gamma_db))
    price_frame = load_price_logs(Path(args.v2_live))
    panel = build_panel(legs, summary, price_frame, Path(args.events_db))
    panel_cache = output_dir / "shock_minute_panel.csv"
    panel.reset_index(names="ts").to_csv(panel_cache, index=False)
    selection_dates, holdout_dates = _split_dates(panel)
    thresholds = selection_thresholds(panel, selection_dates)
    events, overlaps = detect_events(panel, thresholds)
    outcomes = attach_outcomes(events, price_frame["price"])
    controls = match_controls(events, panel, selection_dates, holdout_dates)
    control_outcomes = attach_control_outcomes(controls, price_frame["price"])
    stats = evaluate(events, outcomes, control_outcomes, holdout_dates)
    price_led_ids = set(events.loc[events["shock_class"] == "PRICE_LED_VOL", "t0"])
    price_led = outcomes[outcomes["t0"].isin(price_led_ids)]
    robustness = diagnostic_stats(events, outcomes, control_outcomes)

    paths = {
        "panel": panel_cache,
        "events": output_dir / "shock_events.csv",
        "outcomes": output_dir / "shock_outcomes.csv",
        "controls": output_dir / "matched_controls.csv",
        "stats": output_dir / "primary_holdout_stats.csv",
        "price_led": output_dir / "price_led_diagnostics.csv",
        "robustness": output_dir / "robustness_stats.csv",
        "summary": output_dir / "shock_paths_summary.json",
    }
    events.to_csv(paths["events"], index=False)
    outcomes.to_csv(paths["outcomes"], index=False)
    control_outcomes.to_csv(paths["controls"], index=False)
    stats.to_csv(paths["stats"], index=False)
    price_led.to_csv(paths["price_led"], index=False)
    robustness.to_csv(paths["robustness"], index=False)
    heatmap = write_heatmaps(stats, output_dir)
    pass_rows = stats[stats["pass"]].to_dict(orient="records")
    result = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "panel_rows": int(len(panel)),
        "panel_start": panel.index.min(),
        "panel_end": panel.index.max(),
        "selection_dates": selection_dates,
        "holdout_dates": holdout_dates,
        "thresholds": thresholds,
        "event_counts": events.groupby(["shock_class", "arm"]).size().to_dict() if not events.empty else {},
        "valid_outcomes": outcomes.groupby("horizon")["valid"].sum().to_dict() if not outcomes.empty else {},
        "control_rows": int(len(control_outcomes)),
        "pass_rows": pass_rows,
        "diagnostic_highlights": robustness.sort_values("mean_delta", ascending=False).head(20).to_dict(orient="records"),
        "class_diagnostics": robustness[
            robustness["group"].isin(["CLASS:PRICE_QUIET_SHOCK", "CLASS:PRICE_LED_VOL"])
            & robustness["metric"].isin(["abs_ticks", "range_ticks", "rv_ratio", "trend", "chop"])
        ].to_dict(orient="records"),
        "headline": (
            f"{len(pass_rows)} holdout shock/path cells pass coverage, clustered CI, and FDR."
            if pass_rows
            else "No shock/path cell passes holdout coverage, date-clustered CI, and FDR."
        ),
        "artifacts": {key: str(path) for key, path in paths.items()} | {"heatmap": heatmap},
        "isolation": {"gamma_db_mode": "ro", "events_db_mode": "ro", "modified_live_apps": False, "wrote_live_stores": False},
    }
    # JSON cannot encode tuple dict keys.
    result["event_counts"] = {f"{key[0]}:{key[1]}": int(value) for key, value in result["event_counts"].items()}
    paths["summary"].write_text(json.dumps(result, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps({"ok": True, "headline": result["headline"], "events": result["event_counts"], "valid_outcomes": result["valid_outcomes"], "passes": len(pass_rows), "artifacts": result["artifacts"]}, indent=2, default=_json_default))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="TY skew/vol shock to futures path study.")
    parser.add_argument("--gamma-db", default=str(GAMMA_DB))
    parser.add_argument("--v2-live", default=str(V2_LIVE))
    parser.add_argument("--events-db", default=str(EVENTS_DB))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
