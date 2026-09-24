#!/usr/bin/env python3
"""Core data loading and DV01-adjusted aggression aggregation."""

from __future__ import annotations

import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from scripts.totalaggress_vs_price_chart import (
    CACHE_DIR,
    DATASET,
    DEFAULT_RESET,
    classify_aggressor,
    fetch_ohlcv_price,
)

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
DEFAULT_BUCKET = "1min"
TICK_PLOT_MAX_POINTS = 80_000
DEFAULT_DV01_PATH = PROJECT_ROOT / "config" / "aggression_dv01.json"
CACHE_VERSION = "trades_v2"
BUYSELL_EMA_SPAN = 10
CACHE_RETENTION_DAYS = 30

SYMBOL_PARENT_MAP = {
    "FV": "ZF.FUT",
    "TU": "ZT.FUT",
    "TY": "ZN.FUT",
    "US": "ZB.FUT",
}

PRICE_SYMBOL_PATTERNS = {
    "FV": "FV%",
    "TU": "TU%",
    "TY": "TY%",
    "US": "US%",
}


@dataclass(frozen=True)
class AggressionRequest:
    """Canonical request shape for dashboard aggregation."""

    aggression_symbols: Tuple[str, ...]
    price_symbol: str
    start: datetime
    end: datetime
    bucket: str = DEFAULT_BUCKET
    dv01_adjust: bool = True
    reset: str = DEFAULT_RESET
    lookback_hours: float = 2.0


def normalize_symbol(symbol: str) -> str:
    """Normalize UI symbol strings to the supported front-curve codes."""
    value = str(symbol or "").strip().upper()
    if value not in SYMBOL_PARENT_MAP:
        raise ValueError(f"Unsupported symbol '{symbol}'. Use one of {sorted(SYMBOL_PARENT_MAP)}.")
    return value


def load_dv01_table(path: Path = DEFAULT_DV01_PATH) -> Dict[str, float]:
    """Load per-contract DV01 constants from JSON config."""
    if not path.exists():
        logger.warning("DV01 config not found at %s; using built-in defaults", path)
        return {"FV": 42.0, "TU": 36.0, "TY": 85.0, "US": 125.0}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {normalize_symbol(k): float(v) for k, v in raw.items()}


def parse_dashboard_datetime(value: str, *, default_to_now: bool = False) -> datetime:
    """
    Parse a dashboard datetime as Eastern time unless it is explicitly tz-aware.

    Browser datetime-local inputs arrive without a timezone. The trading desk UI
    is in New York time, so naive values are localized to America/New_York.
    """
    if not value:
        if default_to_now:
            return datetime.now(timezone.utc)
        raise ValueError("Datetime value is required.")
    text = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=EASTERN)
    return dt.astimezone(timezone.utc)


def trading_day_start(now: Optional[datetime] = None) -> datetime:
    """Return the current Globex trading-day open (18:00 ET) in UTC.

    A trading day runs 18:00 ET to 17:00 ET.  Before 18:00 ET the open is
    yesterday 18:00; at or after 18:00 ET it is today 18:00.
    """
    now_et = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
    start_et = datetime.combine(now_et.date(), time(18, 0), tzinfo=EASTERN)
    if now_et.time() < time(18, 0):
        start_et = start_et - timedelta(days=1)
    return start_et.astimezone(timezone.utc)


def default_start_time(now: Optional[datetime] = None) -> datetime:
    """Return the trading-day open for the default live window."""
    return trading_day_start(now)


def _daily_ranges(start: datetime, end: datetime) -> Iterable[Tuple[datetime, datetime]]:
    """Split a UTC range into day chunks to keep completed-day cache reusable."""
    cursor = start.astimezone(timezone.utc)
    stop = end.astimezone(timezone.utc)
    while cursor < stop:
        next_midnight = datetime.combine(cursor.date(), time(0, 0), tzinfo=timezone.utc) + timedelta(days=1)
        chunk_end = min(stop, next_midnight)
        yield cursor, chunk_end
        cursor = chunk_end


def _cache_path_for_contract_trades(parent_symbol: str, start: datetime) -> Path:
    """Return the v2 parquet cache path for parent-symbol trade pulls."""
    safe_symbol = parent_symbol.replace(".", "_")
    return CACHE_DIR / f"{CACHE_VERSION}_{safe_symbol}_{start.strftime('%Y%m%d')}.parquet"


