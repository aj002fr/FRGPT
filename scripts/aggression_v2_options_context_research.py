#!/usr/bin/env python3
"""Read-only V2 x gamma-tracker options context research.

This script does not import or modify ``scripts/aggression_dashboard_v2``.
It reads V2 live logs, opens the gamma tracker DB with SQLite ``mode=ro``,
and writes research artifacts under ``workspace/analysis/aggression_v2_options_context``.
"""

from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_v3_options_context_research import DEFAULT_GAMMA_DB  # noqa: E402


EASTERN = ZoneInfo("America/New_York")
TICK_SIZE = 1.0 / 64.0
V2_LIVE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_live"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_options_context"
GAMMA_CACHE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v3_options_context" / "options_context_joined.csv"
HORIZONS = (15, 30, 60)
MIN_EPISODE_N = 30
MIN_MINUTE_N = 100
RR_COLS = ("rr25", "rr50", "rr75")


def _connect_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _expiry_ordinal(value: Any) -> float:
    raw = str(value or "").lower()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return float(digits) if digits else np.nan


def _read_csv_glob(pattern: str) -> pd.DataFrame:
    frames = []
    for path in sorted(glob.glob(pattern)):
        try:
            frames.append(pd.read_csv(path))
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _as_utc(series: pd.Series) -> pd.Series:
    ts = pd.to_datetime(series, errors="coerce", utc=True, format="mixed")
    return ts


def load_gamma_metrics_fast(db_path: Path, cache_path: Path | None = None, refresh_gamma_db: bool = False) -> pd.DataFrame:
    """Load RR/skew/vol metrics from gamma DB quickly, plus cached near-spot gamma if present."""
    if cache_path is not None and cache_path.exists() and not refresh_gamma_db:
        cached = load_cached_options_metrics(cache_path)
        if not cached.empty:
            return cached
    with _connect_ro(db_path) as conn:
        summary = pd.read_sql_query(
            """
            SELECT s.timestamp, m.expiry, m.backbone, m.skew_index, m.curve_index,
                   m.rr25, m.rr50, m.rr75, m.rr100,
                   m.backbone_roc_5m, m.backbone_roc_15m, m.backbone_roc_30m,
                   m.backbone_pct
            FROM summary_metrics_v2 m
            JOIN snapshot_v2 s ON s.id = m.snapshot_id
            """,
            conn,
        )
        futures = pd.read_sql_query(
            "SELECT ts AS timestamp, futures_px, atm_vol_1, atm_vol_2 FROM futures_vol_log",
            conn,
        )
    if summary.empty:
        return pd.DataFrame()
    summary["ts"] = _as_utc(summary["timestamp"])
    summary["ordinal"] = summary["expiry"].map(_expiry_ordinal)
    summary = summary.dropna(subset=["ts", "ordinal"]).copy()
    summary["front_weight"] = 1.0 / (summary["ordinal"] - 1.0).clip(lower=1.0)
    metric_cols = ["backbone", "skew_index", "curve_index", *RR_COLS, "rr100", "backbone_roc_5m", "backbone_roc_15m", "backbone_roc_30m", "backbone_pct"]
    for col in metric_cols:
        summary[col] = pd.to_numeric(summary[col], errors="coerce")

    def weighted(group: pd.DataFrame) -> pd.Series:
        out: dict[str, float] = {}
        for col in metric_cols:
            valid = group.dropna(subset=[col, "front_weight"])
            out[col] = float(np.average(valid[col], weights=valid["front_weight"])) if not valid.empty else np.nan
        for col in RR_COLS:
            out[f"{col}_available"] = int(group[col].notna().sum())
        out["expiry_count"] = int(group["expiry"].nunique())
        return pd.Series(out)

    agg = summary.groupby("ts", sort=True).apply(weighted).reset_index().set_index("ts").sort_index()
    if not futures.empty:
        futures["ts"] = pd.to_datetime(pd.to_numeric(futures["timestamp"], errors="coerce"), unit="s", utc=True)
        futures = futures.drop(columns=["timestamp"]).groupby("ts").last()
        agg = agg.join(futures, how="left")
    agg = agg.resample("1min").last().ffill(limit=2)
    for col in RR_COLS:
        agg[f"{col}_chg_5m"] = agg[col].diff(5)
        agg[f"{col}_chg_15m"] = agg[col].diff(15)
        agg[f"{col}_chg_30m"] = agg[col].diff(30)
    agg["skew_index_chg_30m"] = agg["skew_index"].diff(30)

    cache = load_cached_near_gamma(cache_path) if cache_path else pd.DataFrame()
    if not cache.empty:
        agg = agg.join(cache, how="left")
    for col in ("gamma_near_spot", "put_gamma_near_spot", "call_gamma_near_spot", "gamma_near_spot_z", "gamma_pin_zone"):
        if col not in agg:
            agg[col] = np.nan if col != "gamma_pin_zone" else False
    return agg


