#!/usr/bin/env python3
"""Research-only TBBO feature audit for aggression dashboard V2."""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
import pyarrow.ipc as pa_ipc

from scripts.aggression_dashboard.core import EASTERN, SYMBOL_PARENT_MAP, classify_aggressor, load_dv01_table


API_BASE = "http://100.79.30.21:8001"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo"
TRADE_AUDIT_SUMMARY = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2" / "feature_audit_summary.json"
DEFAULT_BUCKET = "1min"
DEFAULT_PRICE_SYMBOL = "TY"
SESSION_START = time(1, 0)
SESSION_END = time(17, 0)
LARGE_TRADE_SIZE = 100
FEATURE_COLUMNS = [
    "agg_cum_daily",
    "agg_1m",
    "agg_5m",
    "agg_30m",
    "agg_rate",
    "agg_accel",
    "buysell_ratio",
    "trade_intensity",
    "vol_per_min",
    "avg_trade_size",
    "large_trade_share",
    "realized_vol",
    "mom_5m",
    "FV_agg_5m",
    "TU_agg_5m",
    "TY_agg_5m",
    "US_agg_5m",
    "spread_last",
    "spread_mean",
    "size_imbalance_last",
    "size_imbalance_mean",
    "microprice_dev_last",
    "microprice_dev_mean",
    "displayed_depth_last",
    "displayed_depth_mean",
    "trade_size_vs_depth",
    "depth_adjusted_signed_flow",
    "minutes_since_open",
    "session_sin",
    "session_cos",
]


def _session_bounds(date_text: str) -> tuple[datetime, datetime]:
    date_value = datetime.strptime(date_text, "%Y-%m-%d").date()
    start_et = datetime.combine(date_value, SESSION_START, tzinfo=EASTERN)
    end_et = datetime.combine(date_value, SESSION_END, tzinfo=EASTERN)
    return start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)


def _default_dates() -> list[str]:
    if TRADE_AUDIT_SUMMARY.exists():
        raw = json.loads(TRADE_AUDIT_SUMMARY.read_text(encoding="utf-8"))
        rows = raw.get("coverage_by_date") or []
        dates = [row["et_date"] for row in rows if int(row.get("rows") or 0) >= 500]
        return dates
    return ["2026-05-20", "2026-05-21", "2026-05-22", "2026-05-27", "2026-05-28", "2026-05-29", "2026-06-02"]


def _fetch_tbbo_arrow(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    api_base: str,
    timeout: int,
) -> pd.DataFrame:
    query = urllib.parse.urlencode({
        "source": "database",
        "schema": "tbbo",
        "symbols": symbol,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "format": "arrow",
    })
    url = f"{api_base.rstrip('/')}/historical/trades?{query}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read()
    table = pa_ipc.open_stream(io.BytesIO(raw)).read_all()
    if table.num_rows == 0:
        return pd.DataFrame()
    df = table.to_pandas()
    df["dashboard_symbol"] = symbol
    return df