def prune_trades_cache(
    max_age_days: int = CACHE_RETENTION_DAYS,
    cache_dir: Path = CACHE_DIR,
) -> int:
    """Delete old aggression trade-cache parquet files."""
    if not cache_dir.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    removed = 0
    for path in cache_dir.glob("trades*.parquet"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
        except Exception as exc:
            logger.warning("Could not prune cache file %s: %s", path, exc)
    logger.info("prune_trades_cache removed %s files older than %s days from %s", removed, max_age_days, cache_dir)
    return removed


def _extract_raw_symbol(row: Any) -> str:
    """Best-effort raw symbol extraction across Databento dataframe variants."""
    for column in ("raw_symbol", "symbol", "instrument_class"):
        value = row.get(column, "")
        if value is not None and str(value):
            return str(value)
    return ""


def fetch_trades_with_contract(
    start: datetime,
    end: datetime,
    symbol: str,
    *,
    cache: bool = True,
) -> pd.DataFrame:
    """
    Fetch Databento trades while preserving instrument identity.

    This intentionally does not reuse ``fetch_trades_from_api`` because that
    older helper drops ``instrument_id``. The dashboard needs that field to
    filter parent-symbol pulls to one front-month outright per day.
    """
    try:
        import databento as db
    except ImportError:
        logger.error("databento package not installed. pip install databento")
        return pd.DataFrame()

    from scripts.databento_live_state import get_databento_env

    api_key = get_databento_env().get("api_key", "")
    if not api_key:
        logger.error("No DATABENTO_API_KEY found. Set it in config/keys.env or environment.")
        return pd.DataFrame()

    cache_path = _cache_path_for_contract_trades(symbol, start)
    if cache and cache_path.exists():
        logger.info("Loading cached v2 trades from %s", cache_path)
        df = pd.read_parquet(cache_path)
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        return df

    logger.info("Fetching v2 trades from Databento API: %s %s -> %s", symbol, start, end)
    client = db.Historical(key=api_key)

    request = {
        "dataset": DATASET,
        "symbols": [symbol],
        "stype_in": "parent",
        "schema": "trades",
        "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    try:
        cost = client.metadata.get_cost(**request)
        logger.info("  Estimated API cost: $%.4f", cost)
    except Exception as exc:
        logger.warning("  Could not estimate cost: %s", exc)

    try:
        data = client.timeseries.get_range(**request)
    except Exception as exc:
        err_str = str(exc)
        if "data_end_after_available_end" in err_str and "available up to" in err_str:
            match = re.search(r"available up to '([^']+)'", err_str)
            if not match:
                logger.error("Databento API fetch failed: %s", exc)
                return pd.DataFrame()
            request["end"] = match.group(1).strip().replace(" ", "T")
            logger.warning("Data available only up to %s. Retrying.", request["end"])
            try:
                data = client.timeseries.get_range(**request)
            except Exception as retry_exc:
                logger.error("Retry also failed: %s", retry_exc)
                return pd.DataFrame()
        else:
            logger.error("Databento API fetch failed: %s", exc)
            return pd.DataFrame()

    raw_df = data.to_df()
    if raw_df.empty:
        logger.warning("API returned 0 records for %s.", symbol)
        return pd.DataFrame()

    records: List[Dict[str, Any]] = []
    for idx, row in raw_df.iterrows():
        ts = idx if isinstance(idx, pd.Timestamp) else pd.Timestamp(idx, tz="UTC")
        try:
            price = float(row.get("price", 0))
            if price > 1e6:
                price /= 1e9
            size = int(row.get("size", 0))
        except (ValueError, TypeError):
            continue
        if price <= 0 or size <= 0 or price < 80 or price > 150:
            continue
        instrument_id = row.get("instrument_id", "")
        if instrument_id is None or str(instrument_id) == "":
            instrument_id = "unknown"
        records.append({
            "ts": ts,
            "price": price,
            "size": size,
            "side": str(row.get("side", "")),
            "instrument_id": str(instrument_id),
            "raw_symbol": _extract_raw_symbol(row),
        })

    df = pd.DataFrame(records)
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df.sort_values("ts", inplace=True)
    df.reset_index(drop=True, inplace=True)

    if cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_path, index=False)
        logger.info("  Cached v2 trades to %s", cache_path)

    logger.info("Fetched %s v2 trades | %s -> %s", f"{len(df):,}", df["ts"].min(), df["ts"].max())
    return df


def _select_front_month_per_day(trades_df: pd.DataFrame) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    """Filter a parent-symbol trade set to the highest-volume outright per day."""
    if trades_df.empty or "instrument_id" not in trades_df.columns:
        return trades_df, []

    df = trades_df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["date"] = df["ts"].dt.date
    volume = df.groupby(["date", "instrument_id"], as_index=False)["size"].sum()
    front = volume.loc[volume.groupby("date")["size"].idxmax()].copy()
    front = front.rename(columns={"instrument_id": "front_instrument_id", "size": "front_volume"})
    filtered = df.merge(front[["date", "front_instrument_id"]], on="date", how="left")
    filtered = filtered[filtered["instrument_id"] == filtered["front_instrument_id"]].copy()

    contract_rows: List[Dict[str, Any]] = []
    for _, row in front.iterrows():
        date_mask = (df["date"] == row["date"]) & (df["instrument_id"] == row["front_instrument_id"])
        raw_symbols = sorted(s for s in df.loc[date_mask, "raw_symbol"].dropna().astype(str).unique() if s)
        contract_rows.append({
            "date": str(row["date"]),
            "instrument_id": str(row["front_instrument_id"]),
            "raw_symbol": raw_symbols[0] if raw_symbols else "",
            "volume": int(row["front_volume"]),
        })

    dropped = len(df) - len(filtered)
    logger.info(
        "Front-month trade filter kept %s/%s rows; dropped %s back-month rows. Contracts: %s",
        f"{len(filtered):,}",
        f"{len(df):,}",
        f"{dropped:,}",
        contract_rows,
    )
    filtered.drop(columns=["date", "front_instrument_id"], inplace=True)
    filtered.sort_values("ts", inplace=True)
    filtered.reset_index(drop=True, inplace=True)
    return filtered, contract_rows


def fetch_symbol_trades(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    cache: bool = True,
) -> pd.DataFrame:
    """
    Fetch a single parent futures symbol from Databento.

    Completed UTC day chunks use the existing parquet cache. The active trailing
    day is fetched uncached so 1-minute auto-refresh does not serve stale ticks.
    """
    symbol = normalize_symbol(symbol)
    parent = SYMBOL_PARENT_MAP[symbol]
    parts: List[pd.DataFrame] = []
    now_utc = datetime.now(timezone.utc)

    for chunk_start, chunk_end in _daily_ranges(start, end):
        use_cache = cache and chunk_end < now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        df = fetch_trades_with_contract(
            start=chunk_start,
            end=chunk_end,
            symbol=parent,
            cache=use_cache,
        )
        if not df.empty:
            df, front_contracts = _select_front_month_per_day(df)
            df["dashboard_symbol"] = symbol
            df.attrs["front_contracts"] = front_contracts
            parts.append(df)

    if not parts:
        return pd.DataFrame(columns=["ts", "price", "size", "side", "instrument_id", "raw_symbol", "dashboard_symbol"])
    combined = pd.concat(parts, ignore_index=True)
    combined.attrs["front_contracts"] = [
        contract
        for part in parts
        for contract in part.attrs.get("front_contracts", [])
    ]
    combined["ts"] = pd.to_datetime(combined["ts"], utc=True)
    combined.sort_values("ts", inplace=True)
    combined.drop_duplicates(
        subset=["ts", "price", "size", "side", "instrument_id", "dashboard_symbol"],
        inplace=True,
    )
    combined.reset_index(drop=True, inplace=True)
    return combined


def fetch_curve_trades(
    symbols: Iterable[str],
    start: datetime,
    end: datetime,
    *,
    cache: bool = True,
    max_workers: int = 4,
) -> Tuple[Dict[str, pd.DataFrame], Dict[str, str]]:
    """Fetch multiple curve symbols concurrently."""
    normalized = tuple(dict.fromkeys(normalize_symbol(s) for s in symbols))
    errors: Dict[str, str] = {}
    results: Dict[str, pd.DataFrame] = {}
    if not normalized:
        return results, errors

    with ThreadPoolExecutor(max_workers=min(max_workers, len(normalized))) as pool:
        futures = {
            pool.submit(fetch_symbol_trades, sym, start, end, cache=cache): sym
            for sym in normalized
        }
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results[sym] = fut.result()
            except Exception as exc:
                errors[sym] = str(exc)
                logger.exception("Failed to fetch Databento trades for %s", sym)

    return results, errors


SYMBOL_BAR_COLUMNS = (
    "signed_flow",
    "signed_contracts",
    "buy_flow",
    "sell_flow",
    "buy_contracts",
    "sell_contracts",
    "trade_price",
    "trade_count",
)


def _empty_flow_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "signed_flow_bucket",
            "buy_flow_bucket",
            "sell_flow_bucket",
            "buy_contracts_bucket",
            "sell_contracts_bucket",
            "total_aggress",
            "buysell_ratio_raw",
            "buysell_ratio",
            "trade_price",
        ]
    )