def load_cached_options_metrics(path: Path) -> pd.DataFrame:
    """Load prior read-only gamma research cache instead of re-querying SQLite."""
    try:
        header = pd.read_csv(path, nrows=0)
    except Exception:
        return pd.DataFrame()
    opt_cols = [col for col in header.columns if col.startswith("opt_")]
    if not opt_cols:
        return pd.DataFrame()
    usecols = [header.columns[0], *opt_cols]
    try:
        df = pd.read_csv(path, usecols=usecols, low_memory=False)
    except Exception:
        return pd.DataFrame()
    time_col = df.columns[0]
    df["ts"] = pd.to_datetime(df[time_col], utc=True, errors="coerce", format="mixed")
    df = df.dropna(subset=["ts"]).set_index("ts").sort_index()
    df = df.rename(columns={col: col.replace("opt_", "", 1) for col in opt_cols})
    return df[[col.replace("opt_", "", 1) for col in opt_cols]].resample("1min").last()


def load_cached_near_gamma(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    cols = [
        "opt_gamma_near_spot",
        "opt_put_gamma_near_spot",
        "opt_call_gamma_near_spot",
        "opt_gamma_near_spot_z",
        "opt_gamma_pin_zone",
    ]
    try:
        cache = pd.read_csv(path, usecols=lambda c: c == "" or c in cols)
    except Exception:
        return pd.DataFrame()
    if cache.empty:
        return pd.DataFrame()
    time_col = cache.columns[0]
    cache["ts"] = pd.to_datetime(cache[time_col], utc=True, errors="coerce", format="mixed")
    cache = cache.dropna(subset=["ts"]).set_index("ts").sort_index()
    rename = {col: col.replace("opt_", "") for col in cols if col in cache}
    return cache.rename(columns=rename)[list(rename.values())].resample("1min").last()


def _session_z(series: pd.Series, index: pd.DatetimeIndex, min_periods: int = 30) -> pd.Series:
    if series.empty:
        return series
    return series.groupby(index.tz_convert(EASTERN).date).transform(
        lambda x: (x - x.expanding(min_periods=min_periods).mean())
        / x.expanding(min_periods=min_periods).std(ddof=1).replace(0.0, np.nan)
    )


def _signed_stats(
    df: pd.DataFrame,
    mask: pd.Series,
    direction: pd.Series,
    move_col: str,
    *,
    test: str,
    track: str,
    baseline: str,
    horizon: int,
) -> dict[str, Any]:
    subset = df[mask.fillna(False)].dropna(subset=[move_col]).copy()
    if subset.empty:
        return {"track": track, "test": test, "baseline": baseline, "horizon": horizon, "n": 0}
    d = direction.reindex(subset.index).replace(0.0, np.nan)
    signed = d * subset[move_col]
    non_tie = signed.dropna()
    non_tie = non_tie[non_tie != 0.0]
    ties = signed.fillna(0.0) == 0.0
    hit_ex_ties = float((non_tie > 0).mean()) if len(non_tie) else np.nan
    hit_ties_half = float(((signed > 0).sum() + 0.5 * ties.sum()) / len(signed)) if len(signed) else np.nan
    return {
        "track": track,
        "test": test,
        "baseline": baseline,
        "horizon": int(horizon),
        "n": int(len(subset)),
        "n_non_tie": int(len(non_tie)),
        "tie_count": int(ties.sum()),
        "hit_ex_ties": hit_ex_ties,
        "hit_ties_half": hit_ties_half,
        "avg_signed_move": float(signed.mean()) if signed.notna().any() else np.nan,
        "median_signed_move": float(signed.median()) if signed.notna().any() else np.nan,
        "avg_abs_move": float(subset[move_col].abs().mean()),
    }


def _episode_stats(df: pd.DataFrame, mask: pd.Series, *, test: str, track: str, baseline: str) -> dict[str, Any]:
    subset = df[mask.fillna(False)].copy()
    if subset.empty:
        return {"track": track, "test": test, "baseline": baseline, "horizon": 30, "n": 0}
    pnl = pd.to_numeric(subset.get("dir_pnl_30m"), errors="coerce")
    non_tie = pnl.dropna()
    non_tie = non_tie[non_tie != 0.0]
    ties = pnl.fillna(0.0) == 0.0
    return {
        "track": track,
        "test": test,
        "baseline": baseline,
        "horizon": 30,
        "n": int(len(subset)),
        "watch_n": int((subset["state"] == "WATCH").sum()) if "state" in subset else 0,
        "dislocation_n": int((subset["state"] == "DISLOCATION").sum()) if "state" in subset else 0,
        "resolution_rate": float((subset["reversion_status"] == "resolved").mean()),
        "expired_rate": float((subset["reversion_status"] == "expired").mean()),
        "hit_ex_ties": float((non_tie > 0).mean()) if len(non_tie) else np.nan,
        "hit_ties_half": float(((pnl > 0).sum() + 0.5 * ties.sum()) / len(pnl)) if len(pnl) else np.nan,
        "avg_dir_pnl_30m": float(pnl.mean()) if pnl.notna().any() else np.nan,
        "median_dir_pnl_30m": float(pnl.median()) if pnl.notna().any() else np.nan,
        "avg_adverse_30m": float(pd.to_numeric(subset.get("adverse_30m"), errors="coerce").mean()),
        "median_mae_to_resolve_ticks": float(pd.to_numeric(subset.get("mae_to_resolve_ticks"), errors="coerce").median()),
        "median_minutes_to_close": float(pd.to_numeric(subset.get("minutes_to_close"), errors="coerce").median()),
        "median_price_share": float(pd.to_numeric(subset.get("price_share_of_reversion"), errors="coerce").median()),
    }


def load_v2_alerts(v2_dir: Path) -> pd.DataFrame:
    path = v2_dir / "alert_log.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["opened_ts"] = _as_utc(df["opened_ts"])
    df = df.dropna(subset=["opened_ts"]).sort_values("opened_ts")
    for col in [
        "open_price",
        "open_fair_value",
        "open_residual_z",
        "open_residual",
        "direction_sign",
        "dir_pnl_15m",
        "dir_pnl_30m",
        "adverse_15m",
        "adverse_30m",
        "mae_to_resolve_ticks",
        "price_share_of_reversion",
    ]:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_v2_signal_logs(v2_dir: Path) -> pd.DataFrame:
    df = _read_csv_glob(str(v2_dir / "signal_log_*.csv"))
    if df.empty:
        return pd.DataFrame()
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce", format="mixed")
    df = df.dropna(subset=["ts"]).set_index("ts").sort_index()
    for col in ["price", "fair_value", "residual", "residual_z"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.resample("1min").last().dropna(subset=["price", "fair_value"])
    for horizon in HORIZONS:
        df[f"future_change_{horizon}m"] = df.groupby(df.index.tz_convert(EASTERN).date)["price"].shift(-horizon) - df["price"]
        df[f"fair_value_chg_{horizon}m"] = df["fair_value"] - df.groupby(df.index.tz_convert(EASTERN).date)["fair_value"].shift(horizon)
        df[f"rr_placeholder_{horizon}m"] = np.nan
    return df


def build_minute_frame(args: argparse.Namespace) -> pd.DataFrame:
    gamma = load_gamma_metrics_fast(
        Path(args.gamma_db),
        Path(args.gamma_cache) if args.gamma_cache else None,
        refresh_gamma_db=bool(args.refresh_gamma_db),
    )
    signals = load_v2_signal_logs(Path(args.v2_dir))
    if gamma.empty or signals.empty:
        return pd.DataFrame()
    joined = pd.concat([signals.add_prefix("v2_"), gamma.add_prefix("opt_")], axis=1).sort_index()
    joined = joined[joined["v2_price"].notna()].copy()
    for horizon in HORIZONS:
        joined[f"future_change_{horizon}m"] = joined[f"v2_future_change_{horizon}m"]
        joined[f"fair_value_chg_{horizon}m"] = joined[f"v2_fair_value_chg_{horizon}m"]
    for col in ("rr25", "rr50", "rr75"):
        if f"opt_{col}" in joined:
            joined[f"opt_{col}_z"] = _session_z(joined[f"opt_{col}"], joined.index)
            for horizon in (15, 30):
                joined[f"opt_{col}_chg_{horizon}m_z"] = _session_z(joined[f"opt_{col}_chg_{horizon}m"], joined.index)
    joined["opt_backbone_roc_30m_z"] = _session_z(joined.get("opt_backbone_roc_30m", pd.Series(np.nan, index=joined.index)), joined.index)
    joined["opt_skew_index_z"] = _session_z(joined.get("opt_skew_index", pd.Series(np.nan, index=joined.index)), joined.index)
    return joined


def calibrate_rr_sign(frame: pd.DataFrame) -> dict[str, Any]:
    rr = pd.to_numeric(frame.get("opt_rr50_chg_30m"), errors="coerce")
    contemporaneous = frame["v2_price"] - frame.groupby(frame.index.tz_convert(EASTERN).date)["v2_price"].shift(30)
    valid = pd.concat([rr, contemporaneous], axis=1).dropna()
    corr = float(valid.iloc[:, 0].corr(valid.iloc[:, 1])) if len(valid) > 5 else np.nan
    rr_bullish_sign = 1 if pd.isna(corr) or corr >= 0 else -1
    return {
        "corr_rr50_chg_30m_vs_contemp_price_chg_30m": corr,
        "rr_bullish_sign": rr_bullish_sign,
        "put_bid_rr_sign": -rr_bullish_sign,
        "call_bid_rr_sign": rr_bullish_sign,
        "note": "If corr is positive, rising RR behaves bullish in-sample; put-bid is the opposite sign.",
    }


def attach_gamma_to_alerts(alerts: pd.DataFrame, gamma: pd.DataFrame, sign_meta: dict[str, Any]) -> pd.DataFrame:
    if alerts.empty or gamma.empty:
        return alerts.copy()
    left = alerts.sort_values("opened_ts").copy()
    right = gamma.reset_index(names="gamma_ts").sort_values("gamma_ts")
    merged = pd.merge_asof(
        left,
        right,
        left_on="opened_ts",
        right_on="gamma_ts",
        direction="nearest",
        tolerance=pd.Timedelta(minutes=2),
    )
    put_bid_sign = float(sign_meta["put_bid_rr_sign"])
    rr = pd.to_numeric(merged.get("rr50_chg_30m"), errors="coerce")
    merged["skew_put_bid"] = np.sign(rr) == put_bid_sign
    merged["skew_call_bid"] = np.sign(rr) == -put_bid_sign
    merged["skew_flat"] = rr.fillna(0.0).abs() < rr.abs().median(skipna=True) * 0.25
    merged["skew_agrees_fade"] = np.sign(rr) * float(sign_meta["rr_bullish_sign"]) == np.sign(merged["direction_sign"])
    merged["skew_against_fade"] = np.sign(rr) * float(sign_meta["rr_bullish_sign"]) == -np.sign(merged["direction_sign"])
    merged["backbone_up"] = pd.to_numeric(merged.get("backbone_roc_30m"), errors="coerce") > 0
    merged["backbone_down"] = pd.to_numeric(merged.get("backbone_roc_30m"), errors="coerce") < 0
    merged["gamma_pin"] = merged.get("gamma_pin_zone", pd.Series(False, index=merged.index)).fillna(False).astype(bool)
    pc_ratio = pd.to_numeric(merged.get("put_gamma_near_spot"), errors="coerce") / pd.to_numeric(
        merged.get("call_gamma_near_spot"), errors="coerce"
    ).replace(0.0, np.nan)
    merged["put_gamma_heavy"] = pc_ratio >= pc_ratio.median(skipna=True)
    merged["skew_index_extreme"] = pd.to_numeric(merged.get("skew_index"), errors="coerce").abs() >= pd.to_numeric(
        merged.get("skew_index"), errors="coerce"
    ).abs().quantile(0.8)
    return merged


def run_track_a(episodes: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    base = pd.Series(True, index=episodes.index)
    masks = {
        "A0_all_episodes": base,
        "A0_watch_only": episodes["state"].eq("WATCH"),
        "A0_dislocation_only": episodes["state"].eq("DISLOCATION"),
        "A1_gamma_pin": episodes["gamma_pin"],
        "A1_no_gamma_pin": ~episodes["gamma_pin"],
        "A2_skew_agrees_fade": episodes["skew_agrees_fade"],
        "A2_skew_against_fade": episodes["skew_against_fade"],
        "A2_skew_flat": episodes["skew_flat"],
        "A3_backbone_up": episodes["backbone_up"],
        "A3_backbone_down": episodes["backbone_down"],
        "A4_skew_index_extreme": episodes["skew_index_extreme"],
        "A5_put_gamma_heavy": episodes["put_gamma_heavy"],
        "A5_call_gamma_heavy": ~episodes["put_gamma_heavy"],
    }
    for name, mask in masks.items():
        rows.append(_episode_stats(episodes, mask, test=name, track="A_episode_open", baseline="A0_all_episodes"))
        for state in ("WATCH", "DISLOCATION"):
            rows.append(
                _episode_stats(
                    episodes,
                    mask & episodes["state"].eq(state),
                    test=f"{name}_{state.lower()}",
                    track="A_episode_open_by_state",
                    baseline=f"A0_{state.lower()}_only",
                )
            )
    return pd.DataFrame(rows)


def _option_dir(frame: pd.DataFrame, sign_meta: dict[str, Any], col: str = "opt_rr50_chg_30m") -> pd.Series:
    return np.sign(pd.to_numeric(frame.get(col), errors="coerce")) * float(sign_meta["rr_bullish_sign"])


def run_track_b(frame: pd.DataFrame, sign_meta: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    residual = pd.to_numeric(frame["v2_residual"], errors="coerce")
    z = pd.to_numeric(frame["v2_residual_z"], errors="coerce")
    fade_dir = -np.sign(residual)
    opt_dir = _option_dir(frame, sign_meta)
    for zthr in (1.5, 2.0):
        zmask = z.abs() >= zthr
        for horizon in (15, 30):
            move_col = f"future_change_{horizon}m"
            rows.append(_signed_stats(frame, zmask, fade_dir, move_col, test=f"B1_z{zthr}_baseline", track="B_minute_fade", baseline=f"z{zthr}", horizon=horizon))
            rows.append(_signed_stats(frame, zmask & (opt_dir == fade_dir), fade_dir, move_col, test=f"B1_z{zthr}_skew_with_fade", track="B_minute_fade", baseline=f"B1_z{zthr}_baseline", horizon=horizon))
            rows.append(_signed_stats(frame, zmask & (opt_dir == -fade_dir), fade_dir, move_col, test=f"B1_z{zthr}_skew_against_fade", track="B_minute_fade", baseline=f"B1_z{zthr}_baseline", horizon=horizon))
    # B2 crossings: one row per threshold crossing, time until |z| < 1.
    crossings = []
    for _, day in frame.groupby(frame.index.tz_convert(EASTERN).date, sort=False):
        active = False
        for ts, row in day.iterrows():
            abs_z = abs(float(row["v2_residual_z"])) if pd.notna(row["v2_residual_z"]) else np.nan
            if not active and pd.notna(abs_z) and abs_z >= 1.5:
                later = day.loc[ts:]
                resolved = later[later["v2_residual_z"].abs() < 1.0]
                minutes = np.nan if resolved.empty else (resolved.index[0] - ts).total_seconds() / 60.0
                crossings.append({"ts": ts, "minutes_to_revert": minutes, "gamma_pin": bool(row.get("opt_gamma_pin_zone", False))})
                active = True
            elif active and pd.notna(abs_z) and abs_z < 1.0:
                active = False
    cross_df = pd.DataFrame(crossings)
    if not cross_df.empty:
        for name, mask in {
            "B2_reversion_crossings_all": pd.Series(True, index=cross_df.index),
            "B2_reversion_crossings_gamma_pin": cross_df["gamma_pin"],
            "B2_reversion_crossings_no_gamma_pin": ~cross_df["gamma_pin"],
        }.items():
            sub = cross_df[mask.fillna(False)]
            rows.append({
                "track": "B_time_to_reversion",
                "test": name,
                "baseline": "B2_reversion_crossings_all",
                "horizon": 0,
                "n": int(len(sub)),
                "resolved_rate": float(sub["minutes_to_revert"].notna().mean()) if len(sub) else np.nan,
                "median_minutes_to_revert": float(sub["minutes_to_revert"].median()) if sub["minutes_to_revert"].notna().any() else np.nan,
            })
    for horizon in (15, 30):
        rows.append(
            _signed_stats(
                frame,
                frame["opt_rr50_chg_30m"].notna() & (frame["opt_rr50_chg_30m"] != 0),
                opt_dir,
                f"future_change_{horizon}m",
                test=f"B3_rr50_leadlag_control_{horizon}m",
                track="B_lead_lag_control",
                baseline="random_or_unconditional",
                horizon=horizon,
            )
        )
    return pd.DataFrame(rows)


def run_track_d(frame: pd.DataFrame, sign_meta: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    opt_dir = _option_dir(frame, sign_meta)
    rr_abs = pd.to_numeric(frame["opt_rr50_chg_30m"], errors="coerce").abs()
    rr_active = rr_abs >= rr_abs.groupby(frame.index.tz_convert(EASTERN).date).transform(lambda x: x.expanding(min_periods=30).quantile(0.6))
    for fv_h in (15, 30):
        fv_chg = pd.to_numeric(frame[f"fair_value_chg_{fv_h}m"], errors="coerce")
        fv_dir = np.sign(fv_chg)
        fv_active = fv_dir != 0
        for horizon in (15, 30, 60):
            move_col = f"future_change_{horizon}m"
            rows.append(_signed_stats(frame, fv_active, fv_dir, move_col, test=f"D2_fv{fv_h}_direction_only", track="D_skew_flow_direction", baseline="fv_direction_only", horizon=horizon))
            rows.append(_signed_stats(frame, fv_active & rr_active & (opt_dir == fv_dir), fv_dir, move_col, test=f"D2_fv{fv_h}_skew_agrees", track="D_skew_flow_direction", baseline=f"D2_fv{fv_h}_direction_only", horizon=horizon))
            rows.append(_signed_stats(frame, fv_active & rr_active & (opt_dir == -fv_dir), fv_dir, move_col, test=f"D2_fv{fv_h}_skew_disagrees", track="D_skew_flow_direction", baseline=f"D2_fv{fv_h}_direction_only", horizon=horizon))
            rows.append(_signed_stats(frame, fv_active & (~rr_active.fillna(False)), fv_dir, move_col, test=f"D2_fv{fv_h}_skew_flat", track="D_skew_flow_direction", baseline=f"D2_fv{fv_h}_direction_only", horizon=horizon))
            prior_opt_dir = _option_dir(frame, sign_meta, col="opt_rr50_chg_15m").shift(15)
            rows.append(_signed_stats(frame, fv_active & rr_active & (prior_opt_dir == fv_dir), fv_dir, move_col, test=f"D4_fv{fv_h}_skew_leads", track="D_skew_lead_split", baseline=f"D2_fv{fv_h}_direction_only", horizon=horizon))
    return pd.DataFrame(rows)


def run_track_c(episodes: pd.DataFrame, frame: pd.DataFrame, sign_meta: dict[str, Any], stats: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    # Always produce candidate compound rows; mark thin samples naturally via n.
    dislocation = episodes["state"].eq("DISLOCATION")
    rows.append(_episode_stats(episodes, dislocation, test="C1_dislocation_baseline", track="C_compound_episode", baseline="DISLOCATION"))
    rows.append(
        _episode_stats(
            episodes,
            dislocation & episodes["gamma_pin"] & (~episodes["skew_against_fade"]),
            test="C1_dislocation_gamma_pin_skew_not_against",
            track="C_compound_episode",
            baseline="C1_dislocation_baseline",
        )
    )
    episode_dates = sorted(episodes["opened_ts"].dt.tz_convert(EASTERN).dt.date.astype(str).dropna().unique().tolist())
    if len(episode_dates) > 1:
        cut = int(len(episode_dates) * 0.7)
        holdout_dates_ep = set(episode_dates[cut:])
        holdout_ep = episodes[episodes["opened_ts"].dt.tz_convert(EASTERN).dt.date.astype(str).isin(holdout_dates_ep)].copy()
        if not holdout_ep.empty:
            rows.append(
                _episode_stats(
                    holdout_ep,
                    pd.Series(True, index=holdout_ep.index),
                    test="C3_holdout_all_episodes",
                    track="C_holdout_episode",
                    baseline="holdout_all",
                )
            )
            rows.append(
                _episode_stats(
                    holdout_ep,
                    holdout_ep["skew_agrees_fade"],
                    test="C3_holdout_skew_agrees_fade",
                    track="C_holdout_episode",
                    baseline="C3_holdout_all_episodes",
                )
            )
    # Track D holdout for best-looking fv/skew agreement if any pooled lift exists.
    opt_dir = _option_dir(frame, sign_meta)
    dates = sorted({str(d) for d in frame.index.tz_convert(EASTERN).date})
    cut = int(len(dates) * 0.7) if len(dates) > 1 else len(dates)
    holdout_dates = set(dates[cut:])
    holdout = frame[[str(d) in holdout_dates for d in frame.index.tz_convert(EASTERN).date]].copy()
    if not holdout.empty:
        fv_chg = pd.to_numeric(holdout["fair_value_chg_30m"], errors="coerce")
        fv_dir = np.sign(fv_chg)
        rr_abs = pd.to_numeric(holdout["opt_rr50_chg_30m"], errors="coerce").abs()
        rr_active = rr_abs >= rr_abs.groupby(holdout.index.tz_convert(EASTERN).date).transform(lambda x: x.expanding(min_periods=30).quantile(0.6))
        holdout_opt_dir = opt_dir.reindex(holdout.index)
        rows.append(_signed_stats(holdout, fv_dir != 0, fv_dir, "future_change_30m", test="C2_holdout_fv_direction_only", track="C_holdout_direction", baseline="fv_direction_only", horizon=30))
        rows.append(_signed_stats(holdout, (fv_dir != 0) & rr_active & (holdout_opt_dir == fv_dir), fv_dir, "future_change_30m", test="C2_holdout_fv_skew_agrees", track="C_holdout_direction", baseline="C2_holdout_fv_direction_only", horizon=30))
    return pd.DataFrame(rows)


def _delta_vs_baseline(stats: pd.DataFrame) -> pd.DataFrame:
    out = stats.copy()
    out["hit_ties_half_delta"] = np.nan
    out["resolution_rate_delta"] = np.nan
    for idx, row in out.iterrows():
        baseline = row.get("baseline")
        if not baseline or baseline == row.get("test"):
            continue
        base = out[(out["test"] == baseline) & (out["horizon"] == row.get("horizon"))]
        if base.empty:
            base = out[out["test"] == baseline]
        if base.empty:
            continue
        b = base.iloc[0]
        if pd.notna(row.get("hit_ties_half")) and pd.notna(b.get("hit_ties_half")):
            out.at[idx, "hit_ties_half_delta"] = float(row["hit_ties_half"] - b["hit_ties_half"])
        if pd.notna(row.get("resolution_rate")) and pd.notna(b.get("resolution_rate")):
            out.at[idx, "resolution_rate_delta"] = float(row["resolution_rate"] - b["resolution_rate"])
    return out


def summarize(stats: pd.DataFrame, sign_meta: dict[str, Any], frame: pd.DataFrame, episodes: pd.DataFrame, output_dir: Path) -> dict[str, Any]:
    eligible = stats[(stats["n"].fillna(0) >= 20)].copy()
    sort_cols = [col for col in ["hit_ties_half_delta", "resolution_rate_delta", "hit_ties_half", "resolution_rate", "n"] if col in eligible]
    best = eligible.sort_values(sort_cols, ascending=False).head(30).replace({np.nan: None}).to_dict(orient="records") if sort_cols else []
    options_gate_mask = ~eligible["test"].astype(str).str.startswith("A0_")
    pooled_gate_candidates = eligible[
        options_gate_mask
        & (
        ((eligible.get("hit_ties_half_delta", pd.Series(np.nan, index=eligible.index)) >= 0.08) & (eligible["n"] >= 50))
        | ((eligible.get("resolution_rate_delta", pd.Series(np.nan, index=eligible.index)) >= 0.08) & (eligible["n"] >= 50))
        )
    ].copy()
    holdout_gate_candidates = eligible[
        eligible["test"].astype(str).str.contains("holdout", case=False, na=False)
        & (
            ((eligible.get("hit_ties_half_delta", pd.Series(np.nan, index=eligible.index)) >= 0.08) & (eligible["n"] >= 50))
            | ((eligible.get("resolution_rate_delta", pd.Series(np.nan, index=eligible.index)) >= 0.08) & (eligible["n"] >= 50))
        )
    ].copy()
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "minute_rows": int(len(frame)),
        "episode_rows": int(len(episodes)),
        "start": frame.index.min().isoformat() if not frame.empty else None,
        "end": frame.index.max().isoformat() if not frame.empty else None,
        "rr_sign_calibration": sign_meta,
        "decision_gate_passed": bool(not holdout_gate_candidates.empty),
        "pooled_gate_candidates": pooled_gate_candidates.replace({np.nan: None}).to_dict(orient="records"),
        "holdout_gate_candidates": holdout_gate_candidates.replace({np.nan: None}).to_dict(orient="records"),
        "best_rows": best,
        "artifacts": {
            "stats": str(output_dir / "v2_options_context_stats.csv"),
            "episodes_joined": str(output_dir / "v2_options_episode_joined.csv"),
            "minute_joined": str(output_dir / "v2_options_minute_joined.csv"),
            "summary": str(output_dir / "v2_options_context_summary.json"),
        },
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    gamma = load_gamma_metrics_fast(
        Path(args.gamma_db),
        Path(args.gamma_cache) if args.gamma_cache else None,
        refresh_gamma_db=bool(args.refresh_gamma_db),
    )
    frame = build_minute_frame(args)
    sign_meta = calibrate_rr_sign(frame)
    alerts = load_v2_alerts(Path(args.v2_dir))
    episodes = attach_gamma_to_alerts(alerts, gamma, sign_meta)
    track_a = run_track_a(episodes)
    track_b = run_track_b(frame, sign_meta)
    track_d = run_track_d(frame, sign_meta)
    prelim = _delta_vs_baseline(pd.concat([track_a, track_b, track_d], ignore_index=True, sort=False))
    track_c = run_track_c(episodes, frame, sign_meta, prelim)
    stats = _delta_vs_baseline(pd.concat([track_a, track_b, track_d, track_c], ignore_index=True, sort=False))
    stats.to_csv(output_dir / "v2_options_context_stats.csv", index=False)
    episodes.to_csv(output_dir / "v2_options_episode_joined.csv", index=False)
    frame.tail(args.max_minute_rows).to_csv(output_dir / "v2_options_minute_joined.csv", index=True)
    summary = summarize(stats, sign_meta, frame, episodes, output_dir)
    (output_dir / "v2_options_context_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only V2 x gamma tracker options context research.")
    parser.add_argument("--gamma-db", default=str(DEFAULT_GAMMA_DB))
    parser.add_argument("--gamma-cache", default=str(GAMMA_CACHE), help="Optional cached near-spot gamma metrics from prior read-only research.")
    parser.add_argument("--refresh-gamma-db", action="store_true", help="Rebuild gamma metrics from SQLite instead of using the cached options context.")
    parser.add_argument("--v2-dir", default=str(V2_LIVE_DIR))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--gamma-window-ticks", type=float, default=4.0)
    parser.add_argument("--max-minute-rows", type=int, default=200000)
    args = parser.parse_args()
    summary = run(args)
    print(json.dumps({
        "ok": summary["ok"],
        "decision_gate_passed": summary["decision_gate_passed"],
        "minute_rows": summary["minute_rows"],
        "episode_rows": summary["episode_rows"],
        "rr_sign_calibration": summary["rr_sign_calibration"],
        "pooled_gate_candidates": summary["pooled_gate_candidates"][:10],
        "holdout_gate_candidates": summary["holdout_gate_candidates"][:10],
        "best_rows": summary["best_rows"][:10],
        "artifacts": summary["artifacts"],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()

