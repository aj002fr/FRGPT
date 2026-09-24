#!/usr/bin/env python3
"""
TotalAggress vs Price/Yield Chart Generator
============================================

Reads Databento MBP-1 or trades data (from zip exports or API) and
PostgreSQL OHLCV to produce a dual-axis chart:
  blue = cumulative TotalAggress ($DV01, millions)
  red  = TY price (decimal)

matching the internal trading desk visualization style.

Data Sources
------------
  - Tick data : Databento zip exports (workspace/wenn/*.zip) or API fetch
  - Price     : PostgreSQL ohlcv_1m table

Assumptions (all explicitly logged)
------------------------------------
  1. Instrument: TY/ZN 10-Year Treasury Note Futures
  2. TotalAggress = cumulative sum of signed $DV01 flow
  3. signed_flow(i) = sign(i) * qty(i) * DV01_PER_CONTRACT
  4. sign: +1 if buy-aggressor (trade at ask), -1 if sell-aggressor (trade at bid)
  5. DV01_PER_CONTRACT = 85.0 $/bp (constant, configurable via --dv01)
  6. Bucket interval: configurable (default 5s)
  7. Reset rule: configurable (none, daily, globex_session)

Usage
-----
  # Auto-detect from zip(s) in workspace/wenn/
  python scripts/totalaggress_vs_price_chart.py

  # Explicit zip path
  python scripts/totalaggress_vs_price_chart.py --zip workspace/wenn/GLBX*.zip

  # Fetch from Databento API (trades schema, cheapest)
  python scripts/totalaggress_vs_price_chart.py --fetch --start 2026-01-29 --end 2026-02-05

  # Tuning
  python scripts/totalaggress_vs_price_chart.py --fetch --bucket 1s --dv01 85 --reset daily
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Third-party imports (deferred where optional)
# ---------------------------------------------------------------------------
try:
    import pandas as pd
    import numpy as np
except ImportError:
    print("ERROR: pandas and numpy are required. Install with: pip install pandas numpy")
    sys.exit(1)

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    print("ERROR: plotly is required. Install with: pip install plotly")
    sys.exit(1)

try:
    import zstandard as zstd
except ImportError:
    zstd = None  # Only needed for zip reading

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_DV01 = 85.0        # $/bp per TY contract (approximate)
DEFAULT_BUCKET = "5s"      # Resample interval
DEFAULT_RESET = "none"     # Reset rule: none | daily | globex
DATASET = "GLBX.MDP3"
SYMBOL_PARENT = "ZN.FUT"
CACHE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "_totalaggress_cache"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis"


# ============================================================================
# 1.  ZIP SCHEMA AUTO-DETECTION  +  STREAMING CSV.ZST READER
# ============================================================================

def detect_zip_schemas(zip_path: Path) -> Dict[str, List[str]]:
    """Inspect a Databento zip and return {schema: [filenames]}."""
    schemas: Dict[str, List[str]] = defaultdict(list)
    with zipfile.ZipFile(zip_path, "r") as z:
        for name in z.namelist():
            if name.endswith(".csv.zst"):
                # Pattern: glbx-mdp3-YYYYMMDD.<schema>.csv.zst
                parts = name.rsplit(".", 3)  # [prefix, schema, csv, zst]
                if len(parts) >= 4:
                    schema = parts[-3]  # e.g. "mbp-1", "trades", "status"
                    schemas[schema].append(name)
            elif name == "metadata.json":
                with z.open(name) as f:
                    meta = json.load(f)
                    q_schema = meta.get("query", {}).get("schema", "")
                    if q_schema:
                        logger.info(f"  Zip query schema: {q_schema}")
    return dict(schemas)


def iter_zst_csv_rows(
    zip_path: Path,
    filenames: List[str],
) -> Any:  # Generator[Dict[str, str]]
    """Stream-decompress .csv.zst files inside a zip and yield CSV rows."""
    if zstd is None:
        raise ImportError("zstandard is required for zip reading. pip install zstandard")

    dctx = zstd.ZstdDecompressor()
    for fname in sorted(filenames):
        with zipfile.ZipFile(zip_path, "r") as z:
            with z.open(fname) as f:
                compressed = f.read()
                reader = dctx.stream_reader(io.BytesIO(compressed))
                data = b""
                while True:
                    chunk = reader.read(1 << 20)  # 1 MB chunks
                    if not chunk:
                        break
                    data += chunk
                reader.close()

                lines = data.decode("utf-8").strip().split("\n")
                if len(lines) <= 1:
                    continue
                csv_reader = csv.DictReader(lines)
                for row in csv_reader:
                    yield row


def parse_databento_ts(ts_str: str) -> Optional[datetime]:
    """Parse a Databento timestamp (ISO or nanosecond integer) to UTC datetime."""
    if not ts_str:
        return None
    try:
        if "T" in ts_str or "-" in ts_str:
            clean = ts_str.replace("Z", "+00:00")
            # Truncate nanoseconds to microseconds
            if "." in clean:
                base, rest = clean.split(".", 1)
                tz_idx = -1
                for i, c in enumerate(rest):
                    if c in "+-":
                        tz_idx = i
                        break
                if tz_idx > 0:
                    frac = rest[:tz_idx][:6]
                    tz = rest[tz_idx:]
                    clean = f"{base}.{frac}{tz}"
                else:
                    clean = f"{base}.{rest[:6]}"
            return datetime.fromisoformat(clean)
        # Nanosecond integer
        ts_ns = int(float(ts_str))
        return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    except Exception:
        return None


def load_trades_from_zip(zip_path: Path) -> pd.DataFrame:
    """
    Extract trade records from a Databento zip.

    Tries schemas in order of preference:
      1. trades  (has side field directly)
      2. mbp-1   (filter action='T' for trade records)
    """
    schemas = detect_zip_schemas(zip_path)
    logger.info(f"Schemas found in {zip_path.name}: {list(schemas.keys())}")

    trade_files = schemas.get("trades", [])
    mbp1_files = schemas.get("mbp-1", [])

    rows: List[Dict[str, Any]] = []

    if trade_files:
        logger.info(f"Reading {len(trade_files)} trades files …")
        method = "trades"
        for row in iter_zst_csv_rows(zip_path, trade_files):
            ts = parse_databento_ts(row.get("ts_event", ""))
            if ts is None:
                continue
            price_str = row.get("price", "")
            size_str = row.get("size", "")
            side = row.get("side", "")
            if not price_str or not size_str:
                continue
            try:
                price = float(price_str)
                # Detect fixed-point (>1e6 means probably nanosecond-scaled)
                if price > 1e6:
                    price /= 1e9
                size = int(float(size_str))
            except (ValueError, TypeError):
                continue
            if price < 80 or price > 150:  # filter spreads
                continue
            rows.append({"ts": ts, "price": price, "size": size, "side": str(side)})

    elif mbp1_files:
        logger.info(f"Reading {len(mbp1_files)} MBP-1 files (filtering action='T') …")
        method = "mbp-1"
        for row in iter_zst_csv_rows(zip_path, mbp1_files):
            action = row.get("action", "")
            if action != "T":
                continue
            ts = parse_databento_ts(row.get("ts_event", ""))
            if ts is None:
                continue
            price_str = row.get("price", "")
            size_str = row.get("size", "")
            side = row.get("side", "")
            if not price_str or not size_str:
                continue
            try:
                price = float(price_str)
                if price > 1e6:
                    price /= 1e9
                size = int(float(size_str))
            except (ValueError, TypeError):
                continue
            if price < 80 or price > 150:  # filter spreads
                continue
            rows.append({"ts": ts, "price": price, "size": size, "side": str(side)})
    else:
        logger.warning(
            f"No trades or MBP-1 data found in {zip_path.name}. "
            f"Available schemas: {list(schemas.keys())}. "
            f"Use --fetch to get data from Databento API instead."
        )
        return pd.DataFrame()

    if not rows:
        logger.warning("Parsed 0 trade records from zip.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df.sort_values("ts", inplace=True)
    df.reset_index(drop=True, inplace=True)
    logger.info(
        f"Loaded {len(df):,} trade records from zip ({method}) | "
        f"{df['ts'].min()} → {df['ts'].max()}"
    )
    return df


# ============================================================================
# 2.  DATABENTO API FETCH  (trades schema – cheapest)
# ============================================================================

def fetch_trades_from_api(
    start: datetime,
    end: datetime,
    symbol: str = SYMBOL_PARENT,
    cache: bool = True,
) -> pd.DataFrame:
    """Fetch trade records from Databento Historical API and optionally cache."""
    try:
        import databento as db
    except ImportError:
        logger.error("databento package not installed. pip install databento")
        return pd.DataFrame()

    # Load API key
    from scripts.databento_live_state import get_databento_env
    env = get_databento_env()
    api_key = env.get("api_key", "")
    if not api_key:
        logger.error("No DATABENTO_API_KEY found. Set it in config/keys.env or environment.")
        return pd.DataFrame()

    # Check cache
    cache_key = f"trades_{symbol}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    cache_path = CACHE_DIR / f"{cache_key}.parquet"
    if cache and cache_path.exists():
        logger.info(f"Loading cached trades from {cache_path}")
        df = pd.read_parquet(cache_path)
        logger.info(f"Cached: {len(df):,} records | {df['ts'].min()} → {df['ts'].max()}")
        return df

    logger.info(f"Fetching trades from Databento API: {start} → {end}")
    client = db.Historical(key=api_key)

    # Cost estimate
    try:
        cost = client.metadata.get_cost(
            dataset=DATASET,
            symbols=[symbol],
            stype_in="parent",
            schema="trades",
            start=start.strftime("%Y-%m-%dT%H:%M:%S"),
            end=end.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        logger.info(f"  Estimated API cost: ${cost:.4f}")
    except Exception as e:
        logger.warning(f"  Could not estimate cost: {e}")

    try:
        data = client.timeseries.get_range(
            dataset=DATASET,
            symbols=[symbol],
            stype_in="parent",
            schema="trades",
            start=start.strftime("%Y-%m-%dT%H:%M:%S"),
            end=end.strftime("%Y-%m-%dT%H:%M:%S"),
        )
    except Exception as e:
        err_str = str(e)
        # Auto-retry with reduced end date if data isn't available yet
        if "data_end_after_available_end" in err_str and "available up to" in err_str:
            import re
            m = re.search(r"available up to '([^']+)'", err_str)
            if m:
                avail_end = m.group(1)
                # Normalize to ISO format with 'T' separator
                avail_end_iso = avail_end.strip().replace(" ", "T")
                logger.warning(
                    f"Data available only up to {avail_end_iso}. Retrying with that end time."
                )
                try:
                    data = client.timeseries.get_range(
                        dataset=DATASET,
                        symbols=[symbol],
                        stype_in="parent",
                        schema="trades",
                        start=start.strftime("%Y-%m-%dT%H:%M:%S"),
                        end=avail_end_iso,
                    )
                except Exception as e2:
                    logger.error(f"Retry also failed: {e2}")
                    return pd.DataFrame()
            else:
                logger.error(f"Databento API fetch failed: {e}")
                return pd.DataFrame()
        else:
            logger.error(f"Databento API fetch failed: {e}")
            return pd.DataFrame()

    raw_df = data.to_df()
    if raw_df.empty:
        logger.warning("API returned 0 records.")
        return pd.DataFrame()

    logger.info(f"  API returned {len(raw_df):,} records")

    # Normalize to our standard columns
    records: List[Dict[str, Any]] = []
    for idx, row in raw_df.iterrows():
        ts = idx if isinstance(idx, pd.Timestamp) else pd.Timestamp(idx, tz="UTC")
        price = float(row.get("price", 0))
        # Auto-detect fixed-point
        if price > 1e6:
            price /= 1e9
        size = int(row.get("size", 0))
        side = str(row.get("side", ""))
        if price <= 0 or size <= 0:
            continue
        # Filter out calendar spreads / non-outright contracts (price < 80 or > 150)
        if price < 80 or price > 150:
            continue
        records.append({"ts": ts, "price": price, "size": size, "side": side})

    df = pd.DataFrame(records)
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df.sort_values("ts", inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Cache
    if cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_path, index=False)
        logger.info(f"  Cached to {cache_path}")

    logger.info(f"Fetched {len(df):,} trades | {df['ts'].min()} → {df['ts'].max()}")
    return df


# ============================================================================
# 3.  TRADE CLASSIFICATION  +  TOTALAGGRESS COMPUTATION
# ============================================================================

def classify_aggressor(side_str: str) -> int:
    """
    Map Databento side field to aggressor sign.

    Convention (Databento TradeMsg / MBP action='T'):
      'B' or 'Bid'  → aggressor was a buyer (lifted the offer) → +1
      'A' or 'Ask'  → aggressor was a seller (hit the bid)     → -1
      anything else  → unknown → 0

    Verified empirically: when price rises, side='B' volume dominates;
    when price falls, side='A' volume dominates.
    """
    s = str(side_str).strip().upper()
    if s in ("B", "BID"):
        return +1
    if s in ("A", "ASK"):
        return -1
    return 0


def compute_total_aggress(
    trades_df: pd.DataFrame,
    dv01: float = DEFAULT_DV01,
    bucket: str = DEFAULT_BUCKET,
    reset: str = DEFAULT_RESET,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Compute cumulative TotalAggress from trade records.

    Returns
    -------
    flow_df : DataFrame with columns [bucket_ts, signed_flow_bucket, total_aggress, trade_price]
    meta    : dict of assumptions / stats
    """
    if trades_df.empty:
        return pd.DataFrame(), {}

    df = trades_df.copy()
    df["sign"] = df["side"].apply(classify_aggressor)
    df["signed_flow"] = df["sign"] * df["size"] * dv01

    # Classify stats
    n_buy = (df["sign"] == 1).sum()
    n_sell = (df["sign"] == -1).sum()
    n_unknown = (df["sign"] == 0).sum()
    logger.info(
        f"Aggressor classification: "
        f"buy={n_buy:,}  sell={n_sell:,}  unknown={n_unknown:,}  "
        f"total={len(df):,}"
    )

    # Set timestamp as index for resampling
    df.set_index("ts", inplace=True)

    # Bucket
    bucketed_flow = df["signed_flow"].resample(bucket).sum().fillna(0.0)
    bucketed_price = df["price"].resample(bucket).last().ffill()

    # Reset logic
    if reset == "none":
        total_aggress = bucketed_flow.cumsum()
    elif reset == "daily":
        # Reset at midnight UTC each day
        groups = bucketed_flow.groupby(bucketed_flow.index.date)
        total_aggress = groups.cumsum()
    elif reset == "globex":
        # Reset at 6pm ET (23:00 UTC in winter, 22:00 UTC in summer)
        # Approximate: reset at 23:00 UTC
        session_ids = ((bucketed_flow.index.hour < 23).astype(int).diff().fillna(0).abs().cumsum())
        total_aggress = bucketed_flow.groupby(session_ids).cumsum()
    else:
        total_aggress = bucketed_flow.cumsum()

    flow_df = pd.DataFrame({
        "signed_flow_bucket": bucketed_flow,
        "total_aggress": total_aggress,
        "trade_price": bucketed_price,
    })
    flow_df.index.name = "bucket_ts"

    meta = {
        "dv01_per_contract": dv01,
        "bucket_interval": bucket,
        "reset_rule": reset,
        "sign_mapping": "+1=buy_aggress(bid_side), -1=sell_aggress(ask_side), 0=unknown",
        "formula": "TotalAggress(t) = cumsum( sum_bucket( sign_i * size_i * DV01 ) )",
        "trade_count": len(trades_df),
        "buy_aggress_count": int(n_buy),
        "sell_aggress_count": int(n_sell),
        "unknown_side_count": int(n_unknown),
        "bucket_count": len(flow_df),
        "total_aggress_min": float(total_aggress.min()),
        "total_aggress_max": float(total_aggress.max()),
        "total_aggress_final": float(total_aggress.iloc[-1]) if len(total_aggress) > 0 else 0.0,
        "time_start": str(flow_df.index.min()),
        "time_end": str(flow_df.index.max()),
    }
    return flow_df, meta