def _empty_symbol_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=list(SYMBOL_BAR_COLUMNS))


def trades_to_symbol_minute_bars(
    raw_df: pd.DataFrame,
    symbol: str,
    *,
    dv01_unit: float,
    bucket: str = DEFAULT_BUCKET,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Resample one symbol's prints to 1-minute flow bars. Drops tick-level data."""
    if raw_df is None or raw_df.empty:
        return _empty_symbol_bars(), {
            "trade_count": 0,
            "buy_aggress_count": 0,
            "sell_aggress_count": 0,
            "unknown_side_count": 0,
            "dv01_per_contract": float(dv01_unit),
            "flow_unit_used": float(dv01_unit),
            "front_contracts": [],
        }
    df = raw_df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["sign"] = df["side"].apply(classify_aggressor)
    unit = float(dv01_unit)
    df["signed_flow"] = df["sign"] * df["size"] * unit
    df["signed_contracts"] = df["sign"] * df["size"]
    df["buy_flow"] = (df["sign"] == 1).astype(float) * df["size"] * unit
    df["sell_flow"] = (df["sign"] == -1).astype(float) * df["size"] * unit
    df["buy_contracts"] = (df["sign"] == 1).astype(float) * df["size"]
    df["sell_contracts"] = (df["sign"] == -1).astype(float) * df["size"]
    df.set_index("ts", inplace=True)
    df.sort_index(inplace=True)
    bars = pd.DataFrame({
        "signed_flow": df["signed_flow"].resample(bucket).sum().fillna(0.0),
        "signed_contracts": df["signed_contracts"].resample(bucket).sum().fillna(0.0),
        "buy_flow": df["buy_flow"].resample(bucket).sum().fillna(0.0),
        "sell_flow": df["sell_flow"].resample(bucket).sum().fillna(0.0),
        "buy_contracts": df["buy_contracts"].resample(bucket).sum().fillna(0.0),
        "sell_contracts": df["sell_contracts"].resample(bucket).sum().fillna(0.0),
        "trade_price": df["price"].resample(bucket).last().ffill(),
        "trade_count": df["sign"].resample(bucket).count().fillna(0),
    })
    stats = {
        "trade_count": int(len(df)),
        "buy_aggress_count": int((df["sign"] == 1).sum()),
        "sell_aggress_count": int((df["sign"] == -1).sum()),
        "unknown_side_count": int((df["sign"] == 0).sum()),
        "dv01_per_contract": unit,
        "flow_unit_used": unit,
        "front_contracts": raw_df.attrs.get("front_contracts", []),
    }
    return bars, stats


def printed_minute_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """Keep only minutes that actually contain prints."""
    if bars is None or bars.empty:
        return _empty_symbol_bars()
    if "trade_count" not in bars.columns:
        return bars.copy()
    return bars.loc[bars["trade_count"].fillna(0) > 0].copy()


def upsert_minute_bars(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """Replace overlapping minutes by index; keep closed bars incoming does not include."""
    if incoming is None or incoming.empty:
        return existing if existing is not None else _empty_symbol_bars()
    if existing is None or existing.empty:
        return incoming.copy()
    current = existing.copy()
    update = incoming.copy()
    current.index = pd.to_datetime(current.index, utc=True)
    update.index = pd.to_datetime(update.index, utc=True)
    kept = current.loc[~current.index.isin(update.index)]
    return pd.concat([kept, update]).sort_index()


def _finalize_curve_bars(
    resampled: pd.DataFrame,
    *,
    reset: str,
    dv01_adjust: bool,
    per_symbol: Dict[str, Any],
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Apply session cumsum and buy/sell ratio to already-bucketed curve flow."""
    if resampled.empty:
        return _empty_flow_frame(), {"per_symbol": per_symbol}

    if reset == "daily":
        total_aggress = resampled["signed_flow_bucket"].groupby(resampled.index.date).cumsum()
    elif reset == "globex":
        session_ids = ((resampled.index.hour < 23).astype(int).diff().fillna(0).abs().cumsum())
        total_aggress = resampled["signed_flow_bucket"].groupby(session_ids).cumsum()
    else:
        total_aggress = resampled["signed_flow_bucket"].cumsum()

    if len(total_aggress):
        total_aggress = total_aggress - total_aggress.iloc[0]

    denom = resampled["buy_flow_bucket"] + resampled["sell_flow_bucket"]
    resampled = resampled.copy()
    resampled["total_aggress"] = total_aggress
    buysell_ratio_raw = (100.0 * resampled["signed_flow_bucket"] / denom).where(denom != 0)
    resampled["buysell_ratio_raw"] = buysell_ratio_raw
    resampled["buysell_ratio"] = buysell_ratio_raw.ewm(
        span=BUYSELL_EMA_SPAN,
        ignore_na=True,
        min_periods=1,
    ).mean()
    resampled.index.name = "bucket_ts"
    meta = {
        "bucket_interval": DEFAULT_BUCKET,
        "reset_rule": reset,
        "dv01_adjust": bool(dv01_adjust),
        "formula": "TotalAggress(t)=cumsum(sum_bucket(sign_i * size_i * flow_unit_i))",
        "flow_unit": "$DV01 per bp" if dv01_adjust else "contracts",
        "buysell_ratio_smoothing": f"EMA(span={BUYSELL_EMA_SPAN}) of per-bucket ratio",
        "per_symbol": per_symbol,
        "trade_count": int(sum(v.get("trade_count", 0) for v in per_symbol.values())),
        "total_aggress_min": float(resampled["total_aggress"].min()),
        "total_aggress_max": float(resampled["total_aggress"].max()),
        "total_aggress_final": float(resampled["total_aggress"].iloc[-1]),
        "time_start": str(resampled.index.min()),
        "time_end": str(resampled.index.max()),
    }
    return resampled, meta


def combine_symbol_minute_bars(
    bars_by_symbol: Dict[str, pd.DataFrame],
    *,
    dv01_adjust: bool = True,
    reset: str = DEFAULT_RESET,
    per_symbol: Optional[Dict[str, Any]] = None,
    bucket: str = DEFAULT_BUCKET,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Sum stored 1-minute symbol bars into the curve aggression frame."""
    frames = [df for df in bars_by_symbol.values() if df is not None and not df.empty]
    stats = per_symbol if per_symbol is not None else {
        symbol: {"trade_count": int(df["trade_count"].sum()) if not df.empty and "trade_count" in df.columns else 0}
        for symbol, df in bars_by_symbol.items()
    }
    if not frames:
        return _empty_flow_frame(), {"per_symbol": stats}

    signed_col = "signed_flow" if dv01_adjust else "signed_contracts"
    buy_col = "buy_flow" if dv01_adjust else "buy_contracts"
    sell_col = "sell_flow" if dv01_adjust else "sell_contracts"
    signed = pd.concat([df[signed_col] for df in frames], axis=1).sum(axis=1)
    buy = pd.concat([df[buy_col] for df in frames], axis=1).sum(axis=1)
    sell = pd.concat([df[sell_col] for df in frames], axis=1).sum(axis=1)
    buy_contracts = pd.concat([df["buy_contracts"] for df in frames], axis=1).sum(axis=1)
    sell_contracts = pd.concat([df["sell_contracts"] for df in frames], axis=1).sum(axis=1)
    prices = pd.concat([df["trade_price"] for df in frames], axis=1)
    trade_price = prices.stack().groupby(level=0).last()
    resampled = pd.DataFrame({
        "signed_flow_bucket": signed.fillna(0.0),
        "buy_flow_bucket": buy.fillna(0.0),
        "sell_flow_bucket": sell.fillna(0.0),
        "buy_contracts_bucket": buy_contracts.fillna(0.0),
        "sell_contracts_bucket": sell_contracts.fillna(0.0),
        "trade_price": trade_price,
    }).sort_index()
    flow_df, meta = _finalize_curve_bars(
        resampled,
        reset=reset,
        dv01_adjust=dv01_adjust,
        per_symbol=stats,
    )
    meta["bucket_interval"] = bucket
    return flow_df, meta


def aggregate_curve_aggression(
    trades_by_symbol: Dict[str, pd.DataFrame],
    *,
    dv01_table: Dict[str, float],
    bucket: str = DEFAULT_BUCKET,
    dv01_adjust: bool = True,
    reset: str = DEFAULT_RESET,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Classify trades and sum aggression across selected symbols."""
    bars: Dict[str, pd.DataFrame] = {}
    per_symbol: Dict[str, Dict[str, Any]] = {}
    for symbol, raw_df in trades_by_symbol.items():
        unit = float(dv01_table.get(symbol, 1.0)) if dv01_adjust else 1.0
        bar, stats = trades_to_symbol_minute_bars(
            raw_df,
            symbol,
            dv01_unit=unit,
            bucket=bucket,
        )
        bars[symbol] = bar
        per_symbol[symbol] = stats
    return combine_symbol_minute_bars(
        bars,
        dv01_adjust=True,
        reset=reset,
        per_symbol=per_symbol,
        bucket=bucket,
    )


def last_traded_price_at(index: pd.DatetimeIndex, trades_df: pd.DataFrame) -> pd.Series:
    """Forward-fill the selected symbol's last print price onto print timestamps."""
    empty = pd.Series(index=index, dtype=float)
    if index is None or len(index) == 0 or trades_df is None or trades_df.empty:
        return empty
    if "price" not in trades_df.columns:
        return empty
    work = trades_df.copy()
    if "ts" in work.columns:
        work["ts"] = pd.to_datetime(work["ts"], utc=True)
        series = work.set_index("ts")["price"]
    else:
        series = work["price"].copy()
        series.index = pd.to_datetime(series.index, utc=True)
    series = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    if series.empty:
        return empty
    series = series.groupby(level=0).last()
    target = pd.DatetimeIndex(pd.to_datetime(index, utc=True))
    unique_target = target.unique().sort_values()
    filled = series.reindex(unique_target, method="ffill")
    aligned = filled.reindex(target)
    aligned.index = index
    return aligned


def attach_last_traded_price(tick_df: pd.DataFrame, price_trades: pd.DataFrame) -> pd.DataFrame:
    """Add last_trade_price of the selected price symbol onto the print tape."""
    out = tick_df.copy()
    if out.empty:
        out["last_trade_price"] = pd.Series(dtype=float)
        return out
    out["last_trade_price"] = last_traded_price_at(out.index, price_trades)
    return out


def aggregate_tick_aggression(
    trades_by_symbol: Dict[str, pd.DataFrame],
    *,
    dv01_table: Dict[str, float],
    dv01_adjust: bool = True,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Running TotalAggress on each print, session-rebased to the first print."""
    frames: List[pd.DataFrame] = []
    per_symbol: Dict[str, Any] = {}
    for symbol, raw_df in trades_by_symbol.items():
        if raw_df is None or raw_df.empty:
            per_symbol[symbol] = {"trade_count": 0}
            continue
        df = raw_df.copy()
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df["sign"] = df["side"].apply(classify_aggressor)
        unit = float(dv01_table.get(symbol, 1.0)) if dv01_adjust else 1.0
        df["signed_flow"] = df["sign"] * df["size"].astype(float) * unit
        keep = df[["ts", "signed_flow", "price"]].copy()
        keep["dashboard_symbol"] = symbol
        keep.dropna(subset=["ts"], inplace=True)
        frames.append(keep)
        per_symbol[symbol] = {"trade_count": int(len(keep)), "dv01_per_contract": unit}
    if not frames:
        empty = pd.DataFrame(columns=["signed_flow", "total_aggress", "trade_price"])
        return empty, {"per_symbol": per_symbol, "trade_count": 0, "bucket_interval": "print"}
    all_tr = pd.concat(frames, ignore_index=True)
    all_tr.sort_values(["ts", "dashboard_symbol"], inplace=True)
    all_tr.reset_index(drop=True, inplace=True)
    total = all_tr["signed_flow"].cumsum()
    if len(total):
        total = total - total.iloc[0]
    all_tr["total_aggress"] = total
    all_tr.rename(columns={"price": "trade_price"}, inplace=True)
    all_tr.set_index("ts", inplace=True)
    all_tr.index.name = "print_ts"
    return all_tr, {
        "bucket_interval": "print",
        "reset_rule": "none",
        "dv01_adjust": bool(dv01_adjust),
        "formula": "TotalAggress(print)=cumsum(sign_i * size_i * flow_unit_i)",
        "flow_unit": "$DV01 per bp" if dv01_adjust else "contracts",
        "per_symbol": per_symbol,
        "trade_count": int(len(all_tr)),
        "total_aggress_min": float(all_tr["total_aggress"].min()),
        "total_aggress_max": float(all_tr["total_aggress"].max()),
        "total_aggress_final": float(all_tr["total_aggress"].iloc[-1]),
        "time_start": str(all_tr.index.min()),
        "time_end": str(all_tr.index.max()),
    }


def fetch_price_series(
    price_symbol: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Load the dependent price/yield symbol from PostgreSQL OHLCV."""
    symbol = normalize_symbol(price_symbol)
    return fetch_ohlcv_price(start=start, end=end, symbol_pattern=PRICE_SYMBOL_PATTERNS[symbol])


def price_series_from_trades(trades_df: pd.DataFrame, bucket: str = DEFAULT_BUCKET) -> pd.DataFrame:
    """Build a price series from the selected symbol's trade prints."""
    if trades_df.empty:
        return pd.DataFrame(columns=["price"])
    df = trades_df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df.set_index("ts", inplace=True)
    price = df["price"].resample(bucket).last().ffill().dropna()
    return pd.DataFrame({"price": price})


def build_aggression_dataset(
    request: AggressionRequest,
    *,
    cache: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Fetch all inputs and return aggregated flow, price, and metadata."""
    dv01_table = load_dv01_table()
    trades_by_symbol, fetch_errors = fetch_curve_trades(
        request.aggression_symbols,
        request.start,
        request.end,
        cache=cache,
    )
    flow_df, flow_meta = aggregate_curve_aggression(
        trades_by_symbol,
        dv01_table=dv01_table,
        bucket=request.bucket,
        dv01_adjust=request.dv01_adjust,
        reset=request.reset,
    )
    tick_trades = dict(trades_by_symbol)
    if request.price_symbol not in tick_trades:
        extra, extra_errors = fetch_curve_trades(
            (request.price_symbol,),
            request.start,
            request.end,
            cache=cache,
        )
        tick_trades[request.price_symbol] = extra.get(request.price_symbol, pd.DataFrame())
        fetch_errors = {**fetch_errors, **extra_errors}
    tick_df, tick_meta = aggregate_tick_aggression(
        {symbol: tick_trades.get(symbol, pd.DataFrame()) for symbol in request.aggression_symbols},
        dv01_table=dv01_table,
        dv01_adjust=request.dv01_adjust,
    )
    tick_df = attach_last_traded_price(tick_df, tick_trades.get(request.price_symbol, pd.DataFrame()))
    price_df = fetch_price_series(request.price_symbol, request.start, request.end)
    price_source = "postgres_ohlcv"
    if price_df.empty:
        price_df = price_series_from_trades(
            trades_by_symbol.get(request.price_symbol, pd.DataFrame()),
            bucket=request.bucket,
        )
        price_source = "databento_trade_prints"
    meta = {
        "request": {
            "aggression_symbols": list(request.aggression_symbols),
            "price_symbol": request.price_symbol,
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
            "bucket": request.bucket,
            "dv01_adjust": request.dv01_adjust,
        },
        "dv01_table": dv01_table,
        "fetch_errors": fetch_errors,
        "flow": flow_meta,
        "tick_flow": tick_df,
        "tick_flow_meta": tick_meta,
        "price_rows": int(len(price_df)),
        "price_source": price_source,
    }
    return flow_df, price_df, meta

