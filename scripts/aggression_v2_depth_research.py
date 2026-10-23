#!/usr/bin/env python3
"""Research-only MBP-10 depth tests for the Aggression V2/V3 workflow.

This script intentionally does not modify any live dashboard. It builds a
1-minute ZN depth feature matrix and tests whether disappearing displayed depth
is associated with higher future volatility, different V2 dislocation outcomes,
or better fair-value fits.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.linear_model import RidgeCV
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from scripts.aggression_dashboard_v2.features import FEATURE_SET


DEFAULT_MBP10_DIR = Path(r"D:\FRDBs\FutMBP\ZN_outrights_mbp10")
DEFAULT_PREBUILT_DIR = Path(r"D:\FRDBs\FutMBP\research_features")
DEFAULT_FEATURE_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
DEFAULT_PREDICTIONS = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models" / "walkforward_predictions.csv"
DEFAULT_OUT = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_depth"
DEFAULT_DOC = PROJECT_ROOT / "docs" / "aggression_v2_depth_research.md"

LEVELS = range(10)
RAW_COLS = [
    "ts_event",
    "symbol",
    "action",
    "side",
    "depth",
    "price",
    "size",
    *[f"bid_px_{i:02d}" for i in LEVELS],
    *[f"ask_px_{i:02d}" for i in LEVELS],
    *[f"bid_sz_{i:02d}" for i in LEVELS],
    *[f"ask_sz_{i:02d}" for i in LEVELS],
]
PREBUILT_COLS = [
    "ts_event",
    "action",
    "side",
    "size",
    "mid",
    "bs0",
    "as0",
    "cum_bid_10",
    "cum_ask_10",
    "imb10",
    "ofi_event",
    "ofi_100ms",
    "ofi_500ms",
    "ofi_2s",
    "can_B_event",
    "can_A_event",
    "add_B_event",
    "add_A_event",
    "sweep_flag",
    "cancel_qty_B_100ms",
    "cancel_qty_A_100ms",
]
DEPTH_FEATURES = [
    "total_depth_10",
    "depth_imb_10",
    "top_depth_share",
    "depth_z",
    "depth_chg_5m",
    "depth_chg_30m",
    "quote_ofi_1m",
    "signed_trade_1m",
    "ofi_100ms_mean",
    "ofi_500ms_mean",
    "ofi_2s_mean",
    "cancel_to_add",
    "cancel_imbalance",
    "sweep_count",
]


def _numeric(frame: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for col in cols:
        if col in frame:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def _parquet_columns(path: Path) -> list[str]:
    return pq.ParquetFile(path).schema_arrow.names


def _select_front_symbol(raw: pd.DataFrame) -> str | None:
    trades = raw[raw["action"].astype(str) == "T"].copy()
    if not trades.empty:
        volume = trades.groupby("symbol")["size"].sum().sort_values(ascending=False)
        return str(volume.index[0]) if not volume.empty else None
    counts = raw["symbol"].value_counts()
    return str(counts.index[0]) if not counts.empty else None


def _read_raw_day(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    cols = [col for col in RAW_COLS if col in _parquet_columns(path)]
    raw = pd.read_parquet(path, columns=cols)
    raw["ts_event"] = pd.to_datetime(raw["ts_event"], utc=True, errors="coerce")
    raw = raw.dropna(subset=["ts_event", "symbol"]).copy()
    raw = _numeric(raw, ["price", "size", *[f"bid_px_{i:02d}" for i in LEVELS], *[f"ask_px_{i:02d}" for i in LEVELS], *[f"bid_sz_{i:02d}" for i in LEVELS], *[f"ask_sz_{i:02d}" for i in LEVELS]])
    symbol = _select_front_symbol(raw)
    if symbol:
        raw = raw[raw["symbol"].astype(str) == symbol].copy()
    return raw, {
        "file": path.name,
        "source": "raw_mbp10",
        "selected_symbol": symbol,
        "raw_rows": int(len(raw)),
        "start_utc": raw["ts_event"].min().isoformat() if not raw.empty else None,
        "end_utc": raw["ts_event"].max().isoformat() if not raw.empty else None,
    }


def _read_prebuilt_day(raw_path: Path, prebuilt_dir: Path) -> tuple[pd.DataFrame | None, dict[str, Any] | None]:
    date_token = raw_path.name.split("-")[2].split(".")[0]
    matches = sorted(prebuilt_dir.glob(f"{date_token}_ZN*_features.parquet"))
    matches = [path for path in matches if not path.name.startswith("tmp_")]
    if not matches:
        return None, None
    path = matches[0]
    cols = [col for col in PREBUILT_COLS if col in _parquet_columns(path)]
    frame = pd.read_parquet(path, columns=cols)
    frame["ts_event"] = pd.to_datetime(frame["ts_event"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["ts_event"]).copy()
    frame = _numeric(frame, [col for col in cols if col != "ts_event" and col not in {"action", "side"}])
    return frame, {
        "file": path.name,
        "source": "prebuilt_research_features",
        "selected_symbol": path.name.split("_")[1] if "_" in path.name else None,
        "raw_rows": int(len(frame)),
        "start_utc": frame["ts_event"].min().isoformat() if not frame.empty else None,
        "end_utc": frame["ts_event"].max().isoformat() if not frame.empty else None,
    }


def _minute_features_from_prebuilt(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data = data.set_index("ts_event").sort_index()
    bid = data["cum_bid_10"].astype(float)
    ask = data["cum_ask_10"].astype(float)
    total = bid + ask
    out = pd.DataFrame(index=data.resample("1min").size().index)
    out["mid"] = data["mid"].resample("1min").last().ffill()
    out["total_depth_10"] = total.resample("1min").mean()
    out["bid_depth_10"] = bid.resample("1min").mean()
    out["ask_depth_10"] = ask.resample("1min").mean()
    out["depth_imb_10"] = data["imb10"].resample("1min").mean()
    out["quote_ofi_1m"] = data.get("ofi_event", pd.Series(0.0, index=data.index)).resample("1min").sum()
    out["signed_trade_1m"] = data.get("signed_trade_event", pd.Series(0.0, index=data.index)).resample("1min").sum()
    for horizon in ("100ms", "500ms", "2s"):
        col = f"ofi_{horizon}"
        out[f"{col}_mean"] = data.get(col, pd.Series(np.nan, index=data.index)).resample("1min").mean()
    can_b = data.get("can_B_event", pd.Series(0.0, index=data.index)).resample("1min").sum()
    can_a = data.get("can_A_event", pd.Series(0.0, index=data.index)).resample("1min").sum()
    add_b = data.get("add_B_event", pd.Series(0.0, index=data.index)).resample("1min").sum()
    add_a = data.get("add_A_event", pd.Series(0.0, index=data.index)).resample("1min").sum()
    out["cancel_qty"] = can_b + can_a
    out["add_qty"] = add_b + add_a
    out["cancel_to_add"] = out["cancel_qty"] / out["add_qty"].replace(0.0, np.nan)
    out["cancel_imbalance"] = (can_b - can_a) / (can_b + can_a).replace(0.0, np.nan)
    out["sweep_count"] = data.get("sweep_flag", pd.Series(0.0, index=data.index)).resample("1min").sum()
    if "bs0" in data and "as0" in data:
        out["top_depth_share"] = ((data["bs0"].astype(float) + data["as0"].astype(float)) / total.replace(0.0, np.nan)).resample("1min").mean()
    else:
        out["top_depth_share"] = np.nan
    return out


def _minute_features_from_raw(raw: pd.DataFrame) -> pd.DataFrame:
    data = raw.copy().set_index("ts_event").sort_index()
    bid_depth = sum(data[f"bid_sz_{i:02d}"].astype(float).fillna(0.0) for i in LEVELS)
    ask_depth = sum(data[f"ask_sz_{i:02d}"].astype(float).fillna(0.0) for i in LEVELS)
    total = bid_depth + ask_depth
    bid0 = data["bid_sz_00"].astype(float).fillna(0.0)
    ask0 = data["ask_sz_00"].astype(float).fillna(0.0)
    mid = (data["bid_px_00"].astype(float) + data["ask_px_00"].astype(float)) / 2.0
    out = pd.DataFrame(index=data.resample("1min").size().index)
    out["mid"] = mid.resample("1min").last().ffill()
    out["total_depth_10"] = total.resample("1min").mean()
    out["bid_depth_10"] = bid_depth.resample("1min").mean()
    out["ask_depth_10"] = ask_depth.resample("1min").mean()
    out["depth_imb_10"] = ((bid_depth - ask_depth) / total.replace(0.0, np.nan)).resample("1min").mean()
    out["top_depth_share"] = ((bid0 + ask0) / total.replace(0.0, np.nan)).resample("1min").mean()
    out["quote_ofi_1m"] = (bid_depth.diff().fillna(0.0) - ask_depth.diff().fillna(0.0)).resample("1min").sum()
    action = data["action"].astype(str)
    side = data["side"].astype(str)
    size = data["size"].astype(float).fillna(0.0)
    trades = action == "T"
    trade_sign = np.where(side == "B", 1.0, np.where(side == "A", -1.0, 0.0))
    out["signed_trade_1m"] = pd.Series(trade_sign, index=data.index).where(trades, 0.0).mul(size).resample("1min").sum()
    for horizon in ("100ms", "500ms", "2s"):
        out[f"ofi_{horizon}_mean"] = np.nan
    can_b = size.where((action == "C") & (side == "B"), 0.0).resample("1min").sum()
    can_a = size.where((action == "C") & (side == "A"), 0.0).resample("1min").sum()
    add_b = size.where((action == "A") & (side == "B"), 0.0).resample("1min").sum()
    add_a = size.where((action == "A") & (side == "A"), 0.0).resample("1min").sum()
    out["cancel_qty"] = can_b + can_a
    out["add_qty"] = add_b + add_a
    out["cancel_to_add"] = out["cancel_qty"] / out["add_qty"].replace(0.0, np.nan)
    out["cancel_imbalance"] = (can_b - can_a) / (can_b + can_a).replace(0.0, np.nan)
    out["sweep_count"] = trades.astype(float).resample("1min").sum()
    return out


def _finalize_minute_features(frame: pd.DataFrame, date_hint: str) -> pd.DataFrame:
    out = frame.copy()
    out = out[out["mid"].notna() & out["total_depth_10"].notna()].copy()
    out["ret_1m"] = out["mid"].diff()
    out["rv_15m_fwd"] = out["ret_1m"].rolling(15, min_periods=5).std().shift(-15)
    out["rv_30m_fwd"] = out["ret_1m"].rolling(30, min_periods=10).std().shift(-30)
    trailing_mean = out["total_depth_10"].rolling(60, min_periods=20).mean()
    trailing_std = out["total_depth_10"].rolling(60, min_periods=20).std(ddof=1)
    out["depth_z"] = (out["total_depth_10"] - trailing_mean) / trailing_std.replace(0.0, np.nan)
    out["depth_chg_5m"] = out["total_depth_10"].pct_change(5)
    out["depth_chg_30m"] = out["total_depth_10"].pct_change(30)
    out["rv_30m_trailing"] = out["ret_1m"].rolling(30, min_periods=10).std()
    out["abs_move_15m_fwd"] = (out["mid"].shift(-15) - out["mid"]).abs()
    out["abs_move_30m_fwd"] = (out["mid"].shift(-30) - out["mid"]).abs()
    out["depth_refill_5m"] = out["total_depth_10"].shift(-5) / out["total_depth_10"] - 1.0
    out["depth_refill_15m"] = out["total_depth_10"].shift(-15) / out["total_depth_10"] - 1.0
    out["depth_refill_30m"] = out["total_depth_10"].shift(-30) / out["total_depth_10"] - 1.0
    out["imb_persist_5m"] = out["depth_imb_10"].rolling(5, min_periods=3).mean().abs()
    out["imb_persist_15m"] = out["depth_imb_10"].rolling(15, min_periods=5).mean().abs()
    out["et_date"] = pd.DatetimeIndex(out.index).tz_convert("America/New_York").date.astype(str)
    if out["et_date"].nunique() == 0:
        out["et_date"] = date_hint
    out["bucket_ts"] = out.index
    return out.reset_index(drop=True)


def build_depth_matrix(mbp10_dir: Path, prebuilt_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[pd.DataFrame] = []
    audit: list[dict[str, Any]] = []
    for path in sorted(mbp10_dir.glob("*.parquet")):
        date_hint = path.name.split("-")[2].split(".")[0]
        prebuilt, meta = _read_prebuilt_day(path, prebuilt_dir)
        if prebuilt is not None and meta is not None:
            minute = _minute_features_from_prebuilt(prebuilt)
        else:
            raw, meta = _read_raw_day(path)
            minute = _minute_features_from_raw(raw) if not raw.empty else pd.DataFrame()
        if minute.empty:
            if meta:
                meta["minute_rows"] = 0
                meta["usable"] = False
                audit.append(meta)
            continue
        minute = _finalize_minute_features(minute, date_hint)
        minute["source_file"] = path.name
        rows.append(minute)
        meta = meta or {"file": path.name, "source": "unknown"}
        meta.update({
            "minute_rows": int(len(minute)),
            "et_date_count": int(minute["et_date"].nunique()),
            "total_depth_median": float(minute["total_depth_10"].median()),
            "usable": bool(len(minute) >= 100),
        })
        audit.append(meta)
    matrix = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return matrix, pd.DataFrame(audit)


def summarize_depth_vol(depth: pd.DataFrame, variable: str = "total_depth_10") -> pd.DataFrame:
    data = depth.dropna(subset=["total_depth_10", "rv_15m_fwd", "rv_30m_fwd"]).copy()
    if variable not in data:
        return pd.DataFrame()
    data = data.dropna(subset=[variable]).copy()
    if data.empty:
        return pd.DataFrame()
    data["depth_quintile"] = pd.qcut(data[variable], 5, labels=False, duplicates="drop") + 1
    rows = []
    for q, group in data.groupby("depth_quintile"):
        rows.append({
            "variable": variable,
            "depth_quintile": int(q),
            "n": int(len(group)),
            "median_depth": float(group["total_depth_10"].median()),
            "median_variable": float(group[variable].median()),
            "rv_15m_mean": float(group["rv_15m_fwd"].mean()),
            "rv_15m_median": float(group["rv_15m_fwd"].median()),
            "rv_30m_mean": float(group["rv_30m_fwd"].mean()),
            "rv_30m_median": float(group["rv_30m_fwd"].median()),
        })
    return pd.DataFrame(rows)


def summarize_by_quantile(data: pd.DataFrame, variable: str, label: str) -> pd.DataFrame:
    cols = [variable, "rv_15m_fwd", "rv_30m_fwd", "abs_move_15m_fwd", "abs_move_30m_fwd"]
    sample = data.dropna(subset=cols).copy()
    if sample.empty or sample[variable].nunique() < 3:
        return pd.DataFrame()
    sample["bucket"] = pd.qcut(sample[variable], 5, labels=False, duplicates="drop") + 1
    rows = []
    for bucket, group in sample.groupby("bucket"):
        rows.append({
            "diagnostic": label,
            "variable": variable,
            "bucket": int(bucket),
            "n": int(len(group)),
            "median_value": float(group[variable].median()),
            "rv_15m_mean": float(group["rv_15m_fwd"].mean()),
            "rv_30m_mean": float(group["rv_30m_fwd"].mean()),
            "abs_move_15m_mean": float(group["abs_move_15m_fwd"].mean()),
            "abs_move_30m_mean": float(group["abs_move_30m_fwd"].mean()),
        })
    return pd.DataFrame(rows)


def summarize_phase15(depth: pd.DataFrame) -> dict[str, pd.DataFrame]:
    data = depth.copy().replace([np.inf, -np.inf], np.nan)
    outputs: dict[str, pd.DataFrame] = {}

    # Composite score: deliberately simple and transparent; 1 point per stress condition.
    required = ["depth_z", "depth_chg_30m", "cancel_to_add", "rv_30m_trailing", "sweep_count"]
    score_sample = data.dropna(subset=[*required, "rv_15m_fwd", "rv_30m_fwd"]).copy()
    if not score_sample.empty:
        thresholds = {
            "depth_z_q20": float(score_sample["depth_z"].quantile(0.20)),
            "depth_chg_30m_q20": float(score_sample["depth_chg_30m"].quantile(0.20)),
            "cancel_to_add_q80": float(score_sample["cancel_to_add"].quantile(0.80)),
            "rv_30m_trailing_q80": float(score_sample["rv_30m_trailing"].quantile(0.80)),
            "sweep_count_q80": float(score_sample["sweep_count"].quantile(0.80)),
        }
        score = (
            (score_sample["depth_z"] <= thresholds["depth_z_q20"]).astype(int)
            + (score_sample["depth_chg_30m"] <= thresholds["depth_chg_30m_q20"]).astype(int)
            + (score_sample["cancel_to_add"] >= thresholds["cancel_to_add_q80"]).astype(int)
            + (score_sample["rv_30m_trailing"] >= thresholds["rv_30m_trailing_q80"]).astype(int)
            + (score_sample["sweep_count"] >= thresholds["sweep_count_q80"]).astype(int)
        )
        score_sample["gamma_bid_score"] = score
        rows = []
        for bucket, group in score_sample.groupby("gamma_bid_score"):
            rows.append({
                "gamma_bid_score": int(bucket),
                "n": int(len(group)),
                "rv_15m_mean": float(group["rv_15m_fwd"].mean()),
                "rv_30m_mean": float(group["rv_30m_fwd"].mean()),
                "abs_move_15m_mean": float(group["abs_move_15m_fwd"].mean()),
                "abs_move_30m_mean": float(group["abs_move_30m_fwd"].mean()),
                **thresholds,
            })
        outputs["gamma_bid_score"] = pd.DataFrame(rows)

    thin_cut = data["depth_z"].quantile(0.20)
    thin = data[data["depth_z"] <= thin_cut].dropna(subset=["depth_refill_15m", "rv_30m_fwd"]).copy()
    if not thin.empty:
        refill_cut = thin["depth_refill_15m"].median()
        thin["refill_regime"] = np.where(thin["depth_refill_15m"] >= refill_cut, "fast_refill", "slow_refill")
        rows = []
        for regime, group in thin.groupby("refill_regime"):
            rows.append({
                "refill_regime": regime,
                "n": int(len(group)),
                "median_refill_5m": float(group["depth_refill_5m"].median()),
                "median_refill_15m": float(group["depth_refill_15m"].median()),
                "median_refill_30m": float(group["depth_refill_30m"].median()),
                "rv_30m_mean": float(group["rv_30m_fwd"].mean()),
                "abs_move_30m_mean": float(group["abs_move_30m_fwd"].mean()),
            })
        outputs["liquidity_replenishment"] = pd.DataFrame(rows)

    outputs["imbalance_persistence"] = pd.concat(
        [
            summarize_by_quantile(data, "depth_imb_10", "snapshot_imbalance"),
            summarize_by_quantile(data, "imb_persist_5m", "persistent_imbalance_5m"),
            summarize_by_quantile(data, "imb_persist_15m", "persistent_imbalance_15m"),
        ],
        ignore_index=True,
    )
    outputs["top_vs_deep"] = summarize_by_quantile(data, "top_depth_share", "top_depth_share")

    sweep = data.dropna(subset=["sweep_count", "signed_trade_1m", "mid"]).copy()
    sweep = sweep[sweep["sweep_count"] > 0].copy()
    if not sweep.empty:
        direction = np.sign(sweep["signed_trade_1m"]).replace(0.0, np.nan)
        sweep["dir_move_5m"] = direction * (sweep["mid"].shift(-5) - sweep["mid"])
        sweep["dir_move_15m"] = direction * (sweep["mid"].shift(-15) - sweep["mid"])
        sweep["sweep_size_bucket"] = pd.qcut(sweep["sweep_count"], 3, labels=["small", "medium", "large"], duplicates="drop")
        rows = []
        for bucket, group in sweep.dropna(subset=["sweep_size_bucket", "dir_move_5m", "dir_move_15m"]).groupby("sweep_size_bucket", observed=True):
            rows.append({
                "sweep_size_bucket": str(bucket),
                "n": int(len(group)),
                "continuation_5m_rate": float((group["dir_move_5m"] > 0).mean()),
                "continuation_15m_rate": float((group["dir_move_15m"] > 0).mean()),
                "avg_dir_move_5m": float(group["dir_move_5m"].mean()),
                "avg_dir_move_15m": float(group["dir_move_15m"].mean()),
            })
        outputs["sweep_absorption"] = pd.DataFrame(rows)

    div = data.dropna(subset=["quote_ofi_1m", "signed_trade_1m", "mid"]).copy()
    if not div.empty:
        ofi_sign = np.sign(div["quote_ofi_1m"])
        trade_sign = np.sign(div["signed_trade_1m"])
        conditions = [
            (ofi_sign != 0) & (trade_sign != 0) & (ofi_sign == trade_sign),
            (ofi_sign != 0) & (trade_sign != 0) & (ofi_sign != trade_sign),
            (ofi_sign != 0) & (trade_sign == 0),
            (ofi_sign == 0) & (trade_sign != 0),
        ]
        labels = ["agree", "diverge", "book_only", "trade_only"]
        div["ofi_trade_regime"] = np.select(conditions, labels, default="quiet")
        direction = trade_sign.where(trade_sign != 0, ofi_sign).replace(0.0, np.nan)
        div["dir_move_15m"] = direction * (div["mid"].shift(-15) - div["mid"])
        rows = []
        for regime, group in div.dropna(subset=["dir_move_15m"]).groupby("ofi_trade_regime"):
            rows.append({
                "ofi_trade_regime": regime,
                "n": int(len(group)),
                "directional_hit_15m": float((group["dir_move_15m"] > 0).mean()),
                "avg_dir_move_15m": float(group["dir_move_15m"].mean()),
                "rv_15m_mean": float(group["rv_15m_fwd"].mean()),
            })
        outputs["ofi_trade_divergence"] = pd.DataFrame(rows)

    return outputs


def _z_by_day(frame: pd.DataFrame) -> pd.Series:
    return frame.groupby("et_date")["residual"].transform(
        lambda x: (x - x.mean()) / (x.std(ddof=1) if x.std(ddof=1) else np.nan)
    )


def _episodes_from_predictions(predictions: pd.DataFrame, threshold: float = 2.0, horizon: int = 30) -> pd.DataFrame:
    ridge = predictions[predictions["model"] == "ridge"].copy()
    ridge["bucket_ts"] = pd.to_datetime(ridge["bucket_ts"], utc=True)
    ridge.sort_values(["et_date", "bucket_ts"], inplace=True)
    ridge["residual_z"] = _z_by_day(ridge)
    episodes = []
    for _, day in ridge.groupby("et_date"):
        day = day.dropna(subset=["residual_z", "price", "fair_value"]).reset_index(drop=True)
        active = False
        for i, row in day.iterrows():
            z = abs(float(row["residual_z"]))
            if z >= threshold and not active:
                direction = -1.0 if float(row["residual"]) > 0 else 1.0
                window = day.iloc[i : i + horizon + 1].copy()
                if len(window) < horizon + 1:
                    active = True
                    continue
                pnl_path = direction * (window["price"].astype(float) - float(row["price"]))
                resolved = bool((window["residual_z"].abs() < 1.0).any())
                episodes.append({
                    "bucket_ts": row["bucket_ts"],
                    "et_date": row["et_date"],
                    "open_price": float(row["price"]),
                    "open_residual": float(row["residual"]),
                    "open_residual_z": float(row["residual_z"]),
                    "direction_sign": direction,
                    "reversion_hit_30m": resolved,
                    "dir_pnl_30m": float(pnl_path.iloc[-1]),
                    "adverse_30m": float(pnl_path.min()),
                    "max_abs_z_30m": float(window["residual_z"].abs().max()),
                })
                active = True
            elif z < 1.0:
                active = False
    return pd.DataFrame(episodes)


def summarize_signal_conditioning(depth: pd.DataFrame, predictions_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not predictions_path.exists() or depth.empty:
        return pd.DataFrame(), pd.DataFrame()
    pred = pd.read_csv(predictions_path)
    episodes = _episodes_from_predictions(pred)
    if episodes.empty:
        return episodes, pd.DataFrame()
    dcols = ["bucket_ts", "total_depth_10", "depth_imb_10", "depth_z", "depth_chg_5m", "quote_ofi_1m", "cancel_to_add", "sweep_count"]
    d = depth[dcols].copy()
    d["bucket_ts"] = pd.to_datetime(d["bucket_ts"], utc=True)
    merged = episodes.merge(d, on="bucket_ts", how="inner")
    if merged.empty:
        return merged, pd.DataFrame()
    cutoff = merged["total_depth_10"].quantile(0.2)
    merged["depth_regime"] = np.where(merged["total_depth_10"] <= cutoff, "thin_bottom_20pct", "normal")
    rows = []
    for regime, group in merged.groupby("depth_regime"):
        rows.append({
            "depth_regime": regime,
            "n": int(len(group)),
            "median_depth": float(group["total_depth_10"].median()),
            "reversion_rate_30m": float(group["reversion_hit_30m"].mean()),
            "directional_hit_rate_30m": float((group["dir_pnl_30m"] > 0).mean()),
            "avg_dir_pnl_30m": float(group["dir_pnl_30m"].mean()),
            "median_dir_pnl_30m": float(group["dir_pnl_30m"].median()),
            "avg_adverse_30m": float(group["adverse_30m"].mean()),
            "median_adverse_30m": float(group["adverse_30m"].median()),
            "max_abs_z_30m_mean": float(group["max_abs_z_30m"].mean()),
        })
    return merged, pd.DataFrame(rows)


def model_lift(depth: pd.DataFrame, feature_matrix_path: Path) -> pd.DataFrame:
    if not feature_matrix_path.exists() or depth.empty:
        return pd.DataFrame()
    base = pd.read_csv(feature_matrix_path, parse_dates=["bucket_ts"])
    base["bucket_ts"] = pd.to_datetime(base["bucket_ts"], utc=True)
    d = depth[["bucket_ts", *DEPTH_FEATURES]].copy()
    d["bucket_ts"] = pd.to_datetime(d["bucket_ts"], utc=True)
    merged = base.merge(d, on="bucket_ts", how="inner").replace([np.inf, -np.inf], np.nan)
    depth_features = [col for col in DEPTH_FEATURES if col in merged and merged[col].notna().mean() > 0.5]
    base_features = [col for col in FEATURE_SET if col in merged]
    common_required = ["target_price_delta", *base_features, *depth_features]
    common = merged.dropna(subset=common_required).copy()
    rows = []
    dates = sorted(common["et_date"].dropna().unique())
    min_train_days = min(5, max(2, len(dates) // 3))
    for feature_name, cols in [("current", base_features), ("current_plus_depth", [*base_features, *depth_features])]:
        preds = []
        actuals = []
        test_dates = []
        for i in range(min_train_days, len(dates)):
            train_dates = dates[:i]
            test_date = dates[i]
            train = common[common["et_date"].isin(train_dates)].copy()
            test = common[common["et_date"] == test_date].copy()
            if len(train) < 1000 or len(test) < 100:
                continue
            model = Pipeline([
                ("scale", StandardScaler()),
                ("model", RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0, 100.0])),
            ])
            model.fit(train[cols].astype(float), train["target_price_delta"].astype(float))
            pred = model.predict(test[cols].astype(float))
            preds.extend(pred.tolist())
            actuals.extend(test["target_price_delta"].astype(float).tolist())
            test_dates.append(test_date)
        if actuals:
            rmse = float(mean_squared_error(actuals, preds) ** 0.5)
            r2 = float(r2_score(actuals, preds))
        else:
            rmse = np.nan
            r2 = np.nan
        rows.append({
            "feature_set": feature_name,
            "features": len(cols),
            "depth_features_used": ",".join(depth_features) if feature_name == "current_plus_depth" else "",
            "test_dates": len(set(test_dates)),
            "test_rows": len(actuals),
            "common_sample_rows": int(len(common)),
            "oos_rmse": rmse,
            "oos_r2": r2,
        })
    return pd.DataFrame(rows)


def write_summary(
    path: Path,
    audit: pd.DataFrame,
    depth_vol: pd.DataFrame,
    signal_summary: pd.DataFrame,
    model_summary: pd.DataFrame,
    phase15: dict[str, pd.DataFrame],
    artifacts: dict[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    usable = audit[audit.get("usable", False) == True] if not audit.empty else pd.DataFrame()
    lines = [
        "# Aggression V2 Depth Research",
        "",
        "## Scope",
        "",
        "Research-only MBP-10 study for the V3 market-depth plan. No V1/V2 dashboard code was changed.",
        "",
        "## Data Coverage",
        "",
        f"- MBP-10 files inspected: {len(audit)}",
        f"- Usable files (>=100 one-minute rows): {len(usable)}",
    ]
    if not audit.empty:
        lines.extend([
            f"- UTC span: {audit['start_utc'].dropna().min()} to {audit['end_utc'].dropna().max()}",
            f"- Sources used: {', '.join(sorted(audit['source'].dropna().unique()))}",
            "",
            "Known limitations: ZN only, 26 calendar files, many days are April 2026 only, and this is much shorter than the TBBO model sample. Model integration should remain gated.",
        ])
    lines.extend(["", "## Test 1: Depth Disappears / Gamma Bids", ""])
    if depth_vol.empty:
        lines.append("No depth/vol result was produced.")
    else:
        for variable, table in depth_vol.groupby("variable"):
            lines.append(f"\n### Quintiles by `{variable}`\n")
            lines.append(table.to_markdown(index=False))
            low = table.sort_values("depth_quintile").head(1)
            high = table.sort_values("depth_quintile").tail(1)
            if not low.empty and not high.empty:
                rv_ratio = float(low["rv_30m_mean"].iloc[0] / high["rv_30m_mean"].iloc[0]) if high["rv_30m_mean"].iloc[0] else np.nan
                lines.append(f"\nBottom-vs-top quintile 30m realized-vol ratio: `{rv_ratio:.2f}x`.")
    lines.extend(["", "## Test 2: Depth-Conditioned V2 Dislocations", ""])
    lines.append(signal_summary.to_markdown(index=False) if not signal_summary.empty else "No overlapping dislocation episodes found.")
    lines.extend(["", "## Test 3: Fair-Value Lift", ""])
    lines.append(model_summary.to_markdown(index=False) if not model_summary.empty else "No overlapping model sample found.")
    lines.extend(["", "## Phase 1.5: Extra MBP-10 Diagnostics", ""])
    if phase15:
        labels = {
            "gamma_bid_score": "Composite Gamma-Bid Risk Score",
            "liquidity_replenishment": "Liquidity Replenishment After Thin Depth",
            "imbalance_persistence": "Imbalance Persistence",
            "sweep_absorption": "Sweep / Absorption",
            "ofi_trade_divergence": "OFI vs Trade Aggression Divergence",
            "top_vs_deep": "Top-of-Book vs Deep-Book Concentration",
        }
        for key, title in labels.items():
            table = phase15.get(key, pd.DataFrame())
            lines.extend(["", f"### {title}", ""])
            lines.append(table.to_markdown(index=False) if not table.empty else "No usable rows.")
    else:
        lines.append("No Phase 1.5 diagnostics were produced.")
    lines.extend([
        "",
        "## Artifacts",
        "",
        *[f"- `{name}`: `{value}`" for name, value in artifacts.items()],
        "",
        "## Recommendation",
        "",
        "Review the tables above before any V3 implementation. A live depth panel is safe as display-only/risk context, but depth/OFI should not enter the model until the short ZN-only MBP-10 sample is accepted as sufficient or expanded.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Research MBP-10 depth features for Aggression V2/V3")
    parser.add_argument("--mbp10-dir", type=Path, default=DEFAULT_MBP10_DIR)
    parser.add_argument("--prebuilt-dir", type=Path, default=DEFAULT_PREBUILT_DIR)
    parser.add_argument("--feature-matrix", type=Path, default=DEFAULT_FEATURE_MATRIX)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    depth, audit = build_depth_matrix(args.mbp10_dir, args.prebuilt_dir)
    if depth.empty:
        raise SystemExit("No usable MBP-10 depth rows found.")

    depth_path = args.out / "depth_feature_matrix.csv"
    audit_path = args.out / "depth_data_audit.csv"
    depth_vol_path = args.out / "depth_vol_summary.csv"
    episodes_path = args.out / "depth_conditioned_episodes.csv"
    signal_summary_path = args.out / "depth_conditioned_signal_summary.csv"
    model_summary_path = args.out / "depth_model_lift_summary.csv"
    summary_json_path = args.out / "depth_research_summary.json"
    phase15_dir = args.out / "phase15"
    phase15_dir.mkdir(parents=True, exist_ok=True)

    depth.to_csv(depth_path, index=False)
    audit.to_csv(audit_path, index=False)
    depth_vol = pd.concat(
        [
            summarize_depth_vol(depth, "total_depth_10"),
            summarize_depth_vol(depth, "depth_z"),
            summarize_depth_vol(depth, "depth_chg_30m"),
        ],
        ignore_index=True,
    )
    depth_vol.to_csv(depth_vol_path, index=False)
    episodes, signal_summary = summarize_signal_conditioning(depth, args.predictions)
    episodes.to_csv(episodes_path, index=False)
    signal_summary.to_csv(signal_summary_path, index=False)
    model_summary = model_lift(depth, args.feature_matrix)
    model_summary.to_csv(model_summary_path, index=False)
    phase15 = summarize_phase15(depth)
    phase15_paths: dict[str, str] = {}
    for name, table in phase15.items():
        out_path = phase15_dir / f"{name}.csv"
        table.to_csv(out_path, index=False)
        phase15_paths[f"phase15_{name}"] = str(out_path)

    summary = {
        "depth_rows": int(len(depth)),
        "audit_rows": int(len(audit)),
        "usable_files": int(audit["usable"].sum()) if "usable" in audit else 0,
        "depth_vol_rows": int(len(depth_vol)),
        "signal_episode_rows": int(len(episodes)),
        "model_lift_rows": int(len(model_summary)),
        "phase15_tables": {name: int(len(table)) for name, table in phase15.items()},
        "artifacts": {
            "depth_feature_matrix": str(depth_path),
            "depth_data_audit": str(audit_path),
            "depth_vol_summary": str(depth_vol_path),
            "depth_conditioned_episodes": str(episodes_path),
            "depth_conditioned_signal_summary": str(signal_summary_path),
            "depth_model_lift_summary": str(model_summary_path),
            **phase15_paths,
        },
    }
    summary_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_summary(
        args.doc,
        audit,
        depth_vol,
        signal_summary,
        model_summary,
        phase15,
        {**summary["artifacts"], "summary_json": str(summary_json_path)},
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
