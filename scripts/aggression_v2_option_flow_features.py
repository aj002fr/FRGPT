#!/usr/bin/env python3
"""Build normalized TY option signed-flow features from local trade cache.

Reads compact Phase-0/1 parquet only; no source API/DB/live-store writes.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = Path(os.environ.get("TY_OPTION_FLOW_CACHE_DIR", Path.home() / ".market_data" / "ty_option_flow_cache"))
V2_LIVE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_live"
TBBO_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
RIDGE_PREDICTIONS = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models" / "walkforward_predictions.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_option_flow"


def load_price(live_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(glob.glob(str(live_dir / "signal_log_*.csv"))):
        try:
            frames.append(pd.read_csv(path, usecols=["ts", "price", "fair_value", "residual", "residual_z"]))
        except Exception:
            continue
    historical = pd.DataFrame()
    if TBBO_MATRIX.exists():
        matrix = pd.read_csv(TBBO_MATRIX, usecols=["bucket_ts", "price"])
        matrix["bucket_ts"] = pd.to_datetime(matrix["bucket_ts"], errors="coerce", utc=True)
        historical = matrix.rename(columns={"bucket_ts": "ts"})
    if RIDGE_PREDICTIONS.exists():
        predictions = pd.read_csv(RIDGE_PREDICTIONS, parse_dates=["bucket_ts"])
        predictions = predictions[predictions["model"] == "ridge"][
            ["bucket_ts", "fair_value", "residual"]
        ].drop_duplicates("bucket_ts", keep="last")
        predictions["bucket_ts"] = pd.to_datetime(predictions["bucket_ts"], utc=True)
        predictions["residual_z"] = predictions.groupby(predictions["bucket_ts"].dt.date)["residual"].transform(
            lambda values: (values - values.expanding(min_periods=30).mean())
            / values.expanding(min_periods=30).std(ddof=1).replace(0.0, np.nan)
        )
        if historical.empty:
            historical = predictions.rename(columns={"bucket_ts": "ts"})
        else:
            historical = historical.merge(
                predictions.rename(columns={"bucket_ts": "ts"}),
                on="ts",
                how="left",
            )
    live = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not live.empty:
        live["ts"] = pd.to_datetime(live["ts"], errors="coerce", utc=True, format="mixed")
    data = pd.concat([historical, live], ignore_index=True, sort=False)
    if data.empty:
        return data
    data["ts"] = pd.DatetimeIndex(pd.to_datetime(data["ts"], errors="coerce", utc=True)).as_unit("ns")
    return data.dropna(subset=["ts", "price"]).sort_values("ts").drop_duplicates("ts", keep="last")


def _streak(series: pd.Series) -> pd.Series:
    values, current, previous = [], 0.0, 0.0
    for value in np.sign(series.fillna(0.0)):
        if value == 0:
            current = 0.0
        elif value == previous:
            current += value
        else:
            current = value
        values.append(current)
        previous = value
    return pd.Series(values, index=series.index)


def build_features(cache_dir: Path, live_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    paths = [path for path in cache_dir.rglob("*.parquet") if not path.name.endswith(".tmp.parquet")]
    frames = [pd.read_parquet(path) for path in paths]
    trades = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if trades.empty:
        return pd.DataFrame(), {"rows": 0, "reason": "no cached trades"}
    trades["ts_event"] = pd.to_datetime(trades["ts_event"], errors="coerce", utc=True)
    trades["ts_event"] = pd.DatetimeIndex(trades["ts_event"]).as_unit("ns")
    trades = trades.dropna(subset=["ts_event", "aggressor", "size", "price"]).sort_values("ts_event")
    price = load_price(live_dir)
    if not price.empty:
        trades = pd.merge_asof(
            trades,
            price.rename(columns={"ts": "price_ts", "price": "ty_price"}).sort_values("price_ts"),
            left_on="ts_event",
            right_on="price_ts",
            direction="nearest",
            tolerance=pd.Timedelta(minutes=1),
        )
    else:
        trades["ty_price"] = np.nan
    cp_sign = np.where(trades["instrument_class"].astype(str).str.upper() == "C", 1.0, -1.0)
    trades["underlying_direction"] = trades["aggressor"].astype(float) * cp_sign
    trades["direction_contract_flow"] = trades["underlying_direction"] * trades["size"].astype(float)
    trades["direction_premium_flow"] = trades["underlying_direction"] * trades["size"].astype(float) * trades["price"].astype(float)
    for column in ("delta_F_mid", "vega_F_mid", "gamma_F_mid"):
        trades[column] = pd.to_numeric(trades.get(column), errors="coerce")
    trades["signed_delta_flow"] = trades["aggressor"] * trades["size"] * trades["delta_F_mid"]
    trades["signed_vega_flow"] = trades["aggressor"] * trades["size"] * trades["vega_F_mid"]
    trades["signed_gamma_flow"] = trades["aggressor"] * trades["size"] * trades["gamma_F_mid"]
    trades["strike_distance"] = (pd.to_numeric(trades["strike_price"], errors="coerce") - pd.to_numeric(trades["ty_price"], errors="coerce")).abs()
    trades["zone"] = np.select(
        [trades["strike_distance"] <= 0.5, trades["strike_distance"] <= 1.5],
        ["ATM", "NEAR_WING"],
        default="WING",
    )
    trades["expiry_bucket"] = np.where(pd.to_numeric(trades["dte"], errors="coerce") <= 7, "FRONT", "DEFERRED")
    trades["et_date"] = trades["ts_event"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    trades["leg"] = trades["instrument_class"].astype(str).str.upper().str[0]
    p90 = trades.groupby(["et_date", "leg"])["size"].transform(lambda values: values.quantile(0.90))
    p95 = trades.groupby(["et_date", "leg"])["size"].transform(lambda values: values.quantile(0.95))
    trades["large_p90"] = trades["size"] >= p90
    trades["large_p95"] = trades["size"] >= p95
    trades["second_bucket"] = trades["ts_event"].dt.floor("1s")
    sweep = (
        trades.groupby(["second_bucket", "leg", "aggressor"])["strike_price"]
        .transform("nunique") >= 3
    )
    trades["sweep_burst_1s"] = sweep
    trades["minute"] = trades["ts_event"].dt.floor("1min")
    for leg in ("C", "P"):
        trades[f"{leg}_buy_contracts"] = np.where((trades["leg"] == leg) & (trades["aggressor"] == 1), trades["size"], 0.0)
        trades[f"{leg}_sell_contracts"] = np.where((trades["leg"] == leg) & (trades["aggressor"] == -1), trades["size"], 0.0)
    for zone in ("ATM", "NEAR_WING", "WING"):
        trades[f"{zone}_direction_flow"] = np.where(trades["zone"] == zone, trades["direction_contract_flow"], 0.0)
    for bucket in ("FRONT", "DEFERRED"):
        trades[f"{bucket}_direction_flow"] = np.where(trades["expiry_bucket"] == bucket, trades["direction_contract_flow"], 0.0)
    trades["large_p90_direction_flow"] = np.where(trades["large_p90"], trades["direction_contract_flow"], 0.0)
    trades["large_p95_direction_flow"] = np.where(trades["large_p95"], trades["direction_contract_flow"], 0.0)
    trades["sweep_direction_flow"] = np.where(trades["sweep_burst_1s"], trades["direction_contract_flow"], 0.0)
    sum_cols = [
        "C_buy_contracts", "C_sell_contracts", "P_buy_contracts", "P_sell_contracts",
        "direction_contract_flow", "direction_premium_flow",
        "signed_delta_flow", "signed_vega_flow", "signed_gamma_flow",
        "ATM_direction_flow", "NEAR_WING_direction_flow", "WING_direction_flow",
        "FRONT_direction_flow", "DEFERRED_direction_flow",
        "large_p90_direction_flow", "large_p95_direction_flow", "sweep_direction_flow",
    ]
    minute = trades.groupby("minute")[sum_cols].sum()
    minute["trade_count"] = trades.groupby("minute").size()
    minute["total_contracts"] = trades.groupby("minute")["size"].sum()
    minute["put_call_buy_imbalance"] = (
        minute["C_buy_contracts"] - minute["P_buy_contracts"]
    ) / (minute["C_buy_contracts"] + minute["P_buy_contracts"]).replace(0.0, np.nan)
    for window in (5, 15, 30):
        for column in ("direction_contract_flow", "direction_premium_flow", "signed_delta_flow", "signed_vega_flow", "signed_gamma_flow", "P_buy_contracts", "C_buy_contracts", "large_p90_direction_flow", "sweep_direction_flow"):
            minute[f"{column}_{window}m"] = minute[column].rolling(window, min_periods=1).sum()
    minute["flow_streak"] = _streak(minute["direction_contract_flow"])
    minute = minute.reset_index(names="ts")
    if not price.empty:
        minute = minute.merge(price, on="ts", how="left")
    audit = {
        "cache_files": len(paths),
        "trade_rows": int(len(trades)),
        "minute_rows": int(len(minute)),
        "dates": sorted(trades["et_date"].unique().tolist()),
        "price_join_rate": float(trades["ty_price"].notna().mean()),
        "atm_rate": float((trades["zone"] == "ATM").mean()),
        "large_p90_rate": float(trades["large_p90"].mean()),
        "sweep_burst_rate": float(trades["sweep_burst_1s"].mean()),
        "delta_coverage": float(trades["delta_F_mid"].notna().mean()),
        "vega_coverage": float(trades["vega_F_mid"].notna().mean()),
        "gamma_coverage": float(trades["gamma_F_mid"].notna().mean()),
    }
    return minute, audit


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    features, audit = build_features(Path(args.cache_dir), Path(args.v2_live))
    feature_path = output_dir / "option_flow_1m.csv"
    audit_path = output_dir / "option_flow_features_summary.json"
    features.to_csv(feature_path, index=False)
    summary = {
        "ok": not features.empty,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "audit": audit,
        "artifacts": {"features": str(feature_path), "summary": str(audit_path)},
    }
    audit_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build TY option signed-flow features.")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    parser.add_argument("--v2-live", default=str(V2_LIVE))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