# ============================================================================
# 4.  FETCH PRICE SERIES (ZIP or POSTGRES)
# ============================================================================

def load_ohlcv_from_zip(zip_path: Path) -> pd.DataFrame:
    """
    Load OHLCV-1m close prices from a Databento zip export.

    Expects the zip to contain an ``ohlcv-1m.csv.zst`` file with columns:
    ts_event, open, high, low, close, volume, symbol  (pretty_px / pretty_ts).

    For multi-contract zips, picks the front-month contract per day by volume.
    Returns a DataFrame indexed by UTC timestamp with a single ``price`` column.
    """
    if zstd is None:
        raise ImportError("zstandard is required for zip reading. pip install zstandard")

    # Find ohlcv files inside the zip
    with zipfile.ZipFile(zip_path, "r") as z:
        ohlcv_files = [n for n in z.namelist() if "ohlcv" in n and n.endswith(".csv.zst")]

    if not ohlcv_files:
        logger.warning(f"No ohlcv .csv.zst files found in {zip_path.name}")
        return pd.DataFrame()

    logger.info(f"Reading OHLCV from {zip_path.name}: {ohlcv_files}")

    rows: List[Dict[str, Any]] = []
    for row in iter_zst_csv_rows(zip_path, ohlcv_files):
        ts = parse_databento_ts(row.get("ts_event", ""))
        if ts is None:
            continue
        close_str = row.get("close", "")
        volume_str = row.get("volume", "0")
        symbol = row.get("symbol", "")
        if not close_str:
            continue
        try:
            close = float(close_str)
            if close > 1e6:
                close /= 1e9  # fixed-point fallback
            volume = int(float(volume_str)) if volume_str else 0
        except (ValueError, TypeError):
            continue
        # Basic sanity: TY prices should be 80–150
        if close < 80 or close > 150:
            continue
        rows.append({"ts": ts, "price": close, "volume": volume, "symbol": symbol})

    if not rows:
        logger.warning("Parsed 0 OHLCV rows from zip.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    # --- Front-month selection: per day, keep the contract with highest volume ---
    df["date"] = df["ts"].dt.date
    daily_vol = df.groupby(["date", "symbol"])["volume"].sum().reset_index()
    front = daily_vol.loc[daily_vol.groupby("date")["volume"].idxmax()][["date", "symbol"]]
    front = front.rename(columns={"symbol": "front_symbol"})
    df = df.merge(front, on="date", how="left")
    df = df[df["symbol"] == df["front_symbol"]].copy()
    df.drop(columns=["volume", "symbol", "date", "front_symbol"], inplace=True)

    df.drop_duplicates(subset=["ts"], keep="last", inplace=True)
    df.set_index("ts", inplace=True)
    df.sort_index(inplace=True)

    logger.info(f"Loaded {len(df):,} OHLCV rows from zip | {df.index.min()} → {df.index.max()}")
    return df


def fetch_ohlcv_price(
    start: datetime,
    end: datetime,
    symbol_pattern: str = "TY%",
) -> pd.DataFrame:
    """Pull close prices from PostgreSQL ohlcv_1m and return as DataFrame."""
    if psycopg2 is None:
        logger.error("psycopg2-binary not installed. pip install psycopg2-binary")
        return pd.DataFrame()

    from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD,
    )
    sql = """
        SELECT ts_event AS ts, close AS price
        FROM ohlcv_1m
        WHERE symbol ILIKE %s
          AND ts_event >= %s
          AND ts_event <= %s
          AND close >= 80 AND close <= 150
        ORDER BY ts_event ASC
    """
    start_str = start.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end.strftime("%Y-%m-%d %H:%M:%S")
    try:
        df = pd.read_sql_query(sql, conn, params=[symbol_pattern, start_str, end_str])
    finally:
        conn.close()

    if df.empty:
        logger.warning(f"No OHLCV data for {symbol_pattern} between {start_str} and {end_str}")
        return df

    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df.drop_duplicates(subset=["ts"], keep="last", inplace=True)
    df.set_index("ts", inplace=True)
    df.sort_index(inplace=True)
    logger.info(f"Loaded {len(df):,} OHLCV rows | {df.index.min()} → {df.index.max()}")
    return df


