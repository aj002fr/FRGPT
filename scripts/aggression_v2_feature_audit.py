#!/usr/bin/env python3
"""Research-only feature audit for a future aggression dashboard V2."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from scripts.aggression_dashboard.core import (
    CACHE_DIR,
    EASTERN,
    SYMBOL_PARENT_MAP,
    _select_front_month_per_day,
    classify_aggressor,
    load_dv01_table,
    price_series_from_trades,
)


OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2"
DEFAULT_PRICE_SYMBOL = "TY"
DEFAULT_BUCKET = "1min"
LARGE_TRADE_SIZE = 100
PARENT_SAFE = {symbol: parent.replace(".", "_") for symbol, parent in SYMBOL_PARENT_MAP.items()}
FEATURE_COLUMNS = [
    "agg_cum_daily",
    "agg_1m",
    "agg_5m",
    "agg_30m",
    "agg_rate",
    "agg_accel",
    "buysell_ratio_raw",
    "buysell_ratio",
    "trade_intensity",
    "vol_per_min",
    "avg_trade_size",
    "large_trade_share",
    "signed_streak",
    "realized_vol",
    "mom_5m",
    "FV_agg_5m",
    "TU_agg_5m",
    "TY_agg_5m",
    "US_agg_5m",
    "minutes_since_open",
    "session_sin",
    "session_cos",
]


def _cache_files_by_symbol(cache_dir: Path = CACHE_DIR) -> Dict[str, List[Path]]:
    files: Dict[str, List[Path]] = {}
    for symbol, safe_parent in PARENT_SAFE.items():
        files[symbol] = sorted(cache_dir.glob(f"trades_v2_{safe_parent}_*.parquet"))
    return files


def _date_from_cache_path(path: Path) -> str:
    return path.stem.rsplit("_", 1)[-1]


def _load_symbol_trades(symbol: str, paths: Iterable[Path]) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    frames: List[pd.DataFrame] = []
    contracts: List[Dict[str, Any]] = []
    required = {"ts", "price", "size", "side", "instrument_id"}

    for path in paths:
        try:
            raw = pd.read_parquet(path)
        except Exception as exc:
            contracts.append({"symbol": symbol, "path": str(path), "error": str(exc)})
            continue
        missing = sorted(required - set(raw.columns))
        if missing:
            contracts.append({"symbol": symbol, "path": str(path), "missing_columns": missing})
            continue
        df = raw.copy()
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df = df[(df["price"] > 80) & (df["price"] < 150) & (df["size"] > 0)].copy()
        if df.empty:
            continue
        df, front_contracts = _select_front_month_per_day(df)
        df["dashboard_symbol"] = symbol
        frames.append(df)
        contracts.extend({"symbol": symbol, **row} for row in front_contracts)

    if not frames:
        return pd.DataFrame(columns=["ts", "price", "size", "side", "instrument_id", "raw_symbol", "dashboard_symbol"]), contracts
    combined = pd.concat(frames, ignore_index=True)
    combined["ts"] = pd.to_datetime(combined["ts"], utc=True)
    combined.sort_values("ts", inplace=True)
    combined.drop_duplicates(subset=["ts", "price", "size", "side", "instrument_id", "dashboard_symbol"], inplace=True)
    combined.reset_index(drop=True, inplace=True)
    return combined, contracts


def _bucket_symbol_trades(symbol: str, trades: pd.DataFrame, dv01: float, bucket: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    df = trades.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["sign"] = df["side"].apply(classify_aggressor)
    df["signed_flow"] = df["sign"] * df["size"] * dv01
    df["buy_flow"] = (df["sign"] == 1).astype(float) * df["size"] * dv01
    df["sell_flow"] = (df["sign"] == -1).astype(float) * df["size"] * dv01
    df["large_volume"] = np.where(df["size"] >= LARGE_TRADE_SIZE, df["size"], 0)
    df.set_index("ts", inplace=True)
    grouped = pd.DataFrame({
        f"{symbol}_signed_flow": df["signed_flow"].resample(bucket).sum().fillna(0.0),
        f"{symbol}_buy_flow": df["buy_flow"].resample(bucket).sum().fillna(0.0),
        f"{symbol}_sell_flow": df["sell_flow"].resample(bucket).sum().fillna(0.0),
        f"{symbol}_contracts": df["size"].resample(bucket).sum().fillna(0.0),
        f"{symbol}_trade_count": df["size"].resample(bucket).count().fillna(0.0),
        f"{symbol}_avg_trade_size": df["size"].resample(bucket).mean(),
        f"{symbol}_large_volume": df["large_volume"].resample(bucket).sum().fillna(0.0),
    })
    return grouped


def _signed_streak(sign_series: pd.Series) -> pd.Series:
    streak = []
    current = 0
    prev = 0
    for value in sign_series.fillna(0):
        sign = int(np.sign(value))
        if sign == 0:
            current = 0
        elif sign == prev:
            current += sign
        else:
            current = sign
        streak.append(current)
        prev = sign
    return pd.Series(streak, index=sign_series.index, dtype=float)


def build_feature_matrix(
    *,
    price_symbol: str = DEFAULT_PRICE_SYMBOL,
    bucket: str = DEFAULT_BUCKET,
    cache_dir: Path = CACHE_DIR,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    files = _cache_files_by_symbol(cache_dir)
    available_dates = {
        symbol: sorted(_date_from_cache_path(path) for path in paths)
        for symbol, paths in files.items()
    }
    common_dates = sorted(set.intersection(*(set(v) for v in available_dates.values() if v)))
    selected_paths = {
        symbol: [path for path in paths if _date_from_cache_path(path) in common_dates]
        for symbol, paths in files.items()
    }

    dv01_table = load_dv01_table()
    trades_by_symbol: Dict[str, pd.DataFrame] = {}
    contracts: List[Dict[str, Any]] = []
    for symbol, paths in selected_paths.items():
        trades, symbol_contracts = _load_symbol_trades(symbol, paths)
        trades_by_symbol[symbol] = trades
        contracts.extend(symbol_contracts)

    symbol_buckets = [
        _bucket_symbol_trades(symbol, trades, float(dv01_table.get(symbol, 1.0)), bucket)
        for symbol, trades in trades_by_symbol.items()
        if not trades.empty
    ]
    if not symbol_buckets:
        return pd.DataFrame(), {"error": "No cached trades found.", "available_dates": available_dates}

    features = pd.concat(symbol_buckets, axis=1).sort_index().fillna(0.0)
    signed_cols = [f"{symbol}_signed_flow" for symbol in SYMBOL_PARENT_MAP]
    buy_cols = [f"{symbol}_buy_flow" for symbol in SYMBOL_PARENT_MAP]
    sell_cols = [f"{symbol}_sell_flow" for symbol in SYMBOL_PARENT_MAP]
    contract_cols = [f"{symbol}_contracts" for symbol in SYMBOL_PARENT_MAP]
    count_cols = [f"{symbol}_trade_count" for symbol in SYMBOL_PARENT_MAP]
    avg_size_cols = [f"{symbol}_avg_trade_size" for symbol in SYMBOL_PARENT_MAP]
    large_cols = [f"{symbol}_large_volume" for symbol in SYMBOL_PARENT_MAP]

    features["agg_1m"] = features.reindex(columns=signed_cols, fill_value=0.0).sum(axis=1)
    features["agg_5m"] = features["agg_1m"].rolling(5, min_periods=1).sum()
    features["agg_30m"] = features["agg_1m"].rolling(30, min_periods=1).sum()

    idx = pd.DatetimeIndex(features.index)
    et_dates = pd.Series(idx.tz_convert(EASTERN).date, index=features.index)
    features["agg_cum_daily"] = features["agg_1m"].groupby(et_dates).cumsum()
    features["agg_cum_daily"] = features["agg_cum_daily"] - features["agg_cum_daily"].groupby(et_dates).transform("first")
    features["agg_rate"] = features["agg_cum_daily"].diff().fillna(0.0)
    features["agg_accel"] = features["agg_rate"].diff().fillna(0.0)

    buy_flow = features.reindex(columns=buy_cols, fill_value=0.0).sum(axis=1)
    sell_flow = features.reindex(columns=sell_cols, fill_value=0.0).sum(axis=1)
    denom = buy_flow + sell_flow
    features["buysell_ratio_raw"] = (100.0 * features["agg_1m"] / denom).where(denom != 0)
    features["buysell_ratio"] = features["buysell_ratio_raw"].ewm(span=10, ignore_na=True, min_periods=1).mean()

    features["trade_intensity"] = features.reindex(columns=count_cols, fill_value=0.0).sum(axis=1)
    features["vol_per_min"] = features.reindex(columns=contract_cols, fill_value=0.0).sum(axis=1)
    avg_numer = features.reindex(columns=avg_size_cols, fill_value=np.nan).multiply(
        features.reindex(columns=count_cols, fill_value=0.0).to_numpy()
    ).sum(axis=1)
    features["avg_trade_size"] = (avg_numer / features["trade_intensity"].replace(0.0, np.nan)).fillna(0.0)
    large_volume = features.reindex(columns=large_cols, fill_value=0.0).sum(axis=1)
    features["large_trade_share"] = (large_volume / features["vol_per_min"].replace(0.0, np.nan)).fillna(0.0)
    features["signed_streak"] = _signed_streak(features["agg_1m"])

    for symbol in SYMBOL_PARENT_MAP:
        col = f"{symbol}_signed_flow"
        features[f"{symbol}_agg_5m"] = features.get(col, pd.Series(0.0, index=features.index)).rolling(5, min_periods=1).sum()

    price_df = price_series_from_trades(trades_by_symbol.get(price_symbol, pd.DataFrame()), bucket=bucket)
    if price_df.empty:
        return pd.DataFrame(), {"error": f"No price rows for {price_symbol}.", "available_dates": available_dates}
    features = features.join(price_df.rename(columns={"price": "price"}), how="inner")
    features["price_return_1m"] = features["price"].diff()
    features["realized_vol"] = features["price_return_1m"].rolling(30, min_periods=5).std()
    features["mom_5m"] = features["price"] - features["price"].shift(5)

    idx = pd.DatetimeIndex(features.index)
    et_idx = idx.tz_convert(EASTERN)
    features["et_date"] = [str(value.date()) for value in et_idx]
    common_et_dates = set(pd.to_datetime(common_dates, format="%Y%m%d").strftime("%Y-%m-%d"))
    if common_et_dates:
        features = features[features["et_date"].isin(common_et_dates)].copy()
        idx = pd.DatetimeIndex(features.index)
        et_idx = idx.tz_convert(EASTERN)
    day_open = features.groupby("et_date")["price"].transform("first")
    features["day_open_price"] = day_open
    features["target_price_delta"] = features["price"] - day_open
    minutes = et_idx.hour * 60 + et_idx.minute - 60
    features["minutes_since_open"] = minutes
    features["session_sin"] = np.sin(2 * np.pi * minutes / (24 * 60))
    features["session_cos"] = np.cos(2 * np.pi * minutes / (24 * 60))

    matrix_cols = ["price", "day_open_price", "target_price_delta", "et_date", *FEATURE_COLUMNS]
    matrix = features.reindex(columns=matrix_cols)
    meta = {
        "available_dates": available_dates,
        "common_dates": common_dates,
        "price_symbol": price_symbol,
        "symbols": list(SYMBOL_PARENT_MAP),
        "bucket": bucket,
        "dv01_table": dv01_table,
        "contracts": contracts,
        "cache_dir": str(cache_dir),
    }
    return matrix, meta


def _summarize_matrix(matrix: pd.DataFrame, meta: Dict[str, Any]) -> Dict[str, Any]:
    if matrix.empty:
        return {"ok": False, "reason": meta.get("error", "Feature matrix is empty."), "meta": meta}
    feature_frame = matrix[FEATURE_COLUMNS]
    missing = feature_frame.isna().mean().sort_values(ascending=False)
    coverage_by_date = matrix.groupby("et_date").agg(
        rows=("price", "count"),
        price_min=("price", "min"),
        price_max=("price", "max"),
        target_std=("target_price_delta", "std"),
    )
    finite = feature_frame.replace([np.inf, -np.inf], np.nan)
    corr = finite.corr()
    high_corr = []
    for i, left in enumerate(corr.columns):
        for right in corr.columns[i + 1:]:
            value = corr.loc[left, right]
            if pd.notna(value) and abs(value) >= 0.90:
                high_corr.append({"left": left, "right": right, "corr": float(value)})

    contracts = pd.DataFrame(meta.get("contracts", []))
    contract_summary = []
    if not contracts.empty and {"symbol", "date", "raw_symbol", "volume"}.issubset(contracts.columns):
        for _, row in contracts[["symbol", "date", "raw_symbol", "instrument_id", "volume"]].drop_duplicates().iterrows():
            contract_summary.append({
                "symbol": str(row["symbol"]),
                "date": str(row["date"]),
                "raw_symbol": str(row["raw_symbol"]),
                "instrument_id": str(row["instrument_id"]),
                "volume": int(row["volume"]) if pd.notna(row["volume"]) else None,
            })

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(matrix)),
        "start": matrix.index.min().isoformat(),
        "end": matrix.index.max().isoformat(),
        "business_dates": sorted(matrix["et_date"].dropna().unique().tolist()),
        "feature_count": len(FEATURE_COLUMNS),
        "features": FEATURE_COLUMNS,
        "missing_fraction": {k: float(v) for k, v in missing.items()},
        "coverage_by_date": coverage_by_date.reset_index().to_dict(orient="records"),
        "high_abs_corr_pairs": sorted(high_corr, key=lambda x: abs(x["corr"]), reverse=True),
        "contract_summary": contract_summary,
        "meta": {k: v for k, v in meta.items() if k != "contracts"},
    }


def run_audit(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    matrix, meta = build_feature_matrix(
        price_symbol=args.price_symbol,
        bucket=args.bucket,
        cache_dir=Path(args.cache_dir),
    )
    summary = _summarize_matrix(matrix, meta)
    feature_path = output_dir / "feature_matrix.csv"
    summary_path = output_dir / "feature_audit_summary.json"
    corr_path = output_dir / "feature_correlation.csv"
    if not matrix.empty:
        matrix.to_csv(feature_path, index_label="bucket_ts")
        matrix[FEATURE_COLUMNS].corr().to_csv(corr_path)
    summary["artifacts"] = {
        "feature_matrix": str(feature_path) if not matrix.empty else None,
        "correlation": str(corr_path) if not matrix.empty else None,
        "summary": str(summary_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit aggression V2 feature matrix from cached trades.")
    parser.add_argument("--price-symbol", default=DEFAULT_PRICE_SYMBOL, choices=sorted(SYMBOL_PARENT_MAP))
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--cache-dir", default=str(CACHE_DIR))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    summary = run_audit(args)
    print(json.dumps({
        "ok": summary.get("ok"),
        "rows": summary.get("rows"),
        "start": summary.get("start"),
        "end": summary.get("end"),
        "business_dates": summary.get("business_dates"),
        "artifacts": summary.get("artifacts"),
        "top_missing": dict(list((summary.get("missing_fraction") or {}).items())[:5]),
        "high_corr_count": len(summary.get("high_abs_corr_pairs") or []),
    }, indent=2))


if __name__ == "__main__":
    main()
