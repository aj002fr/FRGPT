#!/usr/bin/env python3
"""
CPI Two-Factor PCA Pre/Post Dashboard (Standalone HTML).

Builds a self-contained HTML dashboard that:
1) Loads CPI component data from Y:/Meini/bbg_event_fields.xlsx
2) Joins to event timestamps from workspace/economic_events.db
3) Computes historical pre/post window breakeven moves in bps
4) Fits PCA factors for pre-event uncertainty and post-event adjusted surprise
5) Computes bucket stats and factor-to-bps impact estimates
6) Exports a single standalone HTML with embedded JSON + JS
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rules.treasury_price_conversion import points_to_bps
from src.servers.distro_analytics.db_reader import add_timezone, get_ohlcv_from_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

EPS = 1e-9
DEFAULT_EXCEL_PATH = Path("Y:/Meini/bbg_event_fields.xlsx")
DEFAULT_DB_PATH = PROJECT_ROOT / "workspace" / "economic_events.db"


@dataclass
class EventSample:
    date: str
    event_dt: pd.Timestamp
    mom_actual: float
    mom_consensus: float
    mom_cons_std: float
    core_mom_actual: float
    core_mom_consensus: float
    core_mom_cons_std: float
    mom_yoy_level: float
    core_yoy_level: float
    pre_bps: float
    post_bps: float
    pre_cc_bps: float
    post_cc_bps: float


def _safe_float(val: object) -> Optional[float]:
    if val is None:
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(num):
        return None
    return float(num)


def _parse_date_string(val: object) -> Optional[str]:
    if val is None:
        return None
    try:
        dt = pd.to_datetime(val, errors="coerce")
    except Exception:
        return None
    if pd.isna(dt):
        return None
    return dt.strftime("%Y-%m-%d")


def _normalize_label(text: object) -> str:
    if text is None:
        return ""
    s = str(text).strip().lower()
    if not s:
        return ""
    if "(" in s:
        s = s.split("(", 1)[0].strip()
    return " ".join(s.split())


def _classify_component(label: str) -> Optional[str]:
    # Check core aliases first so "core cpi mom" doesn't get captured by "cpi mom".
    aliases = [
        ("core_mom", ["core cpi mom", "core inflation rate mom", "core cpi m/m"]),
        ("core_yoy", ["core cpi yoy", "core inflation rate yoy", "core cpi y/y"]),
        ("mom", ["cpi mom", "inflation rate mom", "headline cpi mom", "cpi m/m"]),
        ("yoy", ["cpi yoy", "inflation rate yoy", "headline cpi yoy", "cpi y/y"]),
    ]
    for key, items in aliases:
        for needle in items:
            if needle in label:
                return key
    return None


def _find_cpi_sheet(workbook: Dict[str, pd.DataFrame]) -> Tuple[str, pd.DataFrame]:
    best_name: Optional[str] = None
    best_df: Optional[pd.DataFrame] = None
    best_score = -1
    for sheet_name, df in workbook.items():
        score = 0
        if "cpi" in sheet_name.lower():
            score += 3
        scan = df.head(30).fillna("").astype(str).to_numpy().ravel().tolist()
        blob = " ".join(str(x).lower() for x in scan)
        for token in ("core cpi", "cpi mom", "cpi yoy"):
            if token in blob:
                score += 2
        if score > best_score:
            best_score = score
            best_name = sheet_name
            best_df = df
    if best_df is None or best_name is None:
        raise ValueError("Could not find a CPI sheet in the workbook.")
    return best_name, best_df


def load_cpi_excel_components(excel_path: Path, start_date: str) -> pd.DataFrame:
    """
    Parse CPI sheet blocks from Bloomberg workbook.
    Assumes each component block is [Date, Actual, Consensus, Std Dev] across columns.
    """
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    wb = pd.read_excel(excel_path, sheet_name=None, header=None)
    sheet_name, df = _find_cpi_sheet(wb)
    logger.info("Using CPI sheet: %s", sheet_name)

    component_positions: Dict[str, Tuple[int, int]] = {}
    for row in range(min(len(df), 30)):
        for col in range(df.shape[1]):
            raw = df.iat[row, col]
            label = _normalize_label(raw)
            comp = _classify_component(label)
            if comp and comp not in component_positions:
                component_positions[comp] = (row, col)

    required = {"mom", "core_mom", "yoy", "core_yoy"}
    missing = sorted(required - set(component_positions))
    if missing:
        raise ValueError(
            f"Missing CPI component blocks in Excel sheet: {missing}. "
            "Expected labels for MoM/Core MoM and YoY/Core YoY."
        )

    by_date: Dict[str, Dict[str, float]] = {}
    for comp, (label_row, base_col) in component_positions.items():
        date_col = base_col
        actual_col = base_col + 1
        consensus_col = base_col + 2
        std_col = base_col + 3
        start_row = label_row + 1

        for r in range(start_row, len(df)):
            date_str = _parse_date_string(df.iat[r, date_col] if date_col < df.shape[1] else None)
            if not date_str:
                continue
            if date_str < start_date:
                continue
            actual = _safe_float(df.iat[r, actual_col] if actual_col < df.shape[1] else None)
            consensus = _safe_float(df.iat[r, consensus_col] if consensus_col < df.shape[1] else None)
            std_val = _safe_float(df.iat[r, std_col] if std_col < df.shape[1] else None)

            row = by_date.setdefault(date_str, {})
            if comp == "mom":
                if actual is not None:
                    row["mom_actual"] = actual
                if consensus is not None:
                    row["mom_consensus"] = consensus
                if std_val is not None:
                    row["mom_cons_std"] = std_val
            elif comp == "core_mom":
                if actual is not None:
                    row["core_mom_actual"] = actual
                if consensus is not None:
                    row["core_mom_consensus"] = consensus
                if std_val is not None:
                    row["core_mom_cons_std"] = std_val
            elif comp == "yoy":
                if actual is not None:
                    row["mom_yoy_level"] = actual
            elif comp == "core_yoy":
                if actual is not None:
                    row["core_yoy_level"] = actual

    rows: List[Dict[str, object]] = []
    for date_str, vals in sorted(by_date.items()):
        rows.append({"date": date_str, **vals})
    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError("No CPI rows parsed from Excel after start_date filter.")
    logger.info("Parsed %d CPI rows from Excel", len(out))
    return out


def load_cpi_event_timestamps(db_path: Path, start_date: str) -> pd.DataFrame:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    sql = """
    SELECT timestamp
    FROM bbg_data
    WHERE date(timestamp) >= date(?)
      AND LOWER(db_event_name) = LOWER('Core Inflation Rate MoM')
    ORDER BY timestamp
    """
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(sql, (start_date,)).fetchall()

    if not rows:
        raise ValueError("No CPI timestamps found in bbg_data for Core Inflation Rate MoM.")

    data = []
    for (ts,) in rows:
        dt = pd.to_datetime(ts, errors="coerce")
        if pd.isna(dt):
            continue
        if dt.tzinfo is None:
            dt = dt.tz_localize("US/Eastern")
        else:
            dt = dt.tz_convert("US/Eastern")
        data.append({"date": dt.strftime("%Y-%m-%d"), "event_dt": dt})
    out = pd.DataFrame(data).drop_duplicates(subset=["date"], keep="last")
    logger.info("Loaded %d CPI event timestamps from DB", len(out))
    return out


def _prepare_ohlcv(symbol_pattern: str, start_date: str) -> pd.DataFrame:
    df = get_ohlcv_from_db(symbol_pattern=symbol_pattern, start_date=start_date)
    df = add_timezone(df, source_tz="UTC", target_tz="US/Eastern").reset_index()
    df["et_ts"] = pd.to_datetime(df["US/Eastern Timezone"], errors="coerce")
    df = df.dropna(subset=["et_ts"]).sort_values("et_ts").reset_index(drop=True)
    return df


def _prior_trading_day(dt: pd.Timestamp) -> pd.Timestamp:
    d = dt - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _window_extent_bps(window_df: pd.DataFrame) -> Optional[float]:
    """
    One-sided max excursion from window open, in bps.
    """
    if window_df.empty:
        return None
    window_df = window_df.sort_values("et_ts")
    w_open = _safe_float(window_df["Open"].iloc[0])
    w_high = _safe_float(window_df["High"].max())
    w_low = _safe_float(window_df["Low"].min())
    if w_open is None or w_high is None or w_low is None:
        return None
    points = max(w_high - w_open, w_open - w_low)
    return float(points_to_bps(points))


def _window_close_to_close_bps(window_df: pd.DataFrame) -> Optional[float]:
    """
    Signed close-to-close return over the window, in bps.
    Positive = end close above start close; negative = end close below start close.
    """
    if window_df.empty:
        return None
    window_df = window_df.sort_values("et_ts")
    close = pd.to_numeric(window_df["Close"], errors="coerce").astype(float)
    close = close[np.isfinite(close.to_numpy(dtype=float))]
    if close.empty:
        return None
    points = float(close.iloc[-1] - close.iloc[0])
    return float(points_to_bps(points))


def _post_clock_extent_series_bps(
    post_df: pd.DataFrame,
    post_start: pd.Timestamp,
) -> List[Dict[str, float | int | str | None]]:
    """
    Clock-time breakeven-style max extent from post window open.
    Anchors: 8:30am, 9:00am, 10:00am, ..., 3:00pm ET.
    """
    if post_df.empty:
        return []
    schedule = [
        ("08:30", 0),
        ("09:00", 30),
        ("10:00", 90),
        ("11:00", 150),
        ("12:00", 210),
        ("13:00", 270),
        ("14:00", 330),
        ("15:00", 390),
    ]
    out: List[Dict[str, float | int | str | None]] = []
    for clock_label, mins in schedule:
        cutoff = post_start + timedelta(minutes=mins)
        seg = post_df[(post_df["et_ts"] >= post_start) & (post_df["et_ts"] <= cutoff)]
        be_bps = _window_extent_bps(seg)
        out.append(
            {
                "minute_offset": int(mins),
                "clock_label": clock_label,
                "be_bps": float(be_bps) if be_bps is not None else None,
            }
        )
    return out


def _window_candles(window_df: pd.DataFrame, start_dt: pd.Timestamp) -> List[Dict[str, float | str]]:
    """
    Serialize OHLC candles for a shared event-aligned axis.
    """
    if window_df.empty:
        return []
    w = window_df.sort_values("et_ts").copy()
    out: List[Dict[str, float | str]] = []
    for _, row in w.iterrows():
        ts = pd.to_datetime(row.get("et_ts"), errors="coerce")
        o = _safe_float(row.get("Open"))
        h = _safe_float(row.get("High"))
        l = _safe_float(row.get("Low"))
        c = _safe_float(row.get("Close"))
        if pd.isna(ts) or None in (o, h, l, c):
            continue
        minutes = float((ts - start_dt).total_seconds() / 60.0)
        out.append(
            {
                "aligned_ts": _format_aligned_ts(minutes),
                "t_min": minutes,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
            }
        )
    return out


def _format_aligned_ts(minutes_from_start: float, anchor: str = "2000-01-01 00:00:00") -> str:
    base = pd.Timestamp(anchor)
    ts = base + pd.to_timedelta(minutes_from_start, unit="m")
    return ts.strftime("%Y-%m-%dT%H:%M:%S")


def _normalized_path_bps(window_df: pd.DataFrame, start_dt: pd.Timestamp) -> List[Dict[str, float | str]]:
    """
    Build normalized window path points in bps with aligned timestamps.
    Alignment uses a synthetic anchor date so all event paths share one x-axis basis.
    """
    if window_df.empty:
        return []
    w = window_df.sort_values("et_ts").copy()
    close = pd.to_numeric(w["Close"], errors="coerce").astype(float)
    w = w[np.isfinite(close.to_numpy(dtype=float))].copy()
    if w.empty:
        return []
    close = pd.to_numeric(w["Close"], errors="coerce").astype(float)
    base = float(close.iloc[0])
    if not np.isfinite(base):
        return []
    out: List[Dict[str, float | str]] = []
    for _, row in w.iterrows():
        ts = pd.to_datetime(row["et_ts"], errors="coerce")
        c = _safe_float(row.get("Close"))
        if pd.isna(ts) or c is None:
            continue
        minutes = float((ts - start_dt).total_seconds() / 60.0)
        bps = float(points_to_bps(c - base))
        out.append(
            {
                "aligned_ts": _format_aligned_ts(minutes),
                "t_min": minutes,
                "bps": bps,
            }
        )
    return out


def _window_range_bps(window_df: pd.DataFrame) -> Optional[float]:
    """
    Realized range (high-low) in bps over a window. Uses only window OHLC.
    """
    if window_df.empty:
        return None
    window_df = window_df.sort_values("et_ts")
    w_high = _safe_float(window_df["High"].max())
    w_low = _safe_float(window_df["Low"].min())
    if w_high is None or w_low is None:
        return None
    return float(points_to_bps(w_high - w_low))


def _window_vol_bps(window_df: pd.DataFrame) -> Optional[float]:
    """
    Realized volatility proxy: std of 1-minute close-to-close changes, in bps.
    Note: for TY futures this is a reasonable "uncertainty" proxy when cons_std is flat.
    """
    if window_df.empty or "Close" not in window_df.columns:
        return None
    window_df = window_df.sort_values("et_ts")
    close = pd.to_numeric(window_df["Close"], errors="coerce").astype(float)
    dpx = close.diff().to_numpy(dtype=float)
    dpx = dpx[np.isfinite(dpx)]
    if len(dpx) < 5:
        return None
    # points_to_bps expects a points delta; apply elementwise then compute std.
    bps = np.array([points_to_bps(float(x)) for x in dpx], dtype=float)
    bps = bps[np.isfinite(bps)]
    if len(bps) < 5:
        return None
    return float(np.std(bps, ddof=0))


def load_market_windows(
    ohlcv_df: pd.DataFrame, event_times: pd.DataFrame
) -> pd.DataFrame:
    out_rows: List[Dict[str, object]] = []
    for _, row in event_times.iterrows():
        event_dt = row["event_dt"]
        if pd.isna(event_dt):
            continue
        pre_day = _prior_trading_day(event_dt)
        pre_start = pre_day.replace(hour=15, minute=0, second=0, microsecond=0)
        pre_end = event_dt.replace(hour=8, minute=29, second=0, microsecond=0)
        post_start = event_dt.replace(hour=8, minute=30, second=0, microsecond=0)
        post_end = event_dt.replace(hour=17, minute=0, second=0, microsecond=0)

        pre_df = ohlcv_df[(ohlcv_df["et_ts"] >= pre_start) & (ohlcv_df["et_ts"] <= pre_end)]
        post_df = ohlcv_df[(ohlcv_df["et_ts"] >= post_start) & (ohlcv_df["et_ts"] <= post_end)]
        full_df = ohlcv_df[(ohlcv_df["et_ts"] >= pre_start) & (ohlcv_df["et_ts"] <= post_end)]
        pre_bps = _window_extent_bps(pre_df)
        post_bps = _window_extent_bps(post_df)
        pre_cc_bps = _window_close_to_close_bps(pre_df)
        post_cc_bps = _window_close_to_close_bps(post_df)
        post_hourly_be_bps = _post_clock_extent_series_bps(post_df=post_df, post_start=post_start)
        pre_path = _normalized_path_bps(pre_df, pre_start)
        post_path = _normalized_path_bps(post_df, post_start)
        full_candles = _window_candles(full_df, pre_start)
        # Market-derived uncertainty proxies, strictly BEFORE pre_start.
        # Use trailing window ending at pre_start to avoid circularity with the pre_bps target.
        trail_start = pre_start - timedelta(days=7)
        trail_df = ohlcv_df[(ohlcv_df["et_ts"] >= trail_start) & (ohlcv_df["et_ts"] < pre_start)]
        trail_range_bps = _window_range_bps(trail_df)
        trail_vol_bps = _window_vol_bps(trail_df)
        if pre_bps is None or post_bps is None or pre_cc_bps is None or post_cc_bps is None:
            continue
        if trail_range_bps is None or trail_vol_bps is None:
            continue
        if not pre_path or not post_path or not full_candles:
            continue
        out_rows.append(
            {
                "date": row["date"],
                "event_dt": event_dt,
                "pre_bps": pre_bps,
                "post_bps": post_bps,
                "pre_cc_bps": pre_cc_bps,
                "post_cc_bps": post_cc_bps,
                "post_hourly_be_bps": post_hourly_be_bps,
                "pre_path": pre_path,
                "post_path": post_path,
                "full_candles": full_candles,
                "trail_range_bps": trail_range_bps,
                "trail_vol_bps": trail_vol_bps,
            }
        )
    out = pd.DataFrame(out_rows)
    logger.info("Computed market windows for %d events", len(out))
    return out


def build_training_table(cpi_df: pd.DataFrame, ts_df: pd.DataFrame, market_df: pd.DataFrame) -> pd.DataFrame:
    def _last_valid(series: pd.Series) -> float:
        vals = series.dropna()
        return vals.iloc[-1] if len(vals) else np.nan

    cpi = cpi_df.copy()
    cpi["date_dt"] = pd.to_datetime(cpi["date"], errors="coerce")
    cpi = cpi.dropna(subset=["date_dt"]).sort_values("date_dt")

    ts = ts_df.copy()
    ts["date_dt"] = pd.to_datetime(ts["date"], errors="coerce")
    ts = ts.dropna(subset=["date_dt"]).sort_values("date_dt")

    market = market_df.copy()
    market["date_dt"] = pd.to_datetime(market["date"], errors="coerce")
    market = market.dropna(subset=["date_dt"]).sort_values("date_dt")

    # Exact-date join for primary MoM and Core MoM fields.
    primary_cols = [
        "date",
        "date_dt",
        "mom_actual",
        "mom_consensus",
        "mom_cons_std",
        "core_mom_actual",
        "core_mom_consensus",
        "core_mom_cons_std",
    ]
    primary = cpi[primary_cols].drop_duplicates(subset=["date"], keep="last")
    df = ts.merge(primary, on=["date", "date_dt"], how="inner")
    df = df.merge(
        market[
            [
                "date",
                "pre_bps",
                "post_bps",
                "pre_cc_bps",
                "post_cc_bps",
                "post_hourly_be_bps",
                "pre_path",
                "post_path",
                "full_candles",
                "trail_range_bps",
                "trail_vol_bps",
            ]
        ],
        on="date",
        how="inner",
    )

    # YoY fields can be published on separate rows; carry forward last known <= event date.
    yoy = cpi[["date_dt", "mom_yoy_level", "core_yoy_level"]].copy()
    yoy = yoy.dropna(subset=["date_dt"])
    yoy = yoy.dropna(subset=["mom_yoy_level", "core_yoy_level"], how="all")
    yoy = (
        yoy.sort_values("date_dt")
        .groupby("date_dt", as_index=False)
        .agg({"mom_yoy_level": _last_valid, "core_yoy_level": _last_valid})
    )
    df = pd.merge_asof(
        df.sort_values("date_dt"),
        yoy,
        on="date_dt",
        direction="backward",
    )

    required_cols = [
        "mom_actual",
        "mom_consensus",
        "mom_cons_std",
        "core_mom_actual",
        "core_mom_consensus",
        "core_mom_cons_std",
        "mom_yoy_level",
        "core_yoy_level",
        "pre_bps",
        "post_bps",
        "pre_cc_bps",
        "post_cc_bps",
        "post_hourly_be_bps",
        "pre_path",
        "post_path",
        "full_candles",
        "trail_range_bps",
        "trail_vol_bps",
        "event_dt",
    ]
    before = len(df)
    for c in required_cols:
        if c not in df.columns:
            df[c] = np.nan
    df = df.dropna(subset=required_cols).copy()
    df = df[(df["mom_cons_std"] > EPS) & (df["core_mom_cons_std"] > EPS)].copy()
    df = df.sort_values("event_dt").reset_index(drop=True)
    logger.info("Training rows after quality filters: %d (dropped %d)", len(df), before - len(df))
    return df


def _fit_linear(y: np.ndarray, x: np.ndarray) -> Tuple[float, float]:
    X = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(beta[0]), float(beta[1])


def rolling_expected_from_yoy(
    actual: np.ndarray,
    yoy: np.ndarray,
    window: int,
    min_obs: int = 12,
) -> Tuple[np.ndarray, List[Tuple[float, float]]]:
    n = len(actual)
    expected = np.full(n, np.nan, dtype=float)
    coeffs: List[Tuple[float, float]] = [(np.nan, np.nan) for _ in range(n)]

    for i in range(n):
        left = max(0, i - window)
        if i <= left:
            continue
        y_train = actual[left:i]
        x_train = yoy[left:i]
        valid = np.isfinite(y_train) & np.isfinite(x_train)
        y_train = y_train[valid]
        x_train = x_train[valid]
        if len(y_train) < min_obs:
            continue
        a, b = _fit_linear(y_train, x_train)
        coeffs[i] = (a, b)
        expected[i] = a + b * yoy[i]
    return expected, coeffs


def _winsorize_series(x: np.ndarray, p_lo: float = 1.0, p_hi: float = 99.0) -> np.ndarray:
    if len(x) == 0:
        return x
    lo = np.nanpercentile(x, p_lo)
    hi = np.nanpercentile(x, p_hi)
    return np.clip(x, lo, hi)


def fit_post_trend_models(df: pd.DataFrame, window: int) -> pd.DataFrame:
    min_obs = max(6, min(8, window - 1))
    mom_expected, mom_coeffs = rolling_expected_from_yoy(
        actual=df["mom_actual"].to_numpy(dtype=float),
        yoy=df["mom_yoy_level"].to_numpy(dtype=float),
        window=window,
        min_obs=min_obs,
    )
    core_expected, core_coeffs = rolling_expected_from_yoy(
        actual=df["core_mom_actual"].to_numpy(dtype=float),
        yoy=df["core_yoy_level"].to_numpy(dtype=float),
        window=window,
        min_obs=min_obs,
    )

    out = df.copy()
    out["mom_expected"] = mom_expected
    out["core_mom_expected"] = core_expected
    out["mom_a"] = [a for a, _ in mom_coeffs]
    out["mom_b"] = [b for _, b in mom_coeffs]
    out["core_a"] = [a for a, _ in core_coeffs]
    out["core_b"] = [b for _, b in core_coeffs]
    out["mom_z_adj"] = (out["mom_actual"] - out["mom_expected"]) / out["mom_cons_std"].clip(lower=EPS)
    out["core_mom_z_adj"] = (out["core_mom_actual"] - out["core_mom_expected"]) / out["core_mom_cons_std"].clip(lower=EPS)
    out["mom_z_adj"] = _winsorize_series(out["mom_z_adj"].to_numpy(dtype=float))
    out["core_mom_z_adj"] = _winsorize_series(out["core_mom_z_adj"].to_numpy(dtype=float))
    out = out.dropna(subset=["mom_expected", "core_mom_expected", "mom_z_adj", "core_mom_z_adj"]).copy()
    out = out.reset_index(drop=True)
    return out


def _fix_eigenvector_signs(loadings: np.ndarray, scores: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    for i in range(loadings.shape[1]):
        col = loadings[:, i]
        idx = int(np.argmax(np.abs(col)))
        if col[idx] < 0:
            loadings[:, i] *= -1.0
            scores[:, i] *= -1.0
    return loadings, scores


def fit_pca_mode(X: np.ndarray) -> Dict[str, object]:
    mu = X.mean(axis=0)
    sigma = X.std(axis=0, ddof=0)
    sigma = np.where(sigma < EPS, 1.0, sigma)
    Xz = (X - mu) / sigma
    cov = np.cov(Xz, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    eigvals = eigvals[order]
    eigvecs = eigvecs[:, order]
    scores = Xz @ eigvecs
    eigvecs, scores = _fix_eigenvector_signs(eigvecs, scores)
    explained = eigvals / max(float(eigvals.sum()), EPS)
    weights = explained / max(float(explained.sum()), EPS)
    composite = scores @ weights
    return {
        "mu": mu.tolist(),
        "sigma": sigma.tolist(),
        "loadings": eigvecs.tolist(),
        "explained_variance_ratio": explained.tolist(),
        "weights": weights.tolist(),
        "factor_scores": scores.tolist(),
        "composite_scores": composite.tolist(),
    }


def _quantile_edges(values: np.ndarray, bucket_count: int) -> List[float]:
    probs = np.linspace(0.0, 1.0, bucket_count + 1)
    edges = np.quantile(values, probs).tolist()
    for i in range(1, len(edges)):
        if edges[i] <= edges[i - 1]:
            edges[i] = edges[i - 1] + 1e-6
    return [float(e) for e in edges]


def _assign_bucket(value: float, edges: List[float]) -> int:
    if value <= edges[0]:
        return 0
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if (value >= lo) and (value < hi or (i == len(edges) - 2 and value <= hi)):
            return i
    return len(edges) - 2


def build_buckets(scores: np.ndarray, target_bps: np.ndarray, bucket_count: int) -> Dict[str, object]:
    edges = _quantile_edges(scores, bucket_count)
    stats: List[Dict[str, object]] = []
    for i in range(bucket_count):
        lo, hi = edges[i], edges[i + 1]
        if i == bucket_count - 1:
            mask = (scores >= lo) & (scores <= hi)
        else:
            mask = (scores >= lo) & (scores < hi)
        vals = target_bps[mask]
        stats.append(
            {
                "bucket": i,
                "low": float(lo),
                "high": float(hi),
                "count": int(mask.sum()),
                "mean_bps": float(np.nanmean(vals)) if len(vals) else None,
            }
        )
    return {"edges": edges, "stats": stats}


def estimate_factor_impacts(factors: np.ndarray, y_bps: np.ndarray) -> Dict[str, float]:
    X = np.column_stack([factors[:, 0], factors[:, 1], np.ones(len(factors))])
    beta, *_ = np.linalg.lstsq(X, y_bps, rcond=None)
    y_hat = X @ beta
    ss_res = float(np.sum((y_bps - y_hat) ** 2))
    ss_tot = float(np.sum((y_bps - np.mean(y_bps)) ** 2))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > EPS else 0.0
    rmse = float(np.sqrt(np.mean((y_bps - y_hat) ** 2)))
    return {
        "beta_factor1": float(beta[0]),
        "beta_factor2": float(beta[1]),
        "intercept": float(beta[2]),
        "r2": float(r2),
        "rmse": float(rmse),
    }


def estimate_factor_impacts_abs(factors: np.ndarray, y_bps: np.ndarray) -> Dict[str, float]:
    """
    Extent-friendly regression: y ~ b1*|F1| + b2*|F2| + c
    Useful when y is always positive (max extent) and direction cancels out in linear fits.
    """
    X = np.column_stack([np.abs(factors[:, 0]), np.abs(factors[:, 1]), np.ones(len(factors))])
    beta, *_ = np.linalg.lstsq(X, y_bps, rcond=None)
    y_hat = X @ beta
    ss_res = float(np.sum((y_bps - y_hat) ** 2))
    ss_tot = float(np.sum((y_bps - np.mean(y_bps)) ** 2))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > EPS else 0.0
    rmse = float(np.sqrt(np.mean((y_bps - y_hat) ** 2)))
    return {
        "beta_factor1": float(beta[0]),
        "beta_factor2": float(beta[1]),
        "intercept": float(beta[2]),
        "r2": float(r2),
        "rmse": float(rmse),
        "feature_transform": "abs",
    }


def _factor_meaning_text(
    loadings: np.ndarray,
    feature1_name: str,
    feature2_name: str,
    mode_label: str,
) -> Dict[str, str]:
    f1 = loadings[:, 0]
    f2 = loadings[:, 1]
    same_sign_f1 = np.sign(f1[0]) == np.sign(f1[1])
    same_sign_f2 = np.sign(f2[0]) == np.sign(f2[1])
    dom1 = feature1_name if abs(f1[0]) >= abs(f1[1]) else feature2_name
    dom2 = feature1_name if abs(f2[0]) >= abs(f2[1]) else feature2_name

    if same_sign_f1:
        f1_text = (
            f"{mode_label} Factor 1 is a common-direction factor: "
            f"{feature1_name} and {feature2_name} tend to move together."
        )
    else:
        f1_text = (
            f"{mode_label} Factor 1 is a spread/mix factor: "
            f"it captures relative imbalance between {feature1_name} and {feature2_name}."
        )
    if same_sign_f2:
        f2_text = (
            f"{mode_label} Factor 2 is a secondary common-direction factor, "
            f"with stronger emphasis on {dom2}."
        )
    else:
        f2_text = (
            f"{mode_label} Factor 2 is a secondary spread factor, "
            f"with stronger emphasis on {dom2}."
        )
    return {"factor1": f1_text, "factor2": f2_text, "dominant1": dom1, "dominant2": dom2}


def _latest_valid_coeff(df: pd.DataFrame, a_col: str, b_col: str) -> Tuple[float, float]:
    valid = df[[a_col, b_col]].dropna()
    if valid.empty:
        return 0.0, 0.0
    row = valid.iloc[-1]
    return float(row[a_col]), float(row[b_col])


def _default_current_inputs(df: pd.DataFrame) -> Dict[str, float]:
    r = df.iloc[-1]
    return {
        "mom_actual": float(r["mom_actual"]),
        "mom_consensus": float(r["mom_consensus"]),
        "mom_cons_std": float(r["mom_cons_std"]),
        "core_mom_actual": float(r["core_mom_actual"]),
        "core_mom_consensus": float(r["core_mom_consensus"]),
        "core_mom_cons_std": float(r["core_mom_cons_std"]),
        "mom_yoy_level": float(r["mom_yoy_level"]),
        "core_yoy_level": float(r["core_yoy_level"]),
        "trail_range_bps": float(r["trail_range_bps"]),
        "trail_vol_bps": float(r["trail_vol_bps"]),
    }


def build_html_payload(
    df: pd.DataFrame,
    pre_pca: Dict[str, object],
    pre_mkt_pca: Dict[str, object],
    post_pca: Dict[str, object],
    pre_impacts: Dict[str, Dict[str, float]],
    pre_mkt_impacts: Dict[str, Dict[str, float]],
    post_impacts: Dict[str, Dict[str, float]],
    pre_buckets: Dict[str, object],
    pre_mkt_buckets: Dict[str, object],
    post_buckets: Dict[str, object],
    start_date: str,
    trend_window: int,
    symbol: str,
    pre_feature_source: str,
    warnings: List[str],
) -> Dict[str, object]:
    pre_factors = np.array(pre_pca["factor_scores"], dtype=float)
    pre_mkt_factors = np.array(pre_mkt_pca["factor_scores"], dtype=float)
    post_factors = np.array(post_pca["factor_scores"], dtype=float)
    pre_scores = np.array(pre_pca["composite_scores"], dtype=float)
    pre_mkt_scores = np.array(pre_mkt_pca["composite_scores"], dtype=float)
    post_scores = np.array(post_pca["composite_scores"], dtype=float)

    hist_rows = []
    for i, row in df.iterrows():
        hist_rows.append(
            {
                "date": row["date"],
                "pre_bps": float(row["pre_bps"]),
                "post_bps": float(row["post_bps"]),
                "pre_cc_bps": float(row["pre_cc_bps"]),
                "post_cc_bps": float(row["post_cc_bps"]),
                "post_hourly_be_bps": row["post_hourly_be_bps"] if isinstance(row["post_hourly_be_bps"], list) else [],
                "pre_factor1": float(pre_factors[i, 0]),
                "pre_factor2": float(pre_factors[i, 1]),
                "pre_mkt_factor1": float(pre_mkt_factors[i, 0]),
                "pre_mkt_factor2": float(pre_mkt_factors[i, 1]),
                "post_factor1": float(post_factors[i, 0]),
                "post_factor2": float(post_factors[i, 1]),
                "pre_score": float(pre_scores[i]),
                "pre_mkt_score": float(pre_mkt_scores[i]),
                "post_score": float(post_scores[i]),
                "pre_path": row["pre_path"] if isinstance(row["pre_path"], list) else [],
                "post_path": row["post_path"] if isinstance(row["post_path"], list) else [],
                "full_candles": row["full_candles"] if isinstance(row["full_candles"], list) else [],
                "trail_range_bps": float(row["trail_range_bps"]),
                "trail_vol_bps": float(row["trail_vol_bps"]),
            }
        )

    mom_a, mom_b = _latest_valid_coeff(df, "mom_a", "mom_b")
    core_a, core_b = _latest_valid_coeff(df, "core_a", "core_b")
    pre_loadings = np.array(pre_pca["loadings"], dtype=float)
    pre_mkt_loadings = np.array(pre_mkt_pca["loadings"], dtype=float)
    post_loadings = np.array(post_pca["loadings"], dtype=float)
    pre_f1_name = "headline consensus std dev" if pre_feature_source == "cons_std" else "headline consensus level"
    pre_f2_name = "core consensus std dev" if pre_feature_source == "cons_std" else "core consensus level"
    pre_meaning = _factor_meaning_text(pre_loadings, pre_f1_name, pre_f2_name, "Pre-event")
    pre_mkt_meaning = _factor_meaning_text(
        pre_mkt_loadings,
        "trailing range (bps)",
        "trailing vol (bps)",
        "Pre-event (market)",
    )
    post_meaning = _factor_meaning_text(
        post_loadings,
        "headline trend-adjusted surprise",
        "core trend-adjusted surprise",
        "Post-event",
    )

    return {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event_count": int(len(df)),
            "start_date": start_date,
            "symbol": symbol,
            "trend_window": trend_window,
        },
        "models": {
            "pre": pre_pca,
            "pre_mkt": pre_mkt_pca,
            "post": post_pca,
            "pre_feature_source": pre_feature_source,
            "trend_latest": {
                "mom_a": mom_a,
                "mom_b": mom_b,
                "core_a": core_a,
                "core_b": core_b,
            },
            "impacts": {
                "pre": pre_impacts,
                "pre_mkt": pre_mkt_impacts,
                "post": post_impacts,
            },
            "interpretation": {
                "pre": pre_meaning,
                "pre_mkt": pre_mkt_meaning,
                "post": post_meaning,
            },
        },
        "default_buckets": {
            "pre": pre_buckets,
            "pre_mkt": pre_mkt_buckets,
            "post": post_buckets,
        },
        "historical_rows": hist_rows,
        "defaults": _default_current_inputs(df),
        "warnings": warnings,
    }


def render_html(data: Dict[str, object]) -> str:
    payload = json.dumps(data)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>CPI Two-Factor PCA Bucketing</title>
  <style>
    :root {{
      --bg: #0b111b;
      --panel: #111827;
      --panel2: #1f2937;
      --text: #f9fafb;
      --muted: #9ca3af;
      --line: #374151;
      --accent: #60a5fa;
      --good: #34d399;
      --warn: #fb923c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 20px;
    }}
    .wrap {{ max-width: 1600px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px 0; }}
    .sub {{ color: var(--muted); margin-bottom: 16px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 12px;
      margin-bottom: 12px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 12px;
    }}
    .panel h3 {{ margin: 0 0 10px 0; font-size: 1rem; }}
    .row {{ display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }}
    label {{ min-width: 160px; color: var(--muted); font-size: 0.9rem; }}
    input {{
      width: 120px;
      background: var(--panel2);
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px 8px;
    }}
    select {{
      width: 200px;
      background: var(--panel2);
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px 8px;
    }}
    button {{
      background: var(--accent);
      color: #061225;
      border: none;
      border-radius: 8px;
      font-weight: 600;
      padding: 8px 12px;
      cursor: pointer;
      margin-top: 6px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 6px 4px;
      text-align: left;
      vertical-align: top;
    }}
    tr.clickable-row {{ cursor: pointer; }}
    tr.clickable-row:hover {{ background: rgba(96,165,250,0.12); }}
    tr.selected-row {{ background: rgba(52,211,153,0.18); }}
    th {{ color: var(--muted); font-weight: 600; }}
    .kpi {{ font-size: 1.2rem; font-weight: 700; }}
    .small {{ color: var(--muted); font-size: 0.85rem; }}
    .tag {{ padding: 2px 6px; border-radius: 999px; font-size: 0.78rem; border: 1px solid var(--line); }}
    .ok {{ color: var(--good); }}
    .warn {{ color: var(--warn); }}
    .mono {{ font-family: Consolas, Menlo, monospace; }}
    .two {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 12px;
    }}
    .chart-wrap {{
      width: 100%;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0f172a;
      padding: 8px;
    }}
  </style>
</head>
<body>
<div class="wrap">
  <h1>CPI Two-Factor PCA Bucketing Dashboard</h1>
  <div class="sub" id="metaLine"></div>
  <div class="panel" id="warnPanel" style="display:none; margin-bottom:12px;"></div>

  <div class="grid">
    <div class="panel">
      <h3>Current Event Inputs</h3>
      <div class="row"><label>Headline MoM Actual</label><input id="mom_actual" type="number" step="0.01"></div>
      <div class="row"><label>Headline MoM Consensus</label><input id="mom_consensus" type="number" step="0.01"></div>
      <div class="row"><label>Headline MoM Std Dev</label><input id="mom_cons_std" type="number" step="0.001"></div>
      <div class="row"><label>Core MoM Actual</label><input id="core_mom_actual" type="number" step="0.01"></div>
      <div class="row"><label>Core MoM Consensus</label><input id="core_mom_consensus" type="number" step="0.01"></div>
      <div class="row"><label>Core MoM Std Dev</label><input id="core_mom_cons_std" type="number" step="0.001"></div>
      <div class="row"><label>Headline YoY Level</label><input id="mom_yoy_level" type="number" step="0.1"></div>
      <div class="row"><label>Core YoY Level</label><input id="core_yoy_level" type="number" step="0.1"></div>
      <div class="row"><label>Trailing Range (bps)</label><input id="trail_range_bps" type="number" step="0.1"></div>
      <div class="row"><label>Trailing Vol (bps)</label><input id="trail_vol_bps" type="number" step="0.1"></div>
      <div class="row"><label>Bucket Count</label><input id="bucket_count" type="number" step="1" min="2" max="20" value="5"></div>
      <div class="row"><label>Regression Score Model</label>
        <select id="score_model">
          <option value="linear" selected>linear (signed)</option>
          <option value="abs">abs(|F|) (extent-friendly)</option>
        </select>
      </div>
      <button id="runBtn">Recompute</button>
      <div class="small">Post-event trend proxy: rolling regression expected MoM from YoY (latest coefficients).</div>
    </div>

    <div class="panel">
      <h3>Pre-Event Factor Outputs</h3>
      <div class="kpi mono" id="pre_factor_vals"></div>
      <div class="small mono" id="pre_score"></div>
      <div id="pre_bucket_summary"></div>
      <div class="small mono" id="pre_impact_summary"></div>
    </div>

    <div class="panel">
      <h3>Pre-Event (Market) Factor Outputs</h3>
      <div class="kpi mono" id="pre_mkt_factor_vals"></div>
      <div class="small mono" id="pre_mkt_score"></div>
      <div id="pre_mkt_bucket_summary"></div>
      <div class="small mono" id="pre_mkt_impact_summary"></div>
    </div>

    <div class="panel">
      <h3>Post-Event Factor Outputs</h3>
      <div class="kpi mono" id="post_factor_vals"></div>
      <div class="small mono" id="post_score"></div>
      <div id="post_bucket_summary"></div>
      <div class="small mono" id="post_impact_summary"></div>
    </div>
  </div>

  <div class="two">
    <div class="panel">
      <h3>Pre-Event PCA + Buckets</h3>
      <div id="pre_model_info" class="small mono"></div>
      <table id="pre_bucket_table"></table>
      <svg id="pre_boxplot" width="100%" height="260"></svg>
    </div>
    <div class="panel">
      <h3>Pre-Event (Market) PCA + Buckets</h3>
      <div id="pre_mkt_model_info" class="small mono"></div>
      <table id="pre_mkt_bucket_table"></table>
      <svg id="pre_mkt_boxplot" width="100%" height="260"></svg>
    </div>
    <div class="panel">
      <h3>Post-Event PCA + Buckets</h3>
      <div id="post_model_info" class="small mono"></div>
      <table id="post_bucket_table"></table>
      <svg id="post_boxplot" width="100%" height="260"></svg>
    </div>
  </div>

  <div class="panel" style="margin-top:12px;">
    <h3>Bucket Drilldown (Click Any Bucket Row)</h3>
    <div class="small" id="drilldown_meta">No bucket selected.</div>
    <div class="two" style="margin-top:8px;">
      <div>
        <div class="small" style="margin-bottom:6px;">Normalized Price Paths (shared aligned timestamp axis)</div>
        <div class="chart-wrap"><svg id="bucket_paths_chart" width="100%" height="340"></svg></div>
      </div>
      <div>
        <div class="small" style="margin-bottom:6px;">Bucket Distribution (bps, histogram + KDE)</div>
        <div class="chart-wrap"><svg id="bucket_distro_chart" width="100%" height="340"></svg></div>
      </div>
    </div>
    <div style="margin-top:10px;">
      <div class="small" style="margin-bottom:6px;">Selected Event Candlestick (3:00pm T-1 -> 5:00pm event day)</div>
      <div class="chart-wrap"><svg id="bucket_candle_chart" width="100%" height="340"></svg></div>
    </div>
    <div class="small mono" id="bucket_stats" style="margin-top:10px;"></div>
    <div class="small" id="bucket_hourly_meta" style="margin-top:10px;"></div>
    <table id="post_hourly_table" style="margin-top:8px;"></table>
  </div>

  <div class="panel" style="margin-top:12px;">
    <h3>Economic Interpretation And Regression Results</h3>
    <div class="two">
      <div>
        <div class="small"><strong>Pre-event interpretation</strong></div>
        <div id="pre_interp_1" class="small"></div>
        <div id="pre_interp_2" class="small" style="margin-top:4px;"></div>
        <div id="pre_reg_summary" class="small mono" style="margin-top:8px;"></div>
        <div class="small" style="margin-top:10px;"><strong>Pre-event (market) interpretation</strong></div>
        <div id="pre_mkt_interp_1" class="small"></div>
        <div id="pre_mkt_interp_2" class="small" style="margin-top:4px;"></div>
        <div id="pre_mkt_reg_summary" class="small mono" style="margin-top:8px;"></div>
      </div>
      <div>
        <div class="small"><strong>Post-event interpretation</strong></div>
        <div id="post_interp_1" class="small"></div>
        <div id="post_interp_2" class="small" style="margin-top:4px;"></div>
        <div id="post_reg_summary" class="small mono" style="margin-top:8px;"></div>
      </div>
    </div>
  </div>

</div>

<script>
const DATA = {payload};
const EPS = 1e-9;
let SELECTED_BUCKET = null;

function q(id) {{ return document.getElementById(id); }}
function showError(msg) {{
  const wp = q("warnPanel");
  if (!wp) return;
  wp.style.display = "block";
  const safe = String(msg || "Unknown error");
  wp.innerHTML = `<h3 style="margin:0 0 8px 0;">Runtime Error</h3><div class="small warn mono">${{safe}}</div>`;
}}
function fmt(x, d=3) {{
  if (x === null || x === undefined || Number.isNaN(x)) return "n/a";
  return Number(x).toFixed(d);
}}
function mean(arr) {{
  if (!arr.length) return null;
  const s = arr.reduce((a,b)=>a+b,0);
  return s / arr.length;
}}
function quantile(sortedVals, p) {{
  if (!sortedVals.length) return NaN;
  const pos = (sortedVals.length - 1) * p;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  if (lo === hi) return sortedVals[lo];
  const w = pos - lo;
  return sortedVals[lo] * (1 - w) + sortedVals[hi] * w;
}}
function edgesFromScores(scores, buckets) {{
  const sorted = [...scores].sort((a,b)=>a-b);
  const probs = Array.from({{length: buckets+1}}, (_,i)=>i/buckets);
  const edges = probs.map(p => quantile(sorted, p));
  for (let i=1;i<edges.length;i++) {{
    if (!(edges[i] > edges[i-1])) edges[i] = edges[i-1] + 1e-6;
  }}
  return edges;
}}
function assignBucket(x, edges) {{
  if (x <= edges[0]) return 0;
  for (let i=0;i<edges.length-1;i++) {{
    const lo = edges[i], hi = edges[i+1];
    if ((x >= lo) && ((x < hi) || (i===edges.length-2 && x <= hi))) return i;
  }}
  return edges.length - 2;
}}
function toHourlyMap(arr) {{
  const out = {{}};
  (arr || []).forEach((r) => {{
    const h = String(r.clock_label || "").trim();
    const v = Number(r.be_bps);
    if (h && Number.isFinite(v)) out[h] = v;
  }});
  return out;
}}
function modelTransform(vec, model) {{
  const mu = model.mu;
  const sigma = model.sigma;
  const L = model.loadings; // 2x2 columns are factors
  const z0 = (vec[0] - mu[0]) / Math.max(sigma[0], EPS);
  const z1 = (vec[1] - mu[1]) / Math.max(sigma[1], EPS);
  const f1 = z0 * L[0][0] + z1 * L[1][0];
  const f2 = z0 * L[0][1] + z1 * L[1][1];
  const w1 = model.weights[0], w2 = model.weights[1];
  const score = f1 * w1 + f2 * w2;
  return {{f1, f2, score}};
}}
function bucketStats(rows, scoreKey, bpsKey, edges) {{
  const out = [];
  for (let i=0;i<edges.length-1;i++) {{
    const lo = edges[i], hi = edges[i+1];
    const vals = rows
      .filter(r => {{
        const s = r[scoreKey];
        return (s >= lo) && (i===edges.length-2 ? s <= hi : s < hi);
      }})
      .map(r => r[bpsKey])
      .filter(v => Number.isFinite(v));
    out.push({{
      bucket: i,
      low: lo,
      high: hi,
      count: vals.length,
      mean_bps: mean(vals),
    }});
  }}
  return out;
}}
function percentile(sortedVals, p) {{
  if (!sortedVals.length) return NaN;
  const pos = (sortedVals.length - 1) * p;
  const lo = Math.floor(pos), hi = Math.ceil(pos);
  if (lo === hi) return sortedVals[lo];
  const w = pos - lo;
  return sortedVals[lo] * (1 - w) + sortedVals[hi] * w;
}}
function bucketBoxStats(rows, scoreKey, bpsKey, edges) {{
  const out = [];
  for (let i=0;i<edges.length-1;i++) {{
    const lo = edges[i], hi = edges[i+1];
    const vals = rows
      .filter(r => {{
        const s = r[scoreKey];
        return (s >= lo) && (i===edges.length-2 ? s <= hi : s < hi);
      }})
      .map(r => r[bpsKey])
      .filter(v => Number.isFinite(v))
      .sort((a,b)=>a-b);
    if (!vals.length) {{
      out.push({{bucket:i, count:0}});
      continue;
    }}
    out.push({{
      bucket: i,
      count: vals.length,
      min: vals[0],
      q1: percentile(vals, 0.25),
      median: percentile(vals, 0.50),
      q3: percentile(vals, 0.75),
      max: vals[vals.length - 1],
    }});
  }}
  return out;
}}
function renderBoxplot(svgId, boxRows, highlightIdx, onBucketClick) {{
  const svg = q(svgId);
  const w = Math.max(560, svg.clientWidth || 560);
  const h = 260;
  const padL = 44, padR = 16, padT = 20, padB = 36;
  svg.setAttribute("viewBox", `0 0 ${{w}} ${{h}}`);
  svg.innerHTML = "";

  const valid = boxRows.filter(r => r.count && Number.isFinite(r.min));
  if (!valid.length) {{
    svg.innerHTML = `<text x="20" y="36" fill="#9ca3af" font-size="12">No data for boxplot</text>`;
    return;
  }}
  let yMin = Math.min(...valid.map(r => r.min));
  let yMax = Math.max(...valid.map(r => r.max));
  if (!(yMax > yMin)) {{ yMin -= 1; yMax += 1; }}
  const yPad = (yMax - yMin) * 0.08;
  yMin -= yPad; yMax += yPad;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const slot = plotW / Math.max(boxRows.length, 1);
  const yPix = (v) => padT + (1 - (v - yMin) / (yMax - yMin)) * plotH;

  const axis = document.createElementNS("http://www.w3.org/2000/svg", "line");
  axis.setAttribute("x1", String(padL)); axis.setAttribute("x2", String(w - padR));
  axis.setAttribute("y1", String(h - padB)); axis.setAttribute("y2", String(h - padB));
  axis.setAttribute("stroke", "#374151"); axis.setAttribute("stroke-width", "1");
  svg.appendChild(axis);

  for (let t=0; t<=4; t++) {{
    const v = yMin + (t/4)*(yMax-yMin);
    const y = yPix(v);
    const gl = document.createElementNS("http://www.w3.org/2000/svg", "line");
    gl.setAttribute("x1", String(padL)); gl.setAttribute("x2", String(w - padR));
    gl.setAttribute("y1", String(y)); gl.setAttribute("y2", String(y));
    gl.setAttribute("stroke", "#1f2937"); gl.setAttribute("stroke-width", "1");
    svg.appendChild(gl);
    const tx = document.createElementNS("http://www.w3.org/2000/svg", "text");
    tx.setAttribute("x", "4"); tx.setAttribute("y", String(y + 4));
    tx.setAttribute("fill", "#9ca3af"); tx.setAttribute("font-size", "11");
    tx.textContent = v.toFixed(1);
    svg.appendChild(tx);
  }}

  boxRows.forEach((r, i) => {{
    const cx = padL + i * slot + slot * 0.5;
    const bw = Math.max(16, slot * 0.46);
    const isHi = i === highlightIdx;
    const stroke = isHi ? "#34d399" : "#60a5fa";
    const fill = isHi ? "rgba(52,211,153,0.24)" : "rgba(96,165,250,0.18)";

    const lb = document.createElementNS("http://www.w3.org/2000/svg", "text");
    lb.setAttribute("x", String(cx - 4)); lb.setAttribute("y", String(h - 14));
    lb.setAttribute("fill", isHi ? "#34d399" : "#9ca3af");
    lb.setAttribute("font-size", "11");
    lb.textContent = String(i + 1);
    lb.style.cursor = "pointer";
    lb.addEventListener("click", () => onBucketClick && onBucketClick(i));
    svg.appendChild(lb);
    if (!r.count || !Number.isFinite(r.min)) return;

    const yMinP = yPix(r.min), yQ1 = yPix(r.q1), yMed = yPix(r.median), yQ3 = yPix(r.q3), yMaxP = yPix(r.max);
    const wLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
    wLine.setAttribute("x1", String(cx)); wLine.setAttribute("x2", String(cx));
    wLine.setAttribute("y1", String(yMaxP)); wLine.setAttribute("y2", String(yMinP));
    wLine.setAttribute("stroke", stroke); wLine.setAttribute("stroke-width", "1.5");
    svg.appendChild(wLine);

    const box = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    box.setAttribute("x", String(cx - bw/2)); box.setAttribute("y", String(Math.min(yQ1, yQ3)));
    box.setAttribute("width", String(bw)); box.setAttribute("height", String(Math.max(1, Math.abs(yQ3 - yQ1))));
    box.setAttribute("fill", fill); box.setAttribute("stroke", stroke); box.setAttribute("stroke-width", "1.5");
    box.style.cursor = "pointer";
    box.addEventListener("click", () => onBucketClick && onBucketClick(i));
    svg.appendChild(box);

    const med = document.createElementNS("http://www.w3.org/2000/svg", "line");
    med.setAttribute("x1", String(cx - bw/2)); med.setAttribute("x2", String(cx + bw/2));
    med.setAttribute("y1", String(yMed)); med.setAttribute("y2", String(yMed));
    med.setAttribute("stroke", stroke); med.setAttribute("stroke-width", "2");
    svg.appendChild(med);
  }});
}}
function renderBucketTable(tableId, rows, selectedIdx, onBucketClick) {{
  const el = q(tableId);
  const head = `
    <tr>
      <th>Bucket</th><th>Bounds</th><th>N</th><th>Mean bps</th>
    </tr>`;
  const body = rows.map(r => `
    <tr class="clickable-row ${{r.bucket === selectedIdx ? 'selected-row' : ''}}" data-bucket-idx="${{r.bucket}}">
      <td>${{r.bucket + 1}}</td>
      <td class="mono">[${{fmt(r.low,3)}}, ${{fmt(r.high,3)}}]</td>
      <td>${{r.count}}</td>
      <td class="mono">${{fmt(r.mean_bps,2)}}</td>
    </tr>`).join("");
  el.innerHTML = head + body;
  if (onBucketClick) {{
    el.querySelectorAll("tr.clickable-row").forEach((tr) => {{
      tr.addEventListener("click", () => {{
        const idx = parseInt(tr.getAttribute("data-bucket-idx") || "-1", 10);
        if (Number.isInteger(idx) && idx >= 0) onBucketClick(idx);
      }});
    }});
  }}
}}
function stdDev(vals) {{
  if (!vals || vals.length < 2) return 0;
  const m = mean(vals);
  const v = mean(vals.map(vv => (vv - m) * (vv - m)));
  return Math.sqrt(v);
}}
function gaussianKDE(values) {{
  const n = values.length;
  if (n < 2) return () => 0;
  const sd = Math.max(stdDev(values), 1e-6);
  const h = Math.max(1.06 * sd * Math.pow(n, -0.2), 1e-3);
  const inv = 1 / (n * h * Math.sqrt(2 * Math.PI));
  return (x) => {{
    let s = 0;
    for (let i = 0; i < n; i++) {{
      const z = (x - values[i]) / h;
      s += Math.exp(-0.5 * z * z);
    }}
    return inv * s;
  }};
}}
function bootstrapMeanCI(values, nBoot=2000, alpha=0.05) {{
  if (!values || values.length < 2) return null;
  const n = values.length;
  const samples = [];
  for (let i=0; i<nBoot; i++) {{
    let s = 0;
    for (let j=0; j<n; j++) {{
      s += values[Math.floor(Math.random() * n)];
    }}
    samples.push(s / n);
  }}
  samples.sort((a,b)=>a-b);
  return {{
    lo: percentile(samples, alpha / 2),
    hi: percentile(samples, 1 - alpha / 2),
  }};
}}
function calcBucketMembers(rows, scoreKey, edges, idx) {{
  const lo = edges[idx], hi = edges[idx + 1];
  return rows.filter((r) => {{
    const s = r[scoreKey];
    return (s >= lo) && (idx === edges.length - 2 ? s <= hi : s < hi);
  }});
}}
function renderBucketPaths(svgId, members, pathKey, title, onPathClick) {{
  const svg = q(svgId);
  const w = Math.max(760, svg.clientWidth || 760);
  const h = 340;
  const padL = 56, padR = 16, padT = 24, padB = 44;
  svg.setAttribute("viewBox", `0 0 ${{w}} ${{h}}`);
  svg.innerHTML = "";
  if (!members.length) {{
    svg.innerHTML = `<text x="20" y="36" fill="#9ca3af" font-size="12">No events in selected bucket.</text>`;
    return;
  }}
  const series = members.map(m => (m[pathKey] || []).filter(p => Number.isFinite(p.t_min) && Number.isFinite(p.bps)));
  const flat = series.flat();
  if (!flat.length) {{
    svg.innerHTML = `<text x="20" y="36" fill="#9ca3af" font-size="12">No path points available.</text>`;
    return;
  }}
  const xMin = Math.min(...flat.map(p => p.t_min));
  const xMax = Math.max(...flat.map(p => p.t_min));
  let yMin = Math.min(...flat.map(p => p.bps));
  let yMax = Math.max(...flat.map(p => p.bps));
  if (!(yMax > yMin)) {{ yMin -= 1; yMax += 1; }}
  const yPad = (yMax - yMin) * 0.12;
  yMin -= yPad; yMax += yPad;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const xPix = (x) => padL + ((x - xMin) / Math.max(xMax - xMin, 1e-9)) * plotW;
  const yPix = (y) => padT + (1 - (y - yMin) / Math.max(yMax - yMin, 1e-9)) * plotH;

  const xAxis = document.createElementNS("http://www.w3.org/2000/svg", "line");
  xAxis.setAttribute("x1", String(padL)); xAxis.setAttribute("x2", String(w - padR));
  xAxis.setAttribute("y1", String(h - padB)); xAxis.setAttribute("y2", String(h - padB));
  xAxis.setAttribute("stroke", "#4b5563"); xAxis.setAttribute("stroke-width", "1.2");
  svg.appendChild(xAxis);
  const yAxis = document.createElementNS("http://www.w3.org/2000/svg", "line");
  yAxis.setAttribute("x1", String(padL)); yAxis.setAttribute("x2", String(padL));
  yAxis.setAttribute("y1", String(padT)); yAxis.setAttribute("y2", String(h - padB));
  yAxis.setAttribute("stroke", "#4b5563"); yAxis.setAttribute("stroke-width", "1.2");
  svg.appendChild(yAxis);

  for (let t=0; t<=4; t++) {{
    const v = yMin + (t/4)*(yMax-yMin);
    const y = yPix(v);
    const gl = document.createElementNS("http://www.w3.org/2000/svg", "line");
    gl.setAttribute("x1", String(padL)); gl.setAttribute("x2", String(w - padR));
    gl.setAttribute("y1", String(y)); gl.setAttribute("y2", String(y));
    gl.setAttribute("stroke", "#1f2937"); gl.setAttribute("stroke-width", "1");
    svg.appendChild(gl);
    const tx = document.createElementNS("http://www.w3.org/2000/svg", "text");
    tx.setAttribute("x", "4"); tx.setAttribute("y", String(y + 4));
    tx.setAttribute("fill", "#9ca3af"); tx.setAttribute("font-size", "11");
    tx.textContent = `${{v.toFixed(1)}} bps`;
    svg.appendChild(tx);
  }}
  for (let t=0; t<=6; t++) {{
    const xv = xMin + (t/6)*(xMax - xMin);
    const x = xPix(xv);
    const tick = document.createElementNS("http://www.w3.org/2000/svg", "line");
    tick.setAttribute("x1", String(x)); tick.setAttribute("x2", String(x));
    tick.setAttribute("y1", String(h - padB)); tick.setAttribute("y2", String(h - padB + 4));
    tick.setAttribute("stroke", "#6b7280"); tick.setAttribute("stroke-width", "1");
    svg.appendChild(tick);
    const tx = document.createElementNS("http://www.w3.org/2000/svg", "text");
    tx.setAttribute("x", String(x - 14)); tx.setAttribute("y", String(h - 16));
    tx.setAttribute("fill", "#9ca3af"); tx.setAttribute("font-size", "10");
    tx.textContent = `${{fmt(xv,0)}}m`;
    svg.appendChild(tx);
  }}
  const xLab = document.createElementNS("http://www.w3.org/2000/svg", "text");
  xLab.setAttribute("x", String(padL + plotW/2 - 36));
  xLab.setAttribute("y", String(h - 2));
  xLab.setAttribute("fill", "#9ca3af");
  xLab.setAttribute("font-size", "11");
  xLab.textContent = "Minutes from anchor";
  svg.appendChild(xLab);

  let selectedPath = -1;
  const allPaths = [];
  const tooltip = document.createElementNS("http://www.w3.org/2000/svg", "text");
  tooltip.setAttribute("x", String(padL + 8));
  tooltip.setAttribute("y", String(padT + 14));
  tooltip.setAttribute("fill", "#e5e7eb");
  tooltip.setAttribute("font-size", "11");
  tooltip.textContent = "";
  svg.appendChild(tooltip);

  const refreshSelection = () => {{
    allPaths.forEach((p, i) => {{
      const active = selectedPath < 0 || i === selectedPath;
      p.setAttribute("opacity", active ? "0.95" : "0.22");
      p.setAttribute("stroke-width", active ? "2.0" : "1.2");
    }});
  }};

  members.forEach((m, idx) => {{
    const pts = (m[pathKey] || []).filter(p => Number.isFinite(p.t_min) && Number.isFinite(p.bps)).sort((a,b)=>a.t_min-b.t_min);
    if (pts.length < 2) return;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const d = pts.map((p, i) => `${{i===0 ? "M":"L"}} ${{xPix(p.t_min)}} ${{yPix(p.bps)}}`).join(" ");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", `hsl(${{(idx*47)%360}},70%,62%)`);
    path.setAttribute("stroke-width", "1.7");
    path.style.cursor = "pointer";
    path.addEventListener("click", () => {{
      selectedPath = (selectedPath === idx) ? -1 : idx;
      refreshSelection();
      if (typeof onPathClick === "function") onPathClick(selectedPath >= 0 ? m : null);
    }});
    path.addEventListener("mousemove", (ev) => {{
      const rect = svg.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      let best = null;
      let bestDx = Infinity;
      for (const p of pts) {{
        const dx = Math.abs(xPix(p.t_min) - mx);
        if (dx < bestDx) {{
          bestDx = dx;
          best = p;
        }}
      }}
      if (best) {{
        tooltip.textContent = `${{m.date || "event"}} | t=${{fmt(best.t_min,0)}}m | bps=${{fmt(best.bps,2)}}`;
      }}
    }});
    path.addEventListener("mouseleave", () => {{
      tooltip.textContent = selectedPath >= 0 ? tooltip.textContent : "";
    }});
    svg.appendChild(path);
    allPaths.push(path);
  }});
  refreshSelection();

  const titleEl = document.createElementNS("http://www.w3.org/2000/svg", "text");
  titleEl.setAttribute("x", String(padL));
  titleEl.setAttribute("y", "14");
  titleEl.setAttribute("fill", "#d1d5db");
  titleEl.setAttribute("font-size", "12");
  titleEl.textContent = `${{title}} (click a path to isolate)`;
  svg.appendChild(titleEl);
}}
function renderBucketDistro(svgId, values) {{
  const svg = q(svgId);
  const w = Math.max(760, svg.clientWidth || 760);
  const h = 340;
  const padL = 56, padR = 16, padT = 20, padB = 42;
  svg.setAttribute("viewBox", `0 0 ${{w}} ${{h}}`);
  svg.innerHTML = "";
  if (!values.length) {{
    svg.innerHTML = `<text x="20" y="36" fill="#9ca3af" font-size="12">No values in selected bucket.</text>`;
    return;
  }}
  const sorted = [...values].sort((a,b)=>a-b);
  const vMin = sorted[0], vMax = sorted[sorted.length - 1];
  const span = Math.max(vMax - vMin, 1e-6);
  const bins = Math.max(6, Math.min(20, Math.round(Math.sqrt(values.length)) + 3));
  const bw = span / bins;
  const hist = Array.from({{length: bins}}, (_, i) => {{
    const lo = vMin + i*bw;
    const hi = i===bins-1 ? vMax + 1e-9 : lo + bw;
    const c = values.filter(v => v >= lo && v < hi).length;
    return {{lo, hi, density: c / (values.length * bw), count: c}};
  }});
  const kde = gaussianKDE(values);
  const xs = Array.from({{length: 180}}, (_, i) => vMin + (i / 179) * span);
  const ys = xs.map(x => kde(x));
  const yMax = Math.max(1e-9, ...hist.map(hh => hh.density), ...ys);
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const xPix = (x) => padL + ((x - vMin) / Math.max(span, 1e-9)) * plotW;
  const yPix = (y) => padT + (1 - y / yMax) * plotH;

  const xAxis = document.createElementNS("http://www.w3.org/2000/svg", "line");
  xAxis.setAttribute("x1", String(padL)); xAxis.setAttribute("x2", String(w - padR));
  xAxis.setAttribute("y1", String(h - padB)); xAxis.setAttribute("y2", String(h - padB));
  xAxis.setAttribute("stroke", "#4b5563"); xAxis.setAttribute("stroke-width", "1.2");
  svg.appendChild(xAxis);
  const yAxis = document.createElementNS("http://www.w3.org/2000/svg", "line");
  yAxis.setAttribute("x1", String(padL)); yAxis.setAttribute("x2", String(padL));
  yAxis.setAttribute("y1", String(padT)); yAxis.setAttribute("y2", String(h - padB));
  yAxis.setAttribute("stroke", "#4b5563"); yAxis.setAttribute("stroke-width", "1.2");
  svg.appendChild(yAxis);

  for (let t=0; t<=4; t++) {{
    const yd = (t/4) * yMax;
    const y = yPix(yd);
    const gl = document.createElementNS("http://www.w3.org/2000/svg", "line");
    gl.setAttribute("x1", String(padL)); gl.setAttribute("x2", String(w - padR));
    gl.setAttribute("y1", String(y)); gl.setAttribute("y2", String(y));
    gl.setAttribute("stroke", "#1f2937"); gl.setAttribute("stroke-width", "1");
    svg.appendChild(gl);
    const tx = document.createElementNS("http://www.w3.org/2000/svg", "text");
    tx.setAttribute("x", "4"); tx.setAttribute("y", String(y + 4));
    tx.setAttribute("fill", "#9ca3af"); tx.setAttribute("font-size", "10");
    tx.textContent = fmt(yd, 3);
    svg.appendChild(tx);
  }}
  for (let t=0; t<=6; t++) {{
    const xv = vMin + (t/6) * span;
    const x = xPix(xv);
    const tx = document.createElementNS("http://www.w3.org/2000/svg", "text");
    tx.setAttribute("x", String(x - 16)); tx.setAttribute("y", String(h - 16));
    tx.setAttribute("fill", "#9ca3af"); tx.setAttribute("font-size", "10");
    tx.textContent = fmt(xv, 1);
    svg.appendChild(tx);
  }}

  hist.forEach((b) => {{
    const x0 = xPix(b.lo), x1 = xPix(b.hi), y = yPix(b.density);
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(x0 + 1));
    rect.setAttribute("y", String(y));
    rect.setAttribute("width", String(Math.max(1, x1 - x0 - 2)));
    rect.setAttribute("height", String((h - padB) - y));
    rect.setAttribute("fill", "rgba(96,165,250,0.35)");
    rect.setAttribute("stroke", "#60a5fa");
    rect.setAttribute("stroke-width", "1");
    svg.appendChild(rect);
  }});
  const kdePath = document.createElementNS("http://www.w3.org/2000/svg", "path");
  kdePath.setAttribute("d", ys.map((y, i) => `${{i===0 ? "M":"L"}} ${{xPix(xs[i])}} ${{yPix(y)}}`).join(" "));
  kdePath.setAttribute("fill", "none");
  kdePath.setAttribute("stroke", "#f59e0b");
  kdePath.setAttribute("stroke-width", "2");
  svg.appendChild(kdePath);

  const sN = values.length;
  const sMean = mean(values);
  const sStd = stdDev(values);
  const sP5 = percentile(sorted, 0.05);
  const sP50 = percentile(sorted, 0.50);
  const sP95 = percentile(sorted, 0.95);
  const statsBox = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  const sbW = 210, sbH = 86, sbX = w - padR - sbW - 4, sbY = padT + 6;
  statsBox.setAttribute("x", String(sbX));
  statsBox.setAttribute("y", String(sbY));
  statsBox.setAttribute("width", String(sbW));
  statsBox.setAttribute("height", String(sbH));
  statsBox.setAttribute("fill", "rgba(15,23,42,0.85)");
  statsBox.setAttribute("stroke", "#374151");
  statsBox.setAttribute("rx", "6");
  svg.appendChild(statsBox);
  const statsText = [
    `N=${{sN}}`,
    `mean=${{fmt(sMean,2)}}  std=${{fmt(sStd,2)}}`,
    `P5=${{fmt(sP5,2)}}  P50=${{fmt(sP50,2)}}  P95=${{fmt(sP95,2)}}`,
  ];
  statsText.forEach((line, i) => {{
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", String(sbX + 10));
    t.setAttribute("y", String(sbY + 20 + i*22));
    t.setAttribute("fill", "#e5e7eb");
    t.setAttribute("font-size", "12");
    t.textContent = line;
    svg.appendChild(t);
  }});
}}
function renderCandles(svgId, candles, title) {{
  const svg = q(svgId);
  const w = Math.max(760, svg.clientWidth || 760);
  const h = 340;
  const padL = 56, padR = 16, padT = 22, padB = 44;
  svg.setAttribute("viewBox", `0 0 ${{w}} ${{h}}`);
  svg.innerHTML = "";
  if (!candles || !candles.length) {{
    svg.innerHTML = `<text x="20" y="36" fill="#9ca3af" font-size="12">Click a path to view event candlesticks.</text>`;
    return;
  }}
  const rows = candles
    .filter(c => Number.isFinite(c.t_min) && Number.isFinite(c.open) && Number.isFinite(c.high) && Number.isFinite(c.low) && Number.isFinite(c.close))
    .sort((a,b)=>a.t_min-b.t_min);
  if (!rows.length) {{
    svg.innerHTML = `<text x="20" y="36" fill="#9ca3af" font-size="12">No candlestick data available.</text>`;
    return;
  }}
  const xMin = rows[0].t_min;
  const xMax = rows[rows.length - 1].t_min;
  let yMin = Math.min(...rows.map(r => r.low));
  let yMax = Math.max(...rows.map(r => r.high));
  if (!(yMax > yMin)) {{ yMin -= 1; yMax += 1; }}
  const yPad = (yMax - yMin) * 0.08;
  yMin -= yPad; yMax += yPad;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const xPix = (x) => padL + ((x - xMin) / Math.max(xMax - xMin, 1e-9)) * plotW;
  const yPix = (y) => padT + (1 - (y - yMin) / Math.max(yMax - yMin, 1e-9)) * plotH;

  const xAxis = document.createElementNS("http://www.w3.org/2000/svg", "line");
  xAxis.setAttribute("x1", String(padL)); xAxis.setAttribute("x2", String(w - padR));
  xAxis.setAttribute("y1", String(h - padB)); xAxis.setAttribute("y2", String(h - padB));
  xAxis.setAttribute("stroke", "#4b5563"); xAxis.setAttribute("stroke-width", "1.2");
  svg.appendChild(xAxis);
  const yAxis = document.createElementNS("http://www.w3.org/2000/svg", "line");
  yAxis.setAttribute("x1", String(padL)); yAxis.setAttribute("x2", String(padL));
  yAxis.setAttribute("y1", String(padT)); yAxis.setAttribute("y2", String(h - padB));
  yAxis.setAttribute("stroke", "#4b5563"); yAxis.setAttribute("stroke-width", "1.2");
  svg.appendChild(yAxis);

  for (let t=0; t<=4; t++) {{
    const yv = yMin + (t/4)*(yMax - yMin);
    const y = yPix(yv);
    const gl = document.createElementNS("http://www.w3.org/2000/svg", "line");
    gl.setAttribute("x1", String(padL)); gl.setAttribute("x2", String(w - padR));
    gl.setAttribute("y1", String(y)); gl.setAttribute("y2", String(y));
    gl.setAttribute("stroke", "#1f2937"); gl.setAttribute("stroke-width", "1");
    svg.appendChild(gl);
    const tx = document.createElementNS("http://www.w3.org/2000/svg", "text");
    tx.setAttribute("x", "4"); tx.setAttribute("y", String(y + 4));
    tx.setAttribute("fill", "#9ca3af"); tx.setAttribute("font-size", "10");
    tx.textContent = fmt(yv, 3);
    svg.appendChild(tx);
  }}
  for (let t=0; t<=6; t++) {{
    const xv = xMin + (t/6)*(xMax - xMin);
    const x = xPix(xv);
    const tx = document.createElementNS("http://www.w3.org/2000/svg", "text");
    tx.setAttribute("x", String(x - 16)); tx.setAttribute("y", String(h - 16));
    tx.setAttribute("fill", "#9ca3af"); tx.setAttribute("font-size", "10");
    tx.textContent = `${{fmt(xv,0)}}m`;
    svg.appendChild(tx);
  }}
  const slot = plotW / Math.max(rows.length, 1);
  const bodyW = Math.max(1.5, Math.min(8, slot * 0.55));
  rows.forEach((r) => {{
    const x = xPix(r.t_min);
    const wick = document.createElementNS("http://www.w3.org/2000/svg", "line");
    wick.setAttribute("x1", String(x)); wick.setAttribute("x2", String(x));
    wick.setAttribute("y1", String(yPix(r.high))); wick.setAttribute("y2", String(yPix(r.low)));
    wick.setAttribute("stroke", "#9ca3af"); wick.setAttribute("stroke-width", "1");
    svg.appendChild(wick);

    const up = r.close >= r.open;
    const top = up ? r.close : r.open;
    const bot = up ? r.open : r.close;
    const body = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    body.setAttribute("x", String(x - bodyW/2));
    body.setAttribute("y", String(yPix(top)));
    body.setAttribute("width", String(bodyW));
    body.setAttribute("height", String(Math.max(1, yPix(bot) - yPix(top))));
    body.setAttribute("fill", up ? "rgba(52,211,153,0.7)" : "rgba(248,113,113,0.75)");
    body.setAttribute("stroke", up ? "#34d399" : "#f87171");
    body.setAttribute("stroke-width", "1");
    svg.appendChild(body);
  }});
  const titleEl = document.createElementNS("http://www.w3.org/2000/svg", "text");
  titleEl.setAttribute("x", String(padL));
  titleEl.setAttribute("y", "14");
  titleEl.setAttribute("fill", "#d1d5db");
  titleEl.setAttribute("font-size", "12");
  titleEl.textContent = title || "Candlestick";
  svg.appendChild(titleEl);
}}
function setDefaults() {{
  const d = DATA.defaults || {{}};
  for (const k of Object.keys(d)) {{
    const el = q(k);
    if (el) el.value = d[k];
  }}
}}
function renderModelInfo() {{
  const pm = DATA.models.pre;
  const mm = DATA.models.pre_mkt;
  const qm = DATA.models.post;
  const src = DATA.models.pre_feature_source || "cons_std";
  q("pre_model_info").textContent =
    `Feature source: ${{src}} | Explained var: [${{fmt(pm.explained_variance_ratio[0],3)}}, ${{fmt(pm.explained_variance_ratio[1],3)}}]` +
    ` | Loadings f1=[${{fmt(pm.loadings[0][0],3)}}, ${{fmt(pm.loadings[1][0],3)}}], f2=[${{fmt(pm.loadings[0][1],3)}}, ${{fmt(pm.loadings[1][1],3)}}]`;
  q("pre_mkt_model_info").textContent =
    `Features: trailing range/vol (bps) | Explained var: [${{fmt(mm.explained_variance_ratio[0],3)}}, ${{fmt(mm.explained_variance_ratio[1],3)}}]` +
    ` | Loadings f1=[${{fmt(mm.loadings[0][0],3)}}, ${{fmt(mm.loadings[1][0],3)}}], f2=[${{fmt(mm.loadings[0][1],3)}}, ${{fmt(mm.loadings[1][1],3)}}]`;
  q("post_model_info").textContent =
    `Explained var: [${{fmt(qm.explained_variance_ratio[0],3)}}, ${{fmt(qm.explained_variance_ratio[1],3)}}]` +
    ` | Loadings f1=[${{fmt(qm.loadings[0][0],3)}}, ${{fmt(qm.loadings[1][0],3)}}], f2=[${{fmt(qm.loadings[0][1],3)}}, ${{fmt(qm.loadings[1][1],3)}}]`;
}}

function regScore(imp, f1, f2) {{
  if (!imp) return NaN;
  if (imp.feature_transform === "abs") {{
    return imp.beta_factor1 * Math.abs(f1) + imp.beta_factor2 * Math.abs(f2) + imp.intercept;
  }}
  return imp.beta_factor1 * f1 + imp.beta_factor2 * f2 + imp.intercept;
}}

function impactsFor(modeKey, scoreModel) {{
  const pack = (DATA.models.impacts || {{}})[modeKey] || {{}};
  if (scoreModel === "abs") return pack.abs || pack.linear || null;
  return pack.linear || pack.abs || null;
}}
function renderPostHourlyTable(mode, members, bucketIdx) {{
  const el = q("post_hourly_table");
  const meta = q("bucket_hourly_meta");
  if (!el) return;
  if (mode !== "post") {{
    if (meta) meta.textContent = "Hourly post-event table appears when a Post bucket is selected.";
    el.innerHTML = `<tr><td class="small">Select a Post bucket row to view hourly breakeven bps.</td></tr>`;
    return;
  }}
  const clocks = ["08:30","09:00","10:00","11:00","12:00","13:00","14:00","15:00"];
  if (meta) meta.textContent = `Post bucket ${{bucketIdx + 1}}: hourly breakeven-style mean bps from 8:30am to 3:00pm (N=${{members.length}}).`;
  const head = `<tr><th>Clock (ET)</th><th>Mean B/E bps</th><th>N</th></tr>`;
  const body = clocks.map((clock) => {{
    const vals = (members || []).map(r => r.post_hourly_map?.[clock]).filter(v => Number.isFinite(v));
    return `<tr>
      <td class="mono">${{clock}}</td>
      <td class="mono">${{fmt(mean(vals),2)}}</td>
      <td>${{vals.length}}</td>
    </tr>`;
  }}).join("");
  el.innerHTML = head + body;
}}
function updateDrilldown(state, rowsReg, maps) {{
  const metaEl = q("drilldown_meta");
  const statsEl = q("bucket_stats");
  if (!state || !maps[state.mode]) {{
    if (metaEl) metaEl.textContent = "No bucket selected.";
    if (statsEl) statsEl.textContent = "";
    renderPostHourlyTable("pre", [], 0);
    renderBucketPaths("bucket_paths_chart", [], "pre_path", "", null);
    renderBucketDistro("bucket_distro_chart", []);
    renderCandles("bucket_candle_chart", [], "");
    return;
  }}
  const cfg = maps[state.mode];
  const members = calcBucketMembers(rowsReg, cfg.scoreKey, cfg.edges, state.bucketIdx);
  const bpsVals = members.map(m => m[cfg.distroKey]).filter(v => Number.isFinite(v));
  const m = mean(bpsVals);
  const med = percentile([...bpsVals].sort((a,b)=>a-b), 0.5);
  const p5 = percentile([...bpsVals].sort((a,b)=>a-b), 0.05);
  const p95 = percentile([...bpsVals].sort((a,b)=>a-b), 0.95);
  const ci = bootstrapMeanCI(bpsVals, 2000, 0.05);
  const warn = bpsVals.length < 5 ? " | warning: small sample (N<5)" : "";
  if (metaEl) {{
    metaEl.textContent = `${{cfg.label}} bucket ${{state.bucketIdx + 1}} | members=${{members.length}} | window=${{cfg.windowLabel}}`;
  }}
  if (statsEl) {{
    const ciText = ci ? `95% CI=[${{fmt(ci.lo,2)}}, ${{fmt(ci.hi,2)}}]` : "95% CI=n/a";
    statsEl.textContent =
      `C->C distro | N=${{bpsVals.length}} | mean=${{fmt(m,2)}} | median=${{fmt(med,2)}} | P5=${{fmt(p5,2)}} | P95=${{fmt(p95,2)}} | ${{ciText}}${{warn}}`;
  }}
  renderBucketPaths("bucket_paths_chart", members, cfg.pathKey, `${{cfg.label}} normalized paths`, (member) => {{
    const c = (member && Array.isArray(member.full_candles)) ? member.full_candles : [];
    const label = member && member.date ? `${{cfg.label}} event ${{member.date}} | Candles (3pm T-1 -> 5pm)` : "Candlestick";
    renderCandles("bucket_candle_chart", c, label);
  }});
  renderBucketDistro("bucket_distro_chart", bpsVals);
  renderCandles("bucket_candle_chart", [], "");
  renderPostHourlyTable(state.mode, members, state.bucketIdx);
}}
function recompute() {{
  const momActual = Number(q("mom_actual").value);
  const momConsensus = Number(q("mom_consensus").value);
  const momStd = Math.max(Number(q("mom_cons_std").value), EPS);
  const coreActual = Number(q("core_mom_actual").value);
  const coreConsensus = Number(q("core_mom_consensus").value);
  const coreStd = Math.max(Number(q("core_mom_cons_std").value), EPS);
  const momYoy = Number(q("mom_yoy_level").value);
  const coreYoy = Number(q("core_yoy_level").value);
  const trailRange = Number(q("trail_range_bps").value);
  const trailVol = Number(q("trail_vol_bps").value);
  const bucketCount = Math.max(2, Math.min(20, parseInt(q("bucket_count").value || "5", 10)));
  const scoreModel = (q("score_model") && q("score_model").value) ? q("score_model").value : "linear";

  const preSrc = DATA.models.pre_feature_source || "cons_std";
  const preVec = preSrc === "consensus_fallback" ? [momConsensus, coreConsensus] : [momStd, coreStd];
  const preRes = modelTransform(preVec, DATA.models.pre);
  q("pre_factor_vals").textContent = `f1=${{fmt(preRes.f1)}} | f2=${{fmt(preRes.f2)}}`;
  q("pre_score").textContent = `pca composite=${{fmt(preRes.score)}}`;

  const preMktVec = [trailRange, trailVol];
  const preMktRes = modelTransform(preMktVec, DATA.models.pre_mkt);
  q("pre_mkt_factor_vals").textContent = `f1=${{fmt(preMktRes.f1)}} | f2=${{fmt(preMktRes.f2)}}`;

  const trend = DATA.models.trend_latest;
  const momExpected = trend.mom_a + trend.mom_b * momYoy;
  const coreExpected = trend.core_a + trend.core_b * coreYoy;
  const momZAdj = (momActual - momExpected) / momStd;
  const coreZAdj = (coreActual - coreExpected) / coreStd;
  const postVec = [momZAdj, coreZAdj];
  const postRes = modelTransform(postVec, DATA.models.post);
  q("post_factor_vals").textContent = `f1=${{fmt(postRes.f1)}} | f2=${{fmt(postRes.f2)}}`;
  q("post_score").textContent = `pca composite=${{fmt(postRes.score)}} | z_adj=[${{fmt(momZAdj)}}, ${{fmt(coreZAdj)}}]`;

  const pi = impactsFor("pre", scoreModel);
  const mi = impactsFor("pre_mkt", scoreModel);
  const qi = impactsFor("post", scoreModel);
  const piL = impactsFor("pre", "linear");
  const piA = impactsFor("pre", "abs");
  const miL = impactsFor("pre_mkt", "linear");
  const miA = impactsFor("pre_mkt", "abs");
  const qiL = impactsFor("post", "linear");
  const qiA = impactsFor("post", "abs");
  const preRegScoreCurrent = regScore(pi, preRes.f1, preRes.f2);
  const preMktRegScoreCurrent = regScore(mi, preMktRes.f1, preMktRes.f2);
  const postRegScoreCurrent = regScore(qi, postRes.f1, postRes.f2);
  q("pre_score").textContent += ` | regression score=${{fmt(preRegScoreCurrent,2)}} bps`;
  q("post_score").textContent += ` | regression score=${{fmt(postRegScoreCurrent,2)}} bps`;
  q("pre_mkt_score").textContent = `pca composite=${{fmt(preMktRes.score)}} | regression score=${{fmt(preMktRegScoreCurrent,2)}} bps`;

  const rowsReg = DATA.historical_rows.map(r => {{
    const pre_reg_score = regScore(pi, r.pre_factor1, r.pre_factor2);
    const pre_mkt_reg_score = regScore(mi, r.pre_mkt_factor1, r.pre_mkt_factor2);
    const post_reg_score = regScore(qi, r.post_factor1, r.post_factor2);
    const post_hourly_map = toHourlyMap(r.post_hourly_be_bps || []);
    return {{...r, pre_reg_score, pre_mkt_reg_score, post_reg_score, post_hourly_map}};
  }});

  const preScores = rowsReg.map(r => r.pre_reg_score);
  const preMktScores = rowsReg.map(r => r.pre_mkt_reg_score);
  const postScores = rowsReg.map(r => r.post_reg_score);
  const preEdges = edgesFromScores(preScores, bucketCount);
  const preMktEdges = edgesFromScores(preMktScores, bucketCount);
  const postEdges = edgesFromScores(postScores, bucketCount);

  const preStats = bucketStats(rowsReg, "pre_reg_score", "pre_bps", preEdges);
  const preMktStats = bucketStats(rowsReg, "pre_mkt_reg_score", "pre_bps", preMktEdges);
  const postStats = bucketStats(rowsReg, "post_reg_score", "post_bps", postEdges);
  const preBox = bucketBoxStats(rowsReg, "pre_reg_score", "pre_bps", preEdges);
  const preMktBox = bucketBoxStats(rowsReg, "pre_mkt_reg_score", "pre_bps", preMktEdges);
  const postBox = bucketBoxStats(rowsReg, "post_reg_score", "post_bps", postEdges);
  const bucketMaps = {{
    pre: {{ scoreKey: "pre_reg_score", edges: preEdges, bpsKey: "pre_bps", distroKey: "pre_cc_bps", pathKey: "pre_path", label: "Pre", windowLabel: "3pm T-1 -> 8:29am" }},
    pre_mkt: {{ scoreKey: "pre_mkt_reg_score", edges: preMktEdges, bpsKey: "pre_bps", distroKey: "pre_cc_bps", pathKey: "pre_path", label: "Pre (Market)", windowLabel: "3pm T-1 -> 8:29am" }},
    post: {{ scoreKey: "post_reg_score", edges: postEdges, bpsKey: "post_bps", distroKey: "post_cc_bps", pathKey: "post_path", label: "Post", windowLabel: "8:30am -> 5:00pm" }},
  }};

  const preIdx = assignBucket(preRegScoreCurrent, preEdges);
  const postIdx = assignBucket(postRegScoreCurrent, postEdges);
  const preMktIdx = assignBucket(preMktRegScoreCurrent, preMktEdges);
  const preRow = preStats[preIdx];
  const preMktRow = preMktStats[preMktIdx];
  const postRow = postStats[postIdx];

  q("pre_bucket_summary").innerHTML =
    `<div>Bucket <span class="tag">${{preIdx+1}}</span> ` +
    `<span class="mono">[${{fmt(preRow.low)}}, ${{fmt(preRow.high)}}]</span> | N=${{preRow.count}} | mean B/E bps=${{fmt(preRow.mean_bps,2)}}</div>`;
  q("pre_mkt_bucket_summary").innerHTML =
    `<div>Bucket <span class="tag">${{preMktIdx+1}}</span> ` +
    `<span class="mono">[${{fmt(preMktRow.low)}}, ${{fmt(preMktRow.high)}}]</span> | N=${{preMktRow.count}} | mean B/E bps=${{fmt(preMktRow.mean_bps,2)}}</div>`;
  q("post_bucket_summary").innerHTML =
    `<div>Bucket <span class="tag">${{postIdx+1}}</span> ` +
    `<span class="mono">[${{fmt(postRow.low)}}, ${{fmt(postRow.high)}}]</span> | N=${{postRow.count}} | mean B/E bps=${{fmt(postRow.mean_bps,2)}}</div>`;
  const defaultSelected = {{
    pre: {{ mode: "pre", bucketIdx: preIdx }},
    pre_mkt: {{ mode: "pre_mkt", bucketIdx: preMktIdx }},
    post: {{ mode: "post", bucketIdx: postIdx }},
  }};
  if (!SELECTED_BUCKET || !bucketMaps[SELECTED_BUCKET.mode]) {{
    SELECTED_BUCKET = defaultSelected.pre;
  }}

  const setSelected = (mode, idx) => {{
    const maxIdx = Math.max(0, (bucketMaps[mode]?.edges?.length || 2) - 2);
    const safeIdx = Math.max(0, Math.min(maxIdx, Number.isFinite(idx) ? idx : 0));
    SELECTED_BUCKET = {{ mode, bucketIdx: safeIdx }};
    renderBucketTable("pre_bucket_table", preStats, SELECTED_BUCKET.mode==="pre" ? SELECTED_BUCKET.bucketIdx : -1, (i)=>setSelected("pre", i));
    renderBucketTable("pre_mkt_bucket_table", preMktStats, SELECTED_BUCKET.mode==="pre_mkt" ? SELECTED_BUCKET.bucketIdx : -1, (i)=>setSelected("pre_mkt", i));
    renderBucketTable("post_bucket_table", postStats, SELECTED_BUCKET.mode==="post" ? SELECTED_BUCKET.bucketIdx : -1, (i)=>setSelected("post", i));
    renderBoxplot("pre_boxplot", preBox, SELECTED_BUCKET.mode==="pre" ? SELECTED_BUCKET.bucketIdx : preIdx, (i)=>setSelected("pre", i));
    renderBoxplot("pre_mkt_boxplot", preMktBox, SELECTED_BUCKET.mode==="pre_mkt" ? SELECTED_BUCKET.bucketIdx : preMktIdx, (i)=>setSelected("pre_mkt", i));
    renderBoxplot("post_boxplot", postBox, SELECTED_BUCKET.mode==="post" ? SELECTED_BUCKET.bucketIdx : postIdx, (i)=>setSelected("post", i));
    updateDrilldown(SELECTED_BUCKET, rowsReg, bucketMaps);
  }};
  setSelected(SELECTED_BUCKET.mode, SELECTED_BUCKET.bucketIdx);

  const preAssigned = preRegScoreCurrent;
  q("pre_impact_summary").textContent =
    `model=${{scoreModel}} | beta=[${{fmt(pi.beta_factor1,3)}}, ${{fmt(pi.beta_factor2,3)}}], intercept=${{fmt(pi.intercept,3)}}` +
    ` | assigned=${{fmt(preAssigned,2)}} bps | R2=${{fmt(pi.r2,3)}} (lin=${{fmt(piL.r2,3)}}, abs=${{fmt(piA.r2,3)}})`;

  const preMktAssigned = preMktRegScoreCurrent;
  q("pre_mkt_impact_summary").textContent =
    `model=${{scoreModel}} | beta=[${{fmt(mi.beta_factor1,3)}}, ${{fmt(mi.beta_factor2,3)}}], intercept=${{fmt(mi.intercept,3)}}` +
    ` | assigned=${{fmt(preMktAssigned,2)}} bps | R2=${{fmt(mi.r2,3)}} (lin=${{fmt(miL.r2,3)}}, abs=${{fmt(miA.r2,3)}})`;

  const postAssigned = postRegScoreCurrent;
  q("post_impact_summary").textContent =
    `model=${{scoreModel}} | beta=[${{fmt(qi.beta_factor1,3)}}, ${{fmt(qi.beta_factor2,3)}}], intercept=${{fmt(qi.intercept,3)}}` +
    ` | assigned=${{fmt(postAssigned,2)}} bps | R2=${{fmt(qi.r2,3)}} (lin=${{fmt(qiL.r2,3)}}, abs=${{fmt(qiA.r2,3)}})`;

  const interp = DATA.models.interpretation || {{}};
  const preI = interp.pre || {{}};
  const preMI = interp.pre_mkt || {{}};
  const postI = interp.post || {{}};
  q("pre_interp_1").textContent = preI.factor1 || "";
  q("pre_interp_2").textContent = preI.factor2 || "";
  q("pre_mkt_interp_1").textContent = preMI.factor1 || "";
  q("pre_mkt_interp_2").textContent = preMI.factor2 || "";
  q("post_interp_1").textContent = postI.factor1 || "";
  q("post_interp_2").textContent = postI.factor2 || "";
  q("pre_reg_summary").textContent =
    `pre | lin(R2=${{fmt(piL.r2,3)}}, RMSE=${{fmt(piL.rmse,2)}}) | abs(R2=${{fmt(piA.r2,3)}}, RMSE=${{fmt(piA.rmse,2)}})`;
  q("pre_mkt_reg_summary").textContent =
    `pre_mkt | lin(R2=${{fmt(miL.r2,3)}}, RMSE=${{fmt(miL.rmse,2)}}) | abs(R2=${{fmt(miA.r2,3)}}, RMSE=${{fmt(miA.rmse,2)}})`;
  q("post_reg_summary").textContent =
    `post | lin(R2=${{fmt(qiL.r2,3)}}, RMSE=${{fmt(qiL.rmse,2)}}) | abs(R2=${{fmt(qiA.r2,3)}}, RMSE=${{fmt(qiA.rmse,2)}})`;
}}

function init() {{
  try {{
    const meta = DATA.meta || {{}};
    const metaEl = q("metaLine");
    if (metaEl) {{
      metaEl.textContent =
        `Events: ${{meta.event_count}} | Start: ${{meta.start_date}} | Symbol: ${{meta.symbol}} | Generated: ${{meta.generated_at}}`;
    }}
    if (Array.isArray(DATA.warnings) && DATA.warnings.length) {{
      const wp = q("warnPanel");
      if (wp) {{
        wp.style.display = "block";
        wp.innerHTML = `<h3 style="margin:0 0 8px 0;">Warnings</h3>` + DATA.warnings.map(w => `<div class="small warn">- ${{w}}</div>`).join("");
      }}
    }}
    setDefaults();
    renderModelInfo();
    const runBtn = q("runBtn");
    if (runBtn) runBtn.addEventListener("click", recompute);
    recompute();
  }} catch (err) {{
    showError(err && err.stack ? err.stack : err);
  }}
}}
window.addEventListener("error", (e) => {{
  const msg = (e && e.error && e.error.stack) ? e.error.stack : (e && e.message ? e.message : "Unknown runtime error");
  showError(msg);
}});
init();
</script>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CPI two-factor PCA pre/post standalone dashboard")
    parser.add_argument("--excel", default=str(DEFAULT_EXCEL_PATH), help="Path to bbg_event_fields.xlsx")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to economic_events.db")
    parser.add_argument("--symbol", default="TY", help="Futures symbol prefix (e.g., TY)")
    parser.add_argument("--start-date", default="2022-01-01", help="Historical sample start date (YYYY-MM-DD)")
    parser.add_argument("--trend-window", type=int, default=36, help="Rolling window length in events")
    parser.add_argument("--bucket-count", type=int, default=5, help="Default quantile bucket count")
    parser.add_argument("--output", default=None, help="Optional output HTML path")
    return parser.parse_args()


def resolve_excel_path(requested: Path) -> Path:
    if requested.exists():
        return requested
    candidates = [
        Path("Y:/Meini/bbg_event_fields.xlsx"),
        Path("Z:/Meini/bbg_event_fields.xlsx"),
        PROJECT_ROOT / "workspace" / "bbg_event_fields.xlsx",
    ]
    for cand in candidates:
        if cand.exists():
            logger.warning("Requested Excel path missing. Falling back to %s", cand)
            return cand
    raise FileNotFoundError(
        f"Excel file not found at requested path {requested} and no fallback paths exist."
    )


def main() -> None:
    args = parse_args()
    excel_path = resolve_excel_path(Path(args.excel))
    db_path = Path(args.db)

    logger.info("Loading CPI components from %s", excel_path)
    cpi_df = load_cpi_excel_components(excel_path=excel_path, start_date=args.start_date)

    logger.info("Loading CPI timestamps from %s", db_path)
    ts_df = load_cpi_event_timestamps(db_path=db_path, start_date=args.start_date)

    symbol_pattern = f"{args.symbol}%"
    logger.info("Loading OHLCV for %s", symbol_pattern)
    ohlcv_df = _prepare_ohlcv(symbol_pattern=symbol_pattern, start_date=args.start_date)
    market_df = load_market_windows(ohlcv_df=ohlcv_df, event_times=ts_df)

    train_df = build_training_table(cpi_df=cpi_df, ts_df=ts_df, market_df=market_df)
    if len(train_df) < 20:
        raise SystemExit(f"Insufficient cleaned events for stable PCA/regression: {len(train_df)}")

    train_df = fit_post_trend_models(train_df, window=max(8, int(args.trend_window)))
    if len(train_df) < 12:
        raise SystemExit(f"Insufficient events after trend-adjusted filtering: {len(train_df)}")

    warnings: List[str] = []
    X_pre = train_df[["mom_cons_std", "core_mom_cons_std"]].to_numpy(dtype=float)
    pre_feature_source = "cons_std"
    # If uncertainty std columns are flat (as seen in some CPI workbook slices),
    # fallback to consensus levels to preserve meaningful pre-event bucketing.
    if (np.nanstd(X_pre[:, 0]) < EPS) and (np.nanstd(X_pre[:, 1]) < EPS):
        X_pre = train_df[["mom_consensus", "core_mom_consensus"]].to_numpy(dtype=float)
        pre_feature_source = "consensus_fallback"
        warnings.append(
            "Pre-event consensus std dev fields are effectively constant in the historical sample; "
            "using [mom_consensus, core_mom_consensus] as fallback pre-factor inputs."
        )

    X_post = train_df[["mom_z_adj", "core_mom_z_adj"]].to_numpy(dtype=float)
    X_pre_mkt = train_df[["trail_range_bps", "trail_vol_bps"]].to_numpy(dtype=float)
    pre_pca = fit_pca_mode(X_pre)
    pre_mkt_pca = fit_pca_mode(X_pre_mkt)
    post_pca = fit_pca_mode(X_post)

    pre_factors = np.array(pre_pca["factor_scores"], dtype=float)
    pre_mkt_factors = np.array(pre_mkt_pca["factor_scores"], dtype=float)
    post_factors = np.array(post_pca["factor_scores"], dtype=float)
    pre_scores = np.array(pre_pca["composite_scores"], dtype=float)
    pre_mkt_scores = np.array(pre_mkt_pca["composite_scores"], dtype=float)
    post_scores = np.array(post_pca["composite_scores"], dtype=float)
    pre_bps = train_df["pre_bps"].to_numpy(dtype=float)
    post_bps = train_df["post_bps"].to_numpy(dtype=float)

    pre_impacts = {
        "linear": estimate_factor_impacts(pre_factors, pre_bps),
        "abs": estimate_factor_impacts_abs(pre_factors, pre_bps),
    }
    pre_mkt_impacts = {
        "linear": estimate_factor_impacts(pre_mkt_factors, pre_bps),
        "abs": estimate_factor_impacts_abs(pre_mkt_factors, pre_bps),
    }
    post_impacts = {
        "linear": estimate_factor_impacts(post_factors, post_bps),
        "abs": estimate_factor_impacts_abs(post_factors, post_bps),
    }
    pre_buckets = build_buckets(pre_scores, pre_bps, bucket_count=max(2, int(args.bucket_count)))
    pre_mkt_buckets = build_buckets(pre_mkt_scores, pre_bps, bucket_count=max(2, int(args.bucket_count)))
    post_buckets = build_buckets(post_scores, post_bps, bucket_count=max(2, int(args.bucket_count)))

    payload = build_html_payload(
        df=train_df,
        pre_pca=pre_pca,
        pre_mkt_pca=pre_mkt_pca,
        post_pca=post_pca,
        pre_impacts=pre_impacts,
        pre_mkt_impacts=pre_mkt_impacts,
        post_impacts=post_impacts,
        pre_buckets=pre_buckets,
        pre_mkt_buckets=pre_mkt_buckets,
        post_buckets=post_buckets,
        start_date=args.start_date,
        trend_window=int(args.trend_window),
        symbol=args.symbol,
        pre_feature_source=pre_feature_source,
        warnings=warnings,
    )
    html = render_html(payload)

    output_path = (
        Path(args.output)
        if args.output
        else PROJECT_ROOT
        / "workspace"
        / "scenarios"
        / f"cpi_twofactor_pca_dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

    logger.info("Saved dashboard: %s", output_path)
    logger.info(
        "Done. events=%d, start_date=%s, trend_window=%d",
        payload["meta"]["event_count"],
        args.start_date,
        args.trend_window,
    )


if __name__ == "__main__":
    main()