# ============================================================================
# 5.  CHART RENDERERS  (Plotly HTML  +  matplotlib PNG)
# ============================================================================

def _align_series(
    flow_df: pd.DataFrame,
    ohlcv_df: pd.DataFrame,
    bucket: str,
    plot_start: Optional[str] = None,
    plot_end: Optional[str] = None,
) -> Tuple[pd.Series, pd.Series]:
    """
    Merge TotalAggress + price into aligned series, optionally trimmed.

    Uses the OHLCV index as the time grid (trading-hours only) so that
    weekends/holidays produce a natural gap instead of a flat forward-fill.
    TotalAggress is forward-filled onto the OHLCV grid so both lines share
    exact same x-coordinates.
    """
    if not ohlcv_df.empty and isinstance(ohlcv_df.index, pd.DatetimeIndex):
        # Use OHLCV timestamps as the authoritative time grid.
        # This only contains trading-hours bars → no weekend flat lines.
        price_series = ohlcv_df["price"].copy()

        # Forward-fill TotalAggress onto the OHLCV grid:
        # 1. build a combined index so reindex + ffill can carry values forward
        # 2. keep only the OHLCV timestamps
        combined_idx = flow_df.index.union(price_series.index).sort_values()
        ta_on_combined = flow_df["total_aggress"].reindex(combined_idx).ffill().fillna(0.0)
        ta_series = ta_on_combined.reindex(price_series.index)
    else:
        # No OHLCV – fall back to trade-derived price from flow_df
        logger.info("No OHLCV data; using trade-derived prices from flow_df.")
        # Only keep buckets with actual trades (drop empty forward-filled buckets)
        non_zero = flow_df["signed_flow_bucket"] != 0
        ta_series = flow_df.loc[non_zero, "total_aggress"]
        price_series = flow_df.loc[non_zero, "trade_price"] if "trade_price" in flow_df.columns else pd.Series(dtype=float)

    # Final fallback if price is still all-NaN
    if price_series.isna().all() and "trade_price" in flow_df.columns:
        logger.info("Price still NaN; falling back to trade-derived prices.")
        price_series = flow_df["trade_price"].reindex(ta_series.index).ffill()

    # Drop NaN rows
    mask = ta_series.notna() & price_series.notna()
    ta_series = ta_series[mask]
    price_series = price_series[mask]

    # Trim to requested plot window
    if plot_start:
        ts_start = pd.Timestamp(plot_start, tz="UTC")
        ta_series = ta_series[ta_series.index >= ts_start]
        price_series = price_series[price_series.index >= ts_start]
    if plot_end:
        ts_end = pd.Timestamp(plot_end, tz="UTC")
        ta_series = ta_series[ta_series.index <= ts_end]
        price_series = price_series[price_series.index <= ts_end]

    # Rebase TotalAggress to 0 at the plot-window start.
    # The reference chart starts its cumsum at the visible window edge,
    # so pre-accumulated flow before the window shouldn't shift the curve.
    if len(ta_series) > 0:
        offset = ta_series.iloc[0]
        if offset != 0:
            logger.info(f"Rebasing TotalAggress: subtracting starting offset {offset:,.0f}")
            ta_series = ta_series - offset

    return ta_series, price_series


