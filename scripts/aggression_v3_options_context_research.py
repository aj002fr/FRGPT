#!/usr/bin/env python3
"""Research put/call skew and gamma context for Aggression V3/V4.

The gamma tracker database is opened read-only. This script writes only under
``workspace/analysis/aggression_v3_options_context/`` and does not modify the
8055 gamma tracker, V1, V2, or V3 dashboards.
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

TICK_SIZE = 1.0 / 64.0
EASTERN = ZoneInfo("America/New_York")
DEFAULT_GAMMA_DB = Path(r"C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v3.db")
V3_LIVE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v3_live"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v3_options_context"
TBBO_FEATURE_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
PREDICTIONS = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models" / "walkforward_predictions.csv"
RR_COLS = ("rr25", "rr50", "rr75")
HORIZONS = (15, 30)


def _connect_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _parse_ts(series: pd.Series) -> pd.Series:
    ts = pd.to_datetime(series, errors="coerce")
    if getattr(ts.dt, "tz", None) is None:
        ts = ts.dt.tz_localize(EASTERN)
    return ts.dt.tz_convert("UTC")


def _expiry_ordinal(value: Any) -> float:
    raw = str(value or "").lower()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return float(digits) if digits else np.nan


def load_gamma_metrics(db_path: Path, gamma_window_ticks: float) -> pd.DataFrame:
    """Load one-minute front-weighted skew/RR/gamma metrics from gamma tracker DB."""

    with _connect_ro(db_path) as conn:
        summary = pd.read_sql_query(
            """
            SELECT s.timestamp, m.expiry, m.exp_date, m.backbone, m.skew_index, m.curve_index,
                   m.rr25, m.rr50, m.rr75, m.rr100,
                   m.fly25, m.fly50, m.fly75, m.fly100,
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
        surface_gamma = pd.read_sql_query(
            """
            SELECT s.timestamp, o.option_type, o.strike, o.dv01_gamma
            FROM option_surface_v2 o
            JOIN snapshot_v2 s ON s.id = o.snapshot_id
            WHERE o.dv01_gamma IS NOT NULL
            """,
            conn,
        )

    if summary.empty:
        return pd.DataFrame()
    summary["ts"] = _parse_ts(summary["timestamp"])
    summary["ordinal"] = summary["expiry"].map(_expiry_ordinal)
    summary = summary.dropna(subset=["ts", "ordinal"]).copy()
    summary["front_weight"] = 1.0 / (summary["ordinal"] - 1.0).clip(lower=1.0)
    for col in ["backbone", "skew_index", "curve_index", *RR_COLS, "rr100", "backbone_roc_5m", "backbone_roc_15m", "backbone_roc_30m", "backbone_pct"]:
        summary[col] = pd.to_numeric(summary[col], errors="coerce")

    def _weighted(group: pd.DataFrame) -> pd.Series:
        out: dict[str, float] = {}
        for col in ["backbone", "skew_index", "curve_index", *RR_COLS, "rr100", "backbone_roc_5m", "backbone_roc_15m", "backbone_roc_30m", "backbone_pct"]:
            valid = group.dropna(subset=[col, "front_weight"])
            out[col] = float(np.average(valid[col], weights=valid["front_weight"])) if not valid.empty else np.nan
        for col in RR_COLS:
            out[f"{col}_available"] = int(group[col].notna().sum())
        out["expiry_count"] = int(group["expiry"].nunique())
        return pd.Series(out)

    agg = summary.groupby("ts", sort=True).apply(_weighted).reset_index()
    if not futures.empty:
        futures["ts"] = pd.to_datetime(pd.to_numeric(futures["timestamp"], errors="coerce"), unit="s", utc=True)
    near_gamma = _build_near_gamma(surface_gamma, futures, gamma_window_ticks)
    if not near_gamma.empty:
        agg = agg.merge(near_gamma, on="ts", how="left")
    for col in ("gamma_near_spot", "put_gamma_near_spot", "call_gamma_near_spot"):
        if col not in agg:
            agg[col] = np.nan
    if not futures.empty:
        futures = futures.drop(columns=["timestamp"]).groupby("ts", sort=True).last().reset_index()
        agg = agg.merge(futures, on="ts", how="left")

    agg = agg.set_index("ts").sort_index().resample("1min").last().ffill(limit=2)
    for col in RR_COLS:
        agg[f"{col}_chg_5m"] = agg[col].diff(5)
        agg[f"{col}_chg_15m"] = agg[col].diff(15)
        agg[f"{col}_chg_30m"] = agg[col].diff(30)
    agg["skew_index_chg_30m"] = agg["skew_index"].diff(30)
    agg["gamma_near_spot_z"] = _session_z(agg["gamma_near_spot"])
    agg["gamma_pin_zone"] = agg["gamma_near_spot_z"] >= 1.0
    return agg