def _clean_tbbo(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["ts_event"] = pd.to_datetime(out["ts_event"], utc=True)
    for column in ("price", "size", "bid_price", "ask_price", "bid_size", "ask_size"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out[
        (out["price"] > 80)
        & (out["price"] < 150)
        & (out["size"] > 0)
        & (out["bid_price"] > 0)
        & (out["ask_price"] > 0)
        & (out["ask_price"] >= out["bid_price"])
        & (out["bid_size"] > 0)
        & (out["ask_size"] > 0)
    ].copy()
    out.sort_values("ts_event", inplace=True)
    return out


def _bucket_symbol(symbol: str, df: pd.DataFrame, dv01: float, bucket: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = _clean_tbbo(df)
    if work.empty:
        return pd.DataFrame()
    work["sign"] = work["side"].apply(classify_aggressor)
    work["signed_flow"] = work["sign"] * work["size"] * dv01
    work["buy_flow"] = (work["sign"] == 1).astype(float) * work["size"] * dv01
    work["sell_flow"] = (work["sign"] == -1).astype(float) * work["size"] * dv01
    work["large_volume"] = np.where(work["size"] >= LARGE_TRADE_SIZE, work["size"], 0)
    work["spread"] = work["ask_price"] - work["bid_price"]
    denom = work["bid_size"] + work["ask_size"]
    work["size_imbalance"] = (work["bid_size"] - work["ask_size"]) / denom
    work["microprice"] = (work["ask_price"] * work["bid_size"] + work["bid_price"] * work["ask_size"]) / denom
    work["mid"] = (work["bid_price"] + work["ask_price"]) / 2.0
    work["microprice_dev"] = work["microprice"] - work["mid"]
    work["displayed_depth"] = denom
    work["trade_size_vs_depth"] = work["size"] / denom
    work["depth_adjusted_signed_flow"] = work["signed_flow"] / denom
    work.set_index("ts_event", inplace=True)
    grouped = pd.DataFrame({
        f"{symbol}_signed_flow": work["signed_flow"].resample(bucket).sum().fillna(0.0),
        f"{symbol}_buy_flow": work["buy_flow"].resample(bucket).sum().fillna(0.0),
        f"{symbol}_sell_flow": work["sell_flow"].resample(bucket).sum().fillna(0.0),
        f"{symbol}_contracts": work["size"].resample(bucket).sum().fillna(0.0),
        f"{symbol}_trade_count": work["size"].resample(bucket).count().fillna(0.0),
        f"{symbol}_avg_trade_size": work["size"].resample(bucket).mean(),
        f"{symbol}_large_volume": work["large_volume"].resample(bucket).sum().fillna(0.0),
    })
    if symbol == DEFAULT_PRICE_SYMBOL:
        grouped["price"] = work["price"].resample(bucket).last().ffill()
        quote_agg = work[[
            "spread",
            "size_imbalance",
            "microprice_dev",
            "displayed_depth",
            "trade_size_vs_depth",
            "depth_adjusted_signed_flow",
        ]].resample(bucket).agg(["last", "mean"])
        quote_agg.columns = [f"{name}_{agg}" for name, agg in quote_agg.columns]
        grouped = grouped.join(quote_agg)
        grouped["trade_size_vs_depth"] = grouped["trade_size_vs_depth_mean"]
        grouped["depth_adjusted_signed_flow"] = grouped["depth_adjusted_signed_flow_mean"]
    return grouped


def _build_day_matrix(date_text: str, *, api_base: str, timeout: int, bucket: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    start, end = _session_bounds(date_text)
    dv01_table = load_dv01_table()
    frames: list[pd.DataFrame] = []
    fetch_meta: dict[str, Any] = {"date": date_text, "symbols": {}}
    for symbol in SYMBOL_PARENT_MAP:
        try:
            raw = _fetch_tbbo_arrow(symbol=symbol, start=start, end=end, api_base=api_base, timeout=timeout)
            clean = _clean_tbbo(raw)
            fetch_meta["symbols"][symbol] = {
                "raw_rows": int(len(raw)),
                "clean_rows": int(len(clean)),
                "raw_symbols": sorted(clean["symbol"].dropna().astype(str).unique().tolist()) if not clean.empty else [],
                "min_ts": clean["ts_event"].min().isoformat() if not clean.empty else None,
                "max_ts": clean["ts_event"].max().isoformat() if not clean.empty else None,
            }
            bucketed = _bucket_symbol(symbol, clean, float(dv01_table.get(symbol, 1.0)), bucket)
            if not bucketed.empty:
                frames.append(bucketed)
        except Exception as exc:
            fetch_meta["symbols"][symbol] = {"error": str(exc)}
    if not frames:
        return pd.DataFrame(), fetch_meta
    matrix = pd.concat(frames, axis=1).sort_index()
    signed_cols = [f"{symbol}_signed_flow" for symbol in SYMBOL_PARENT_MAP]
    buy_cols = [f"{symbol}_buy_flow" for symbol in SYMBOL_PARENT_MAP]
    sell_cols = [f"{symbol}_sell_flow" for symbol in SYMBOL_PARENT_MAP]
    contract_cols = [f"{symbol}_contracts" for symbol in SYMBOL_PARENT_MAP]
    count_cols = [f"{symbol}_trade_count" for symbol in SYMBOL_PARENT_MAP]
    avg_size_cols = [f"{symbol}_avg_trade_size" for symbol in SYMBOL_PARENT_MAP]
    large_cols = [f"{symbol}_large_volume" for symbol in SYMBOL_PARENT_MAP]
    zero_fill_cols = signed_cols + buy_cols + sell_cols + contract_cols + count_cols + large_cols
    for column in zero_fill_cols:
        if column in matrix:
            matrix[column] = matrix[column].fillna(0.0)
    for column in avg_size_cols:
        if column in matrix:
            matrix[column] = matrix[column].fillna(0.0)
    quote_cols = [
        "price",
        "spread_last",
        "spread_mean",
        "size_imbalance_last",
        "size_imbalance_mean",
        "microprice_dev_last",
        "microprice_dev_mean",
        "displayed_depth_last",
        "displayed_depth_mean",
    ]
    for column in quote_cols:
        if column in matrix:
            matrix[column] = matrix[column].ffill()
    for column in ("trade_size_vs_depth", "depth_adjusted_signed_flow"):
        if column in matrix:
            matrix[column] = matrix[column].fillna(0.0)
    if "price" in matrix:
        matrix = matrix[matrix["price"].notna()].copy()

    matrix["agg_1m"] = matrix.reindex(columns=signed_cols, fill_value=0.0).sum(axis=1)
    matrix["agg_5m"] = matrix["agg_1m"].rolling(5, min_periods=1).sum()
    matrix["agg_30m"] = matrix["agg_1m"].rolling(30, min_periods=1).sum()
    matrix["agg_cum_daily"] = matrix["agg_1m"].cumsum()
    matrix["agg_cum_daily"] = matrix["agg_cum_daily"] - matrix["agg_cum_daily"].iloc[0]
    matrix["agg_rate"] = matrix["agg_cum_daily"].diff().fillna(0.0)
    matrix["agg_accel"] = matrix["agg_rate"].diff().fillna(0.0)
    buy_flow = matrix.reindex(columns=buy_cols, fill_value=0.0).sum(axis=1)
    sell_flow = matrix.reindex(columns=sell_cols, fill_value=0.0).sum(axis=1)
    denom = buy_flow + sell_flow
    matrix["buysell_ratio"] = (100.0 * matrix["agg_1m"] / denom).where(denom != 0).ewm(
        span=10, ignore_na=True, min_periods=1
    ).mean()
    matrix["trade_intensity"] = matrix.reindex(columns=count_cols, fill_value=0.0).sum(axis=1)
    matrix["vol_per_min"] = matrix.reindex(columns=contract_cols, fill_value=0.0).sum(axis=1)
    avg_numer = matrix.reindex(columns=avg_size_cols, fill_value=np.nan).multiply(
        matrix.reindex(columns=count_cols, fill_value=0.0).to_numpy()
    ).sum(axis=1)
    matrix["avg_trade_size"] = (avg_numer / matrix["trade_intensity"].replace(0.0, np.nan)).fillna(0.0)
    large_volume = matrix.reindex(columns=large_cols, fill_value=0.0).sum(axis=1)
    matrix["large_trade_share"] = (large_volume / matrix["vol_per_min"].replace(0.0, np.nan)).fillna(0.0)
    for symbol in SYMBOL_PARENT_MAP:
        col = f"{symbol}_signed_flow"
        matrix[f"{symbol}_agg_5m"] = matrix.get(col, pd.Series(0.0, index=matrix.index)).rolling(5, min_periods=1).sum()
    matrix["price_return_1m"] = matrix["price"].diff()
    matrix["realized_vol"] = matrix["price_return_1m"].rolling(30, min_periods=5).std()
    matrix["mom_5m"] = matrix["price"] - matrix["price"].shift(5)
    idx_et = pd.DatetimeIndex(matrix.index).tz_convert(EASTERN)
    matrix["et_date"] = date_text
    matrix["day_open_price"] = matrix["price"].iloc[0]
    matrix["target_price_delta"] = matrix["price"] - matrix["day_open_price"]
    minutes = idx_et.hour * 60 + idx_et.minute - 60
    matrix["minutes_since_open"] = minutes
    matrix["session_sin"] = np.sin(2 * np.pi * minutes / (24 * 60))
    matrix["session_cos"] = np.cos(2 * np.pi * minutes / (24 * 60))
    cols = ["price", "day_open_price", "target_price_delta", "et_date", *FEATURE_COLUMNS]
    return matrix.reindex(columns=cols), fetch_meta


def _summarize(matrix: pd.DataFrame, fetch_meta: list[dict[str, Any]], dates: list[str]) -> dict[str, Any]:
    if matrix.empty:
        return {"ok": False, "reason": "No TBBO rows returned.", "fetch_meta": fetch_meta}
    feature_frame = matrix[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    missing = feature_frame.isna().mean().sort_values(ascending=False)
    corr = feature_frame.corr()
    high_corr = []
    for i, left in enumerate(corr.columns):
        for right in corr.columns[i + 1:]:
            value = corr.loc[left, right]
            if pd.notna(value) and abs(value) >= 0.90:
                high_corr.append({"left": left, "right": right, "corr": float(value)})
    coverage = matrix.groupby("et_date").agg(
        rows=("price", "count"),
        price_min=("price", "min"),
        price_max=("price", "max"),
        target_std=("target_price_delta", "std"),
        avg_spread=("spread_mean", "mean"),
        avg_depth=("displayed_depth_mean", "mean"),
    )
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(matrix)),
        "dates_requested": dates,
        "dates_returned": sorted(matrix["et_date"].dropna().unique().tolist()),
        "start": matrix.index.min().isoformat(),
        "end": matrix.index.max().isoformat(),
        "features": FEATURE_COLUMNS,
        "missing_fraction": {k: float(v) for k, v in missing.items()},
        "coverage_by_date": coverage.reset_index().to_dict(orient="records"),
        "high_abs_corr_pairs": sorted(high_corr, key=lambda item: abs(item["corr"]), reverse=True),
        "fetch_meta": fetch_meta,
    }


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    dates = args.dates.split(",") if args.dates else _default_dates()
    if args.max_dates:
        dates = dates[-int(args.max_dates):]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    matrices: list[pd.DataFrame] = []
    fetch_meta: list[dict[str, Any]] = []
    for date_text in dates:
        matrix, meta = _build_day_matrix(
            date_text.strip(),
            api_base=args.api_base,
            timeout=int(args.timeout),
            bucket=args.bucket,
        )
        fetch_meta.append(meta)
        if not matrix.empty:
            matrices.append(matrix)
    combined = pd.concat(matrices).sort_index() if matrices else pd.DataFrame()
    summary = _summarize(combined, fetch_meta, dates)
    feature_path = output_dir / "tbbo_feature_matrix.csv"
    corr_path = output_dir / "tbbo_feature_correlation.csv"
    summary_path = output_dir / "tbbo_feature_audit_summary.json"
    if not combined.empty:
        combined.to_csv(feature_path, index_label="bucket_ts")
        combined[FEATURE_COLUMNS].corr().to_csv(corr_path)
    summary["artifacts"] = {
        "feature_matrix": str(feature_path) if not combined.empty else None,
        "correlation": str(corr_path) if not combined.empty else None,
        "summary": str(summary_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit TBBO-backed aggression V2 features.")
    parser.add_argument("--api-base", default=API_BASE)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--dates", default="", help="Comma-separated ET dates YYYY-MM-DD. Defaults to trade audit dates.")
    parser.add_argument("--max-dates", type=int, default=0, help="Use only the last N default dates.")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    summary = run_audit(args)
    print(json.dumps({
        "ok": summary.get("ok"),
        "rows": summary.get("rows"),
        "dates_returned": summary.get("dates_returned"),
        "artifacts": summary.get("artifacts"),
        "top_missing": dict(list((summary.get("missing_fraction") or {}).items())[:8]),
        "high_corr_count": len(summary.get("high_abs_corr_pairs") or []),
    }, indent=2))


if __name__ == "__main__":
    main()