def render_chart_png(
    flow_df: pd.DataFrame,
    ohlcv_df: pd.DataFrame,
    meta: Dict[str, Any],
    output_dir: Path = OUTPUT_DIR,
    bucket: str = DEFAULT_BUCKET,
    plot_start: Optional[str] = None,
    plot_end: Optional[str] = None,
    same_scale: bool = False,
) -> Tuple[Path, Path]:
    """
    Build a matplotlib dual-axis PNG matching the reference chart style.

    Left axis  : TotalAggress (in M)
    Right axis : Price/Yield

    Axis ranges are deliberately aligned so that visual overlap of the
    two lines = "normal" flow-vs-price relationship.  Any divergence =
    passive absorption / iceberg signal.

    The alignment defines an implicit linear fair-value mapping:
      P_implied = P_anchor + Aggress * beta
    where
      beta = price_span / aggress_span
      P_anchor = P_min - A_min * beta
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import matplotlib.ticker as mticker
    except ImportError:
        logger.error("matplotlib is required for PNG output. pip install matplotlib")
        return Path(), Path()

    output_dir.mkdir(parents=True, exist_ok=True)
    ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    png_path = output_dir / f"totalaggress_vs_price_{ts_tag}.png"
    json_path = output_dir / f"totalaggress_vs_price_{ts_tag}.json"

    ta_series, price_series = _align_series(
        flow_df, ohlcv_df, bucket, plot_start, plot_end,
    )

    if ta_series.empty:
        logger.error("No data to plot after trimming. Check --plot-start / --plot-end.")
        return png_path, json_path

    logger.info(
        f"PNG chart data: {len(ta_series):,} points | "
        f"{ta_series.index.min()} → {ta_series.index.max()}"
    )

    # ---- Axis mapping / normalization ----
    # Option A (default): dual-axis alignment (fair-value mapping).
    # Option B: same-scale normalization (min/max match to price axis).
    # Align axes so that:
    #   max(TotalAggress) ↔ max(Price)   (highs aligned)
    #   min(TotalAggress) ↔ min(Price)   (lows aligned)
    # This defines the implicit linear mapping between flow and price.
    # Divergence from the mapping = passive absorption / iceberg signal.

    A_MIN = float(ta_series.min())
    A_MAX = float(ta_series.max())
    P_MIN = float(price_series.min())
    P_MAX = float(price_series.max())

    # Add small symmetric margin (5%) so extremes aren't clipped at the edge
    a_margin = (A_MAX - A_MIN) * 0.05
    p_margin = (P_MAX - P_MIN) * 0.05
    A_MIN -= a_margin
    A_MAX += a_margin
    P_MIN -= p_margin
    P_MAX += p_margin

    aggress_span = A_MAX - A_MIN
    price_span = P_MAX - P_MIN
    beta = price_span / aggress_span if aggress_span else 0.0
    p_anchor = P_MIN - A_MIN * beta   # intercept: P = p_anchor + β * Aggress

    if same_scale:
        # Min/max match: Aggress_max = m * P_max + c; Aggress_min = m * P_min + c
        # Solve: m = (A_max - A_min) / (P_max - P_min), c = A_max - m * P_max
        m = (A_MAX - A_MIN) / (P_MAX - P_MIN) if (P_MAX - P_MIN) else 0.0
        c = A_MAX - m * P_MAX if m else 0.0
        ta_series_scaled = (ta_series - c) / m if m else ta_series.copy()

    # Flow-implied fair value at every point
    p_implied = p_anchor + ta_series * beta
    residual  = price_series - p_implied   # >0 ⇒ price rich vs flow (passive buying)

    logger.info(
        f"Fair-value mapping: P_implied = {p_anchor:.4f} + Aggress * {beta:.4e}"
    )
    logger.info(
        f"  beta = {price_span} / {aggress_span:,.0f} = {beta:.4e} "
        f"(1M $DV01 ≈ {1e6*beta:.5f} price pts ≈ {1e6*beta*32:.1f} ticks)"
    )
    logger.info(
        f"  Residual (actual − implied): "
        f"mean={residual.mean():.5f}  min={residual.min():.5f}  max={residual.max():.5f}"
    )

    # ---- matplotlib figure ----
    fig, ax1 = plt.subplots(figsize=(16, 7))
    ax1.set_facecolor("#eaeaf2")
    fig.patch.set_facecolor("white")

    if same_scale:
        # Single-axis plot: both series are on price scale
        ax1.plot(price_series.index, price_series.values, color="red", linewidth=0.7,
                 label="Price/Yield")
        ax1.plot(ta_series_scaled.index, ta_series_scaled.values, color="blue", linewidth=0.7,
                 label="TotalAggress (scaled)")
        # Left axis: show corresponding TotalAggress values (inverse mapping)
        ax1.set_ylabel("TotalAggress", color="black", fontsize=12)
        ax1.tick_params(axis="y", labelcolor="black")
        ax1.set_ylim(P_MIN, P_MAX)
        ax1.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda y, _: f"{(m * y + c)/1e6:.0f}M")
        )
        ax1.grid(True, alpha=0.3)

        # Right axis: show actual price values
        ax2 = ax1.twinx()
        ax2.set_ylabel("Price/Yield", color="red", fontsize=12)
        ax2.tick_params(axis="y", labelcolor="red")
        ax2.set_ylim(P_MIN, P_MAX)
    else:
        # Dual-axis plot: totalaggress + price on separate axes
        ax1.plot(ta_series.index, ta_series.values, color="blue", linewidth=0.7,
                 label="TotalAggress")
        ax1.set_ylabel("TotalAggress", color="black", fontsize=12)
        ax1.tick_params(axis="y", labelcolor="black")
        ax1.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: f"{x/1e6:.0f}M")
        )
        ax1.set_ylim(A_MIN, A_MAX)
        ax1.grid(True, alpha=0.3)

        ax2 = ax1.twinx()
        ax2.plot(price_series.index, price_series.values, color="red", linewidth=0.7,
                 label="Price/Yield")
        ax2.set_ylabel("Price/Yield", color="red", fontsize=12)
        ax2.tick_params(axis="y", labelcolor="red")
        ax2.set_ylim(P_MIN, P_MAX)

    # X-axis date formatting
    ax1.xaxis.set_major_locator(mdates.DayLocator())
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%Y"))
    ax1.tick_params(axis="x", rotation=0)

    fig.suptitle("TotalAggress vs Price/Yield", fontsize=14, y=0.97)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(str(png_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"PNG saved: {png_path}")

    # ---- Update meta with fair-value mapping ----
    meta["chart"] = {
        "format": "png",
        "price_source": "ohlcv_zip" if not ohlcv_df.empty else "trade_derived",
        "total_points_plotted": len(ta_series),
        "plot_start": str(ta_series.index.min()),
        "plot_end": str(ta_series.index.max()),
        "png_path": str(png_path),
        "same_scale": bool(same_scale),
    }
    meta["axis_aligned_fair_value"] = {
        "description": (
            "Axis ranges are set so visual overlap = normal flow-vs-price. "
            "Divergence = passive absorption signal."
        ),
        "aggress_axis_min": A_MIN,
        "aggress_axis_max": A_MAX,
        "price_axis_min": P_MIN,
        "price_axis_max": P_MAX,
        "beta_points_per_dollar_dv01": beta,
        "beta_ticks_per_1M_dv01": round(1e6 * beta * 32, 2),
        "p_anchor_intercept": round(p_anchor, 6),
        "formula": f"P_implied = {p_anchor:.4f} + Aggress * {beta:.4e}",
        "example_aggress_neg4M": round(p_anchor + (-4_000_000) * beta, 4),
        "residual_mean": round(float(residual.mean()), 5),
        "residual_std": round(float(residual.std()), 5),
        "residual_min": round(float(residual.min()), 5),
        "residual_max": round(float(residual.max()), 5),
        "residual_description": (
            "residual = actual_price - flow_implied_price. "
            ">0 means price rich vs flow (passive buyer/absorption). "
            "<0 means price cheap vs flow (passive seller)."
        ),
    }
    if same_scale:
        meta["same_scale_mapping"] = {
            "description": "TotalAggress scaled onto price axis via min/max alignment.",
            "m": float(m),
            "c": float(c),
            "formula": "scaled = (Aggress - c) / m",
            "aggress_min": float(A_MIN),
            "aggress_max": float(A_MAX),
            "price_min": float(P_MIN),
            "price_max": float(P_MAX),
        }

    with open(json_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    logger.info(f"Assumptions JSON saved: {json_path}")

    return png_path, json_path


def render_chart(
    flow_df: pd.DataFrame,
    ohlcv_df: pd.DataFrame,
    meta: Dict[str, Any],
    output_dir: Path = OUTPUT_DIR,
    bucket: str = DEFAULT_BUCKET,
) -> Tuple[Path, Path]:
    """
    Build a dual-axis Plotly chart and write HTML + JSON.

    Left axis  : TotalAggress ($DV01, displayed in millions)
    Right axis : Price/Yield (decimal TY price)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = output_dir / f"totalaggress_vs_price_{ts_tag}.html"
    json_path = output_dir / f"totalaggress_vs_price_{ts_tag}.json"

    # Align price series to flow buckets
    if not ohlcv_df.empty and isinstance(ohlcv_df.index, pd.DatetimeIndex):
        price_resampled = ohlcv_df["price"].resample(bucket).last().ffill()
        common_idx = flow_df.index.union(price_resampled.index).sort_values()
        ta_series = flow_df["total_aggress"].reindex(common_idx).ffill().fillna(0.0)
        price_series = price_resampled.reindex(common_idx).ffill()
    else:
        # No OHLCV data — use trade-derived prices from flow_df
        logger.info("No OHLCV data; using trade-derived prices from flow_df.")
        common_idx = flow_df.index
        ta_series = flow_df["total_aggress"]
        price_series = flow_df["trade_price"] if "trade_price" in flow_df.columns else pd.Series(dtype=float)

    # Final fallback if price is still all-NaN
    if price_series.isna().all() and "trade_price" in flow_df.columns:
        logger.info("Price still NaN; falling back to trade-derived prices.")
        price_series = flow_df["trade_price"].reindex(common_idx).ffill()

    # Drop rows where both are NaN
    mask = ta_series.notna() & price_series.notna()
    ta_series = ta_series[mask]
    price_series = price_series[mask]

    if ta_series.empty:
        logger.error("No overlapping data between TotalAggress and price. Cannot plot.")
        return html_path, json_path

    logger.info(
        f"Chart data: {len(ta_series):,} points | "
        f"{ta_series.index.min()} → {ta_series.index.max()}"
    )

    # --- Build Plotly figure ---
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Blue line: TotalAggress (left axis, in millions)
    fig.add_trace(
        go.Scattergl(
            x=ta_series.index,
            y=ta_series.values,
            name="TotalAggress",
            line=dict(color="blue", width=1),
            hovertemplate="TotalAggress: %{y:,.0f}<br>%{x}<extra></extra>",
        ),
        secondary_y=False,
    )

    # Red line: Price/Yield (right axis)
    fig.add_trace(
        go.Scattergl(
            x=price_series.index,
            y=price_series.values,
            name="Price/Yield",
            line=dict(color="red", width=1),
            hovertemplate="Price: %{y:.4f}<br>%{x}<extra></extra>",
        ),
        secondary_y=True,
    )

    fig.update_layout(
        title="TotalAggress vs Price/Yield",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=600,
        margin=dict(l=80, r=80, t=60, b=60),
    )

    # Left axis formatting (in millions)
    ta_min = ta_series.min()
    ta_max = ta_series.max()
    fig.update_yaxes(
        title_text="TotalAggress",
        secondary_y=False,
        tickformat=",",
        ticksuffix="",
    )

    # Right axis formatting
    fig.update_yaxes(
        title_text="Price/Yield",
        secondary_y=True,
    )

    fig.update_xaxes(
        title_text="",
        rangeslider_visible=False,
    )

    # Write HTML
    fig.write_html(str(html_path), include_plotlyjs="cdn")
    logger.info(f"Chart saved: {html_path}")

    # Alignment stats for JSON
    meta["chart"] = {
        "price_source": "ohlcv_1m" if not ohlcv_df.empty else "trade_derived",
        "total_points_plotted": len(ta_series),
        "price_points": int(price_series.notna().sum()),
        "flow_points": int(ta_series.notna().sum()),
        "html_path": str(html_path),
    }

    # Write JSON
    with open(json_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    logger.info(f"Assumptions JSON saved: {json_path}")

    return html_path, json_path


# ============================================================================
# 6.  CLI  +  MAIN
# ============================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="TotalAggress vs Price/Yield chart generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--zip",
        type=str,
        nargs="*",
        default=None,
        help="Path(s) to Databento zip file(s). Default: workspace/wenn/*.zip",
    )
    p.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch trades from Databento API (requires DATABENTO_API_KEY)",
    )
    p.add_argument("--start", type=str, default="2026-01-29",
                    help="Start date for API fetch (YYYY-MM-DD)")
    p.add_argument("--end", type=str, default="2026-02-05",
                    help="End date for API fetch (YYYY-MM-DD)")
    p.add_argument("--dv01", type=float, default=DEFAULT_DV01,
                    help=f"DV01 per contract in $/bp (default {DEFAULT_DV01})")
    p.add_argument("--bucket", type=str, default=DEFAULT_BUCKET,
                    help=f"Resample bucket interval (default {DEFAULT_BUCKET})")
    p.add_argument("--reset", type=str, default=DEFAULT_RESET,
                    choices=["none", "daily", "globex"],
                    help=f"TotalAggress reset rule (default {DEFAULT_RESET})")
    p.add_argument("--symbol", type=str, default="TY%",
                    help="PostgreSQL OHLCV symbol pattern (default TY%%)")
    p.add_argument("--ohlcv-zip", type=str, default=None,
                    help="Path to Databento OHLCV zip (overrides PostgreSQL)")
    p.add_argument("--png", action="store_true",
                    help="Output static PNG (matplotlib) instead of interactive HTML")
    p.add_argument("--same-scale", action="store_true",
                    help="Plot TotalAggress on the same price scale via min/max alignment")
    p.add_argument("--plot-start", type=str, default=None,
                    help="Trim plot to start at this datetime (YYYY-MM-DD or ISO)")
    p.add_argument("--plot-end", type=str, default=None,
                    help="Trim plot to end at this datetime (YYYY-MM-DD or ISO)")
    p.add_argument("--no-cache", action="store_true",
                    help="Skip parquet cache for API fetches")
    p.add_argument("--output-dir", type=str, default=str(OUTPUT_DIR),
                    help="Output directory for chart and JSON")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    logger.info("=" * 70)
    logger.info("TotalAggress vs Price/Yield Chart Generator")
    logger.info("=" * 70)

    # ---------------------------------------------------------------
    # Step 1: Load trade data
    # ---------------------------------------------------------------
    trades_df = pd.DataFrame()

    if not args.fetch:
        # Try zip files first
        zip_paths: List[Path] = []
        if args.zip:
            for zp in args.zip:
                p = Path(zp)
                if p.exists():
                    zip_paths.append(p)
                else:
                    # Glob
                    zip_paths.extend(Path(".").glob(zp))
        else:
            # Default: workspace/wenn/*.zip
            zip_paths = list((PROJECT_ROOT / "workspace" / "wenn").glob("*.zip"))

        if zip_paths:
            for zp in zip_paths:
                logger.info(f"Inspecting zip: {zp}")
                df = load_trades_from_zip(zp)
                if not df.empty:
                    trades_df = pd.concat([trades_df, df], ignore_index=True) if not trades_df.empty else df

        if trades_df.empty:
            logger.info(
                "No trade data found in zip(s). "
                "Falling back to Databento API (--fetch implied)."
            )
            args.fetch = True

    if args.fetch and trades_df.empty:
        start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        # End-of-day: if user gives a date, extend to 23:59:59 so we get the full day
        end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc,
        )
        trades_df = fetch_trades_from_api(
            start=start_dt,
            end=end_dt,
            cache=not args.no_cache,
        )

    if trades_df.empty:
        logger.error("No trade data available. Exiting.")
        sys.exit(1)

    # Filter out spread / non-outright prices (safety net for cached data)
    pre_filter = len(trades_df)
    trades_df = trades_df[(trades_df["price"] >= 80) & (trades_df["price"] <= 150)].copy()
    n_filtered = pre_filter - len(trades_df)
    if n_filtered > 0:
        logger.info(f"Filtered {n_filtered:,} non-outright trades (price < 80 or > 150)")

    # Sort
    trades_df.sort_values("ts", inplace=True)
    trades_df.reset_index(drop=True, inplace=True)

    # ---------------------------------------------------------------
    # Step 2: Compute TotalAggress
    # ---------------------------------------------------------------
    logger.info("-" * 70)
    logger.info("Computing TotalAggress …")
    flow_df, meta = compute_total_aggress(
        trades_df,
        dv01=args.dv01,
        bucket=args.bucket,
        reset=args.reset,
    )
    if flow_df.empty:
        logger.error("TotalAggress computation produced no data. Exiting.")
        sys.exit(1)

    # ---------------------------------------------------------------
    # Step 3: Fetch price series (OHLCV zip > Postgres > trade-derived)
    # ---------------------------------------------------------------
    logger.info("-" * 70)
    ohlcv_df = pd.DataFrame()

    # 3a) Try OHLCV zip if provided
    ohlcv_zip_path = Path(args.ohlcv_zip) if args.ohlcv_zip else None
    if ohlcv_zip_path is None:
        # Auto-detect ohlcv.zip in workspace/wenn/
        auto = PROJECT_ROOT / "workspace" / "wenn" / "ohlcv.zip"
        if auto.exists():
            ohlcv_zip_path = auto
    if ohlcv_zip_path and ohlcv_zip_path.exists():
        logger.info(f"Loading OHLCV from zip: {ohlcv_zip_path} …")
        ohlcv_df = load_ohlcv_from_zip(ohlcv_zip_path)

    # 3b) Fall back to PostgreSQL
    if ohlcv_df.empty:
        logger.info("Fetching OHLCV price series from PostgreSQL …")
        ts_min = flow_df.index.min()
        ts_max = flow_df.index.max()
        ohlcv_df = fetch_ohlcv_price(
            start=ts_min.to_pydatetime(),
            end=ts_max.to_pydatetime(),
            symbol_pattern=args.symbol,
        )

    # ---------------------------------------------------------------
    # Step 4: Render chart
    # ---------------------------------------------------------------
    logger.info("-" * 70)
    logger.info("Rendering chart …")
    if args.png:
        out_path, json_path = render_chart_png(
            flow_df=flow_df,
            ohlcv_df=ohlcv_df,
            meta=meta,
            output_dir=Path(args.output_dir),
            bucket=args.bucket,
            plot_start=args.plot_start,
            plot_end=args.plot_end,
            same_scale=args.same_scale,
        )
    else:
        out_path, json_path = render_chart(
            flow_df=flow_df,
            ohlcv_df=ohlcv_df,
            meta=meta,
            output_dir=Path(args.output_dir),
            bucket=args.bucket,
        )

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    logger.info("=" * 70)
    logger.info("DONE")
    logger.info(f"  Output : {out_path}")
    logger.info(f"  JSON   : {json_path}")
    logger.info(f"  Trades parsed       : {meta.get('trade_count', 0):,}")
    logger.info(f"  Buy aggress         : {meta.get('buy_aggress_count', 0):,}")
    logger.info(f"  Sell aggress        : {meta.get('sell_aggress_count', 0):,}")
    logger.info(f"  DV01/contract       : ${args.dv01}")
    logger.info(f"  Bucket              : {args.bucket}")
    logger.info(f"  Reset               : {args.reset}")
    logger.info(f"  TotalAggress range  : {meta.get('total_aggress_min', 0):,.0f} → {meta.get('total_aggress_max', 0):,.0f}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()