def _build_near_gamma(surface: pd.DataFrame, futures: pd.DataFrame, gamma_window_ticks: float) -> pd.DataFrame:
    if surface.empty or futures.empty:
        return pd.DataFrame(columns=["ts", "gamma_near_spot", "put_gamma_near_spot", "call_gamma_near_spot"])
    surface = surface.copy()
    surface["ts"] = _parse_ts(surface["timestamp"])
    surface["strike"] = pd.to_numeric(surface["strike"], errors="coerce")
    surface["dv01_gamma"] = pd.to_numeric(surface["dv01_gamma"], errors="coerce")
    surface = surface.dropna(subset=["ts", "strike", "dv01_gamma"]).sort_values("ts")
    fut = futures[["ts", "futures_px"]].copy()
    fut["futures_px"] = pd.to_numeric(fut["futures_px"], errors="coerce")
    fut = fut.dropna(subset=["ts", "futures_px"]).sort_values("ts")
    if surface.empty or fut.empty:
        return pd.DataFrame(columns=["ts", "gamma_near_spot", "put_gamma_near_spot", "call_gamma_near_spot"])
    merged = pd.merge_asof(surface, fut, on="ts", direction="nearest", tolerance=pd.Timedelta(seconds=2))
    merged = merged.dropna(subset=["futures_px"])
    merged = merged[(merged["strike"] - merged["futures_px"]).abs() <= gamma_window_ticks * TICK_SIZE].copy()
    if merged.empty:
        return pd.DataFrame(columns=["ts", "gamma_near_spot", "put_gamma_near_spot", "call_gamma_near_spot"])
    merged["abs_gamma"] = merged["dv01_gamma"].abs()
    opt_type = merged["option_type"].astype(str).str.lower()
    merged["put_abs_gamma"] = np.where(opt_type.str.startswith("p"), merged["abs_gamma"], 0.0)
    merged["call_abs_gamma"] = np.where(opt_type.str.startswith("c"), merged["abs_gamma"], 0.0)
    return merged.groupby("ts", as_index=False).agg(
        gamma_near_spot=("abs_gamma", "sum"),
        put_gamma_near_spot=("put_abs_gamma", "sum"),
        call_gamma_near_spot=("call_abs_gamma", "sum"),
    )


def _session_z(series: pd.Series, min_periods: int = 30) -> pd.Series:
    def zscore(day: pd.Series) -> pd.Series:
        mean = day.expanding(min_periods=min_periods).mean()
        std = day.expanding(min_periods=min_periods).std(ddof=1).replace(0.0, np.nan)
        return (day - mean) / std

    if series.empty:
        return series
    return series.groupby(series.index.tz_convert(EASTERN).date).transform(zscore)


