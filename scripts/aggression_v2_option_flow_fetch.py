#!/usr/bin/env python3
"""Resumable read-only historical TY option trade fetch and audit.

Streams AccessAPI Arrow batches, immediately filters to TY option trades, and
caches only compact trade prints under the local user profile. No live stores,
dashboards, source databases, ports, or API data are modified.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.ipc as pa_ipc
import requests
import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_BASE = "http://100.79.30.21:8001"
DEFAULT_CACHE = Path(os.environ.get("TY_OPTION_FLOW_CACHE_DIR", Path.home() / ".market_data" / "ty_option_flow_cache"))
LOCAL_PARQUET_DIR = Path(os.environ.get("TY_OPTION_PARQUET_DIR", r"D:\BigMarketDataParquet\OptionsMBP1_v2"))
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_option_flow"
FAMILIES = ("OZN", "ZN", "VY", "WY", "GY", "HY")
KEEP_COLUMNS = [
    "ts_event", "ts_recv", "instrument_id", "raw_symbol", "symbol",
    "bloomberg_symbol", "bloomberg_symbol_v2", "action", "side", "price",
    "size", "bid_price", "ask_price", "bid_size", "ask_size",
    "strike_price", "instrument_class", "expiration", "underlying", "dte",
    "mid", "futures_mid", "iv_bid", "iv_ask", "iv_mid",
    "delta_F_bid", "delta_F_ask", "delta_F_mid",
    "gamma_F_bid", "gamma_F_ask", "gamma_F_mid",
    "vega_F_bid", "vega_F_ask", "vega_F_mid",
]


class CountingReader(io.RawIOBase):
    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        data = self.raw.read(size)
        self.bytes_read += len(data or b"")
        return data

    def readinto(self, buffer: bytearray) -> int:
        data = self.read(len(buffer))
        length = len(data)
        buffer[:length] = data
        return length


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "bid_price_00": "bid_price", "ask_price_00": "ask_price",
        "bid_size_00": "bid_size", "ask_size_00": "ask_size",
    }
    for source, target in aliases.items():
        if target not in frame and source in frame:
            frame[target] = frame[source]
    if "underlying" not in frame and "underlying_symbol" in frame:
        frame["underlying"] = frame["underlying_symbol"]
    if "raw_symbol" not in frame and "symbol" in frame:
        frame["raw_symbol"] = frame["symbol"]
    for column in KEEP_COLUMNS:
        if column not in frame:
            frame[column] = np.nan
    return frame[KEEP_COLUMNS].copy()


def classify_trades(frame: pd.DataFrame) -> pd.DataFrame:
    out = _normalize_columns(frame)
    out["ts_event"] = pd.to_datetime(out["ts_event"], errors="coerce", utc=True)
    out["ts_recv"] = pd.to_datetime(out["ts_recv"], errors="coerce", utc=True)
    for column in ("price", "size", "bid_price", "ask_price", "bid_size", "ask_size", "strike_price", "dte"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out["instrument_class"] = out["instrument_class"].astype(str).str.upper().str[0]
    out["raw_symbol"] = out["raw_symbol"].fillna("").astype(str)
    out["underlying"] = out["underlying"].fillna("").astype(str).str.upper()
    out["family"] = out["raw_symbol"].str.extract(r"^([A-Z]+)", expand=False).fillna("")
    out["strike_price"] = np.where(out["strike_price"].abs() > 1_000_000, out["strike_price"] / 1e9, out["strike_price"])
    out = out[
        (out["action"].astype(str).str.upper() == "T")
        & out["instrument_class"].isin(["C", "P"])
        & out["underlying"].str.startswith("ZN")
        & (out["dte"] <= 20)
        & out["family"].str.startswith(FAMILIES)
        & (out["size"] > 0)
        & out["price"].notna()
    ].copy()
    valid_bbo = (
        out["bid_price"].notna() & out["ask_price"].notna()
        & (out["ask_price"] >= out["bid_price"])
    )
    at_ask = valid_bbo & (out["price"] >= out["ask_price"] - 1e-10)
    at_bid = valid_bbo & (out["price"] <= out["bid_price"] + 1e-10)
    out["bbo_aggressor"] = np.select(
        [at_ask & ~at_bid, at_bid & ~at_ask],
        [1, -1],
        default=0,
    )
    side = out["side"].astype(str).str.upper()
    out["side_aggressor"] = side.map({"B": 1, "A": -1}).fillna(0).astype(int)
    out["aggressor"] = np.where(out["bbo_aggressor"] != 0, out["bbo_aggressor"], out["side_aggressor"])
    out["classification_source"] = np.where(
        out["bbo_aggressor"] != 0,
        "bbo",
        np.where(out["side_aggressor"] != 0, "side", "unknown"),
    )
    out["bbo_side_agree"] = np.where(
        (out["bbo_aggressor"] != 0) & (out["side_aggressor"] != 0),
        out["bbo_aggressor"] == out["side_aggressor"],
        np.nan,
    )
    out["usable_signed"] = out["aggressor"] != 0
    return out.sort_values("ts_event")


def fetch_window(start: datetime, end: datetime, cache_path: Path, force: bool = False) -> dict[str, Any]:
    if cache_path.exists() and not force:
        cached = pd.read_parquet(cache_path)
        return {
            "status": "cached", "start": start.isoformat(), "end": end.isoformat(),
            "raw_rows": None, "trade_rows": int(len(cached)),
            "kept_rows": int(len(cached)), "wire_bytes": None,
            "cache_bytes": cache_path.stat().st_size, "elapsed_sec": 0.0,
        }
    params = {"start": start.replace(tzinfo=None).isoformat(), "end": end.replace(tzinfo=None).isoformat(), "format": "arrow"}
    began = time.monotonic()
    raw_rows = 0
    pieces: list[pd.DataFrame] = []
    with requests.get(f"{API_BASE}/historical/options", params=params, stream=True, timeout=(10, 300)) as response:
        response.raise_for_status()
        if response.headers.get("content-type", "").startswith("application/json"):
            payload = response.json()
            reader_bytes = len(response.content)
            raw_rows = int(payload.get("count") or 0)
        else:
            counted = CountingReader(response.raw)
            reader = pa_ipc.open_stream(counted)
            for batch in reader:
                raw_rows += batch.num_rows
                data = batch.to_pandas()
                if "action" in data:
                    data = data[data["action"].astype(str).str.upper() == "T"]
                if len(data):
                    classified = classify_trades(data)
                    if len(classified):
                        pieces.append(classified)
            reader_bytes = counted.bytes_read
    trades = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=[*KEEP_COLUMNS, "aggressor"])
    if len(trades):
        key_cols = [column for column in ("ts_event", "instrument_id", "price", "size", "side") if column in trades]
        trades = trades.drop_duplicates(key_cols, keep="last")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp = cache_path.with_suffix(".tmp.parquet")
    trades.to_parquet(temp, index=False, compression="zstd")
    temp.replace(cache_path)
    return {
        "status": "ok", "start": start.isoformat(), "end": end.isoformat(),
        "raw_rows": int(raw_rows), "trade_rows": int(sum(len(piece) for piece in pieces)),
        "kept_rows": int(len(trades)), "wire_bytes": int(reader_bytes),
        "cache_bytes": cache_path.stat().st_size,
        "elapsed_sec": time.monotonic() - began,
    }


def fetch_local_date(date_text: str, parquet_dir: Path, cache_path: Path, force: bool = False) -> dict[str, Any]:
    source = parquet_dir / f"{date_text}.parquet"
    if not source.exists():
        return {"status": "missing", "start": date_text, "end": date_text, "error": str(source)}
    if cache_path.exists() and not force:
        cached = pd.read_parquet(cache_path)
        return {
            "status": "cached", "start": date_text, "end": date_text,
            "raw_rows": None, "trade_rows": int(len(cached)), "kept_rows": int(len(cached)),
            "wire_bytes": 0, "cache_bytes": cache_path.stat().st_size, "elapsed_sec": 0.0,
        }
    began = time.monotonic()
    con = duckdb.connect()
    try:
        raw_rows = int(con.execute(f"SELECT COUNT(*) FROM read_parquet('{source.as_posix()}')").fetchone()[0])
        trades = con.execute(
            f"""
            SELECT *
            FROM read_parquet('{source.as_posix()}')
            WHERE action='T'
              AND underlying LIKE 'ZN%'
              AND dte <= 20
            ORDER BY ts_event
            """
        ).fetchdf()
    finally:
        con.close()
    classified = classify_trades(trades) if len(trades) else pd.DataFrame(columns=[*KEEP_COLUMNS, "aggressor"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp = cache_path.with_suffix(".tmp.parquet")
    classified.to_parquet(temp, index=False, compression="zstd")
    temp.replace(cache_path)
    return {
        "status": "ok_local", "start": date_text, "end": date_text,
        "raw_rows": raw_rows, "trade_rows": int(len(trades)), "kept_rows": int(len(classified)),
        "wire_bytes": 0, "source_bytes": source.stat().st_size,
        "cache_bytes": cache_path.stat().st_size, "elapsed_sec": time.monotonic() - began,
    }


def audit_cache(paths: list[Path]) -> dict[str, Any]:
    frames = [pd.read_parquet(path) for path in paths if path.exists()]
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if data.empty:
        return {"rows": 0, "quality_pass": False, "reason": "no retained TY option trades"}
    usable = float(data["usable_signed"].mean())
    both = data[(data["bbo_aggressor"] != 0) & (data["side_aggressor"] != 0)]
    agreement = float(both["bbo_side_agree"].mean()) if len(both) else np.nan
    mapping = float(
        data[["instrument_class", "strike_price", "expiration", "underlying"]]
        .notna().all(axis=1).mean()
    )
    return {
        "rows": int(len(data)),
        "dates": sorted(pd.to_datetime(data["ts_event"], utc=True).dt.date.astype(str).unique().tolist()),
        "usable_signed_rate": usable,
        "bbo_classified_rate": float((data["bbo_aggressor"] != 0).mean()),
        "side_classified_rate": float((data["side_aggressor"] != 0).mean()),
        "bbo_side_agreement": agreement,
        "instrument_mapping_rate": mapping,
        "duplicate_rate_after_dedup": float(data.duplicated(["ts_event", "instrument_id", "price", "size", "side"]).mean()),
        "call_share": float((data["instrument_class"] == "C").mean()),
        "monthly_share": float(data["family"].str.startswith("OZN").mean()),
        "unknown_rate": float((data["aggressor"] == 0).mean()),
        "quality_pass": bool(usable >= 0.90 and mapping >= 0.95),
    }


def write_audit_plot(audit: pd.DataFrame, projected: dict[str, Any], output_dir: Path) -> str | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    labels = [row["start"][5:16] for row in audit.to_dict(orient="records")]
    axes[0].bar(range(len(audit)), pd.to_numeric(audit["raw_rows"], errors="coerce"), label="raw MBP-1")
    axes[0].bar(range(len(audit)), pd.to_numeric(audit["kept_rows"], errors="coerce"), label="kept trades")
    axes[0].set_xticks(range(len(labels)), labels, rotation=60, ha="right", fontsize=7)
    axes[0].set_yscale("log")
    axes[0].set_title("Raw records vs retained trades")
    axes[0].legend()
    values = [
        projected.get("raw_wire_gb_20_dates") or 0.0,
        projected.get("compact_cache_gb_20_dates") or 0.0,
    ]
    axes[1].bar(["Raw Arrow transfer", "Compact trade cache"], values)
    axes[1].set_yscale("log")
    axes[1].set_ylabel("Projected GB for 20 dates")
    axes[1].set_title("Pilot transfer/storage projection")
    fig.tight_layout()
    path = output_dir / "option_flow_phase0_audit.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    cache_dir = Path(args.cache_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"windows": []}
    completed = {(row["start"], row["end"]): row for row in manifest.get("windows", [])}
    audit_rows = []
    cache_paths = []
    for date_text in args.dates:
        if args.source == "local":
            path = cache_dir / date_text / "full_day.parquet"
            key = (f"{date_text}:local", f"{date_text}:local")
            row = completed.get(key) if not args.force else None
            if row is None or not path.exists():
                try:
                    row = fetch_local_date(date_text, Path(args.local_parquet_dir), path, force=args.force)
                except Exception as exc:
                    row = {"status": "error", "start": key[0], "end": key[1], "error": str(exc)}
                completed[key] = row
                manifest["windows"] = list(completed.values())
                _atomic_json(manifest_path, manifest)
            audit_rows.append(row)
            if path.exists():
                cache_paths.append(path)
            continue
        date = datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc)
        for hour in args.hours:
            start = date + timedelta(hours=hour)
            end = start + timedelta(minutes=args.window_minutes)
            path = cache_dir / date_text / f"{start:%H%M}_{end:%H%M}.parquet"
            key = (start.isoformat(), end.isoformat())
            row = completed.get(key) if not args.force else None
            if row is None or not path.exists():
                try:
                    row = fetch_window(start, end, path, force=args.force)
                except Exception as exc:
                    row = {"status": "error", "start": key[0], "end": key[1], "error": str(exc)}
                completed[key] = row
                manifest["windows"] = list(completed.values())
                _atomic_json(manifest_path, manifest)
            audit_rows.append(row)
            if path.exists():
                cache_paths.append(path)
    audit = pd.DataFrame(audit_rows)
    audit.to_csv(output_dir / "data_audit.csv", index=False)
    quality = audit_cache(cache_paths)
    ok_rows = audit[audit["status"].isin(["ok", "ok_local", "cached"])] if not audit.empty else audit
    measured_minutes = max(len(ok_rows) * (1440 if args.source == "local" else args.window_minutes), 1)
    wire_bytes = pd.to_numeric(ok_rows.get("wire_bytes"), errors="coerce").sum(min_count=1)
    cache_bytes = pd.to_numeric(ok_rows.get("cache_bytes"), errors="coerce").sum(min_count=1)
    projected = {
        "raw_wire_gb_per_day": float(wire_bytes / measured_minutes * 1440 / 1e9) if pd.notna(wire_bytes) else None,
        "compact_cache_gb_per_day": float(cache_bytes / measured_minutes * 1440 / 1e9) if pd.notna(cache_bytes) else None,
    }
    projected["raw_wire_gb_20_dates"] = projected["raw_wire_gb_per_day"] * 20 if projected["raw_wire_gb_per_day"] is not None else None
    projected["compact_cache_gb_20_dates"] = projected["compact_cache_gb_per_day"] * 20 if projected["compact_cache_gb_per_day"] is not None else None
    runtime_ok = float(pd.to_numeric(ok_rows.get("elapsed_sec"), errors="coerce").mean()) <= 60 if len(ok_rows) else False
    storage_ok = (
        projected["compact_cache_gb_20_dates"] is not None
        and projected["compact_cache_gb_20_dates"] <= args.max_cache_gb_20_dates
        and (
            args.source == "local"
            or (
                projected["raw_wire_gb_20_dates"] is not None
                and projected["raw_wire_gb_20_dates"] <= args.max_raw_gb_20_dates
            )
        )
    )
    gate = {
        "quality_pass": bool(quality.get("quality_pass")),
        "runtime_pass": runtime_ok,
        "storage_pass": storage_ok,
    }
    gate["phase0_pass"] = all(gate.values())
    plot_path = write_audit_plot(audit, projected, output_dir)
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_dir": str(cache_dir),
        "source": args.source,
        "windows": audit_rows,
        "quality": quality,
        "projected": projected,
        "gate": gate,
        "plot": plot_path,
        "next": "run 20-date pilot" if gate["phase0_pass"] else "STOP: Phase 0 data/runtime/storage gate failed",
    }
    (output_dir / "data_audit.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch/audit historical TY option trade flow.")
    parser.add_argument("--dates", nargs="+", default=["2026-06-18", "2026-07-02", "2026-07-10"])
    parser.add_argument("--hours", nargs="+", type=int, default=[13, 15, 19])
    parser.add_argument("--window-minutes", type=int, default=1)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--max-raw-gb-20-dates", type=float, default=100.0)
    parser.add_argument("--max-cache-gb-20-dates", type=float, default=20.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--source", choices=["api", "local"], default="api")
    parser.add_argument("--local-parquet-dir", default=str(LOCAL_PARQUET_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