def _read_csv_glob(pattern: str) -> pd.DataFrame:
    frames = []
    for path in sorted(glob.glob(pattern)):
        try:
            frames.append(pd.read_csv(path))
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_v3_logs(v3_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    signal = _read_csv_glob(str(v3_dir / "signal_log_*.csv"))
    regime = _read_csv_glob(str(v3_dir / "regime_log_*.csv"))
    if not signal.empty and "ts" in signal:
        signal["ts"] = pd.to_datetime(signal["ts"], utc=True, errors="coerce")
        signal = signal.dropna(subset=["ts"]).set_index("ts").sort_index()
        signal = signal.resample("1min").last()
    if not regime.empty and "ts" in regime:
        regime["ts"] = pd.to_datetime(regime["ts"], utc=True, errors="coerce")
        regime = regime.dropna(subset=["ts"]).set_index("ts").sort_index()
        regime = regime.resample("1min").last()
    return signal, regime


def load_tbbo_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    cols = [
        "bucket_ts",
        "et_date",
        "price",
        "agg_5m",
        "agg_30m",
        "buysell_ratio",
        "displayed_depth_mean",
        "depth_adjusted_signed_flow",
        "realized_vol",
        "mom_5m",
    ]
    data = pd.read_csv(path, usecols=lambda c: c in cols, parse_dates=["bucket_ts"])
    data["bucket_ts"] = pd.to_datetime(data["bucket_ts"], utc=True)
    return data.set_index("bucket_ts").sort_index().resample("1min").last()


def load_predictions(path: Path, model: str = "ridge") -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    cols = ["bucket_ts", "model", "price", "fair_value", "residual"]
    pred = pd.read_csv(path, usecols=lambda c: c in cols, parse_dates=["bucket_ts"])
    pred = pred[pred["model"] == model].copy()
    pred["bucket_ts"] = pd.to_datetime(pred["bucket_ts"], utc=True)
    pred = pred.set_index("bucket_ts").sort_index().resample("1min").last()
    pred["residual_z_hist"] = pred.groupby(pred.index.tz_convert(EASTERN).date)["residual"].transform(
        lambda x: (x - x.mean()) / (x.std(ddof=1) if x.std(ddof=1) else np.nan)
    )
    return pred


def build_joined(args: argparse.Namespace) -> pd.DataFrame:
    gamma = load_gamma_metrics(Path(args.gamma_db), args.gamma_window_ticks)
    signal, regime = load_v3_logs(Path(args.v3_dir))
    tbbo = load_tbbo_history(Path(args.tbbo_features))
    pred = load_predictions(Path(args.predictions))

    frames = [gamma.add_prefix("opt_")]
    if not signal.empty:
        frames.append(signal.add_prefix("v3_"))
    if not regime.empty:
        frames.append(regime.add_prefix("regime_"))
    if not tbbo.empty:
        frames.append(tbbo.add_prefix("tbbo_"))
    if not pred.empty:
        frames.append(pred.add_prefix("pred_"))
    joined = pd.concat(frames, axis=1).sort_index()
    price_candidates = [
        "v3_price",
        "tbbo_price",
        "pred_price",
        "opt_futures_px",
    ]
    joined["price"] = np.nan
    for col in price_candidates:
        if col in joined:
            joined["price"] = joined["price"].fillna(pd.to_numeric(joined[col], errors="coerce"))
    joined = joined[joined["price"].notna()].copy()
    for horizon in HORIZONS:
        joined[f"future_change_{horizon}m"] = joined.groupby(joined.index.tz_convert(EASTERN).date)["price"].shift(-horizon) - joined["price"]
        joined[f"future_dir_{horizon}m"] = np.sign(joined[f"future_change_{horizon}m"])
    return joined


def _stats_for_mask(df: pd.DataFrame, mask: pd.Series, name: str, horizon: int, direction: pd.Series | None = None) -> dict[str, Any]:
    subset = df[mask.fillna(False)].dropna(subset=[f"future_change_{horizon}m"])
    if subset.empty:
        return {"test": name, "horizon": horizon, "n": 0}
    if direction is None:
        direction = pd.Series(np.sign(subset[f"future_change_{horizon}m"]), index=subset.index)
    else:
        direction = direction.reindex(subset.index)
    signed = direction * subset[f"future_change_{horizon}m"]
    hit = signed > 0
    return {
        "test": name,
        "horizon": int(horizon),
        "n": int(len(subset)),
        "hit_rate": float(hit.mean()) if len(subset) else np.nan,
        "avg_signed_move": float(signed.mean()) if signed.notna().any() else np.nan,
        "median_signed_move": float(signed.median()) if signed.notna().any() else np.nan,
        "avg_abs_move": float(subset[f"future_change_{horizon}m"].abs().mean()),
    }


def run_tests(joined: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if joined.empty:
        return pd.DataFrame()

    rr = joined["opt_rr50_chg_30m"] if "opt_rr50_chg_30m" in joined else pd.Series(np.nan, index=joined.index)
    rr_dir = np.sign(rr)
    rr_opp = -np.sign(rr)
    gamma_pin = joined.get("opt_gamma_pin_zone", pd.Series(False, index=joined.index)).fillna(False).astype(bool)
    regime_absorption = joined.get("regime_flags", pd.Series("", index=joined.index)).astype(str).str.contains(
        "absorption.*true|\"absorption\": true", case=False, regex=True
    )
    price_5m_ticks = joined.groupby(joined.index.tz_convert(EASTERN).date)["price"].diff(5).abs() / TICK_SIZE
    tbbo_effort = pd.to_numeric(joined.get("tbbo_agg_5m", pd.Series(np.nan, index=joined.index)), errors="coerce").abs() / price_5m_ticks.clip(lower=1.0)
    regime_effort = pd.to_numeric(joined.get("regime_effort_per_tick", pd.Series(np.nan, index=joined.index)), errors="coerce")
    effort = regime_effort.fillna(tbbo_effort)
    high_effort = effort >= effort.groupby(joined.index.tz_convert(EASTERN).date).transform(lambda x: x.expanding(min_periods=30).quantile(0.75))
    tbbo_agg_z = joined.groupby(joined.index.tz_convert(EASTERN).date)["tbbo_agg_5m"].transform(
        lambda x: (x - x.expanding(min_periods=30).mean())
        / x.expanding(min_periods=30).std(ddof=1).replace(0.0, np.nan)
    ) if "tbbo_agg_5m" in joined else pd.Series(np.nan, index=joined.index)
    muted_move = price_5m_ticks <= price_5m_ticks.groupby(joined.index.tz_convert(EASTERN).date).transform(
        lambda x: x.expanding(min_periods=30).median()
    )
    absorption = regime_absorption | ((tbbo_agg_z.abs() >= 1.0) & muted_move)
    residual_z = pd.to_numeric(joined.get("v3_residual_z", joined.get("pred_residual_z_hist", pd.Series(np.nan, index=joined.index))), errors="coerce")
    residual = pd.to_numeric(joined.get("v3_residual", joined.get("pred_residual", pd.Series(np.nan, index=joined.index))), errors="coerce")
    fade_dir = -np.sign(residual)
    breakout = joined.get("regime_regime", pd.Series("", index=joined.index)).astype(str).str.upper().eq("BREAKOUT")

    for horizon in HORIZONS:
        rows.append(_stats_for_mask(joined, rr.notna() & (rr != 0), "lead_lag_rr50_same_sign", horizon, rr_dir))
        rows.append(_stats_for_mask(joined, rr.notna() & (rr != 0), "lead_lag_rr50_opposite_sign", horizon, rr_opp))
        zmask = residual_z.abs() >= 1.5
        rows.append(_stats_for_mask(joined, zmask, "v3_residual_z_abs_ge_1_5_baseline", horizon, fade_dir))
        rows.append(_stats_for_mask(joined, zmask & (np.sign(rr) == np.sign(residual)), "residual_fade_rr_same_as_residual", horizon, fade_dir))
        rows.append(_stats_for_mask(joined, zmask & (np.sign(rr) == -np.sign(residual)), "residual_fade_rr_opposite_residual", horizon, fade_dir))
        rows.append(_stats_for_mask(joined, breakout, "flow_effort_breakout_baseline", horizon, np.sign(joined.get("regime_effort_ratio", pd.Series(0.0, index=joined.index)).fillna(0.0) * 0 + rr_dir)))
        rows.append(_stats_for_mask(joined, breakout & (rr != 0), "flow_effort_breakout_with_rr_change", horizon, rr_dir))
        rows.append(_stats_for_mask(joined, gamma_pin, "gamma_pin_baseline", horizon, fade_dir))
        rows.append(_stats_for_mask(joined, gamma_pin & high_effort, "gamma_pin_high_effort", horizon, fade_dir))
        rows.append(_stats_for_mask(joined, gamma_pin & absorption, "gamma_pin_absorption", horizon, fade_dir))
    return pd.DataFrame(rows)


def summarize(joined: pd.DataFrame, stats: pd.DataFrame, output_dir: Path) -> dict[str, Any]:
    eligible = stats[stats["n"] >= 20].copy() if not stats.empty else pd.DataFrame()
    best = eligible.sort_values(["hit_rate", "n"], ascending=False).head(20).to_dict(orient="records") if not eligible.empty else []
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(joined)),
        "start": joined.index.min().isoformat() if not joined.empty else None,
        "end": joined.index.max().isoformat() if not joined.empty else None,
        "columns": sorted(joined.columns.tolist()),
        "best": best,
        "artifacts": {
            "joined": str(output_dir / "options_context_joined.csv"),
            "stats": str(output_dir / "options_context_stats.csv"),
            "summary": str(output_dir / "options_context_summary.json"),
        },
        "notes": [
            "Gamma DB was opened read-only.",
            "RR sign convention is empirical; both same-sign and opposite-sign variants are reported.",
            "Sample is thin and should be treated as research only.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Research gamma tracker options context against V3 aggression logs.")
    parser.add_argument("--gamma-db", default=str(DEFAULT_GAMMA_DB))
    parser.add_argument("--v3-dir", default=str(V3_LIVE_DIR))
    parser.add_argument("--tbbo-features", default=str(TBBO_FEATURE_MATRIX))
    parser.add_argument("--predictions", default=str(PREDICTIONS))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--gamma-window-ticks", type=float, default=4.0)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    joined = build_joined(args)
    stats = run_tests(joined)
    joined.tail(200000).to_csv(output_dir / "options_context_joined.csv", index=True)
    stats.to_csv(output_dir / "options_context_stats.csv", index=False)
    summary = summarize(joined, stats, output_dir)
    (output_dir / "options_context_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("ok", "rows", "start", "end", "best", "artifacts")}, indent=2, default=str))


if __name__ == "__main__":
    main()

