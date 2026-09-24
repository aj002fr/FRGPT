#!/usr/bin/env python3
"""
NFP Two-Factor PCA Pre/Post Dashboard (Standalone HTML).

Builds a self-contained HTML dashboard analogous to the CPI two-factor prototype,
but using NFP + UER from the same Bloomberg workbook (NFP sheet).
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.cpi_twofactor_pca_dashboard as base

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

EPS = 1e-9
DEFAULT_EXCEL_PATH = Path("Y:/Meini/bbg_event_fields.xlsx")
DEFAULT_DB_PATH = PROJECT_ROOT / "workspace" / "economic_events.db"


def _find_nfp_sheet(workbook: Dict[str, pd.DataFrame]) -> Tuple[str, pd.DataFrame]:
    best_name: Optional[str] = None
    best_df: Optional[pd.DataFrame] = None
    best_score = -1
    for sheet_name, df in workbook.items():
        score = 0
        low_name = sheet_name.lower()
        if "nfp" in low_name:
            score += 4
        scan = df.head(20).fillna("").astype(str).to_numpy().ravel().tolist()
        blob = " ".join(str(x).lower() for x in scan)
        if "nfp" in blob:
            score += 2
        if "uer" in blob or "unemployment rate" in blob:
            score += 2
        if score > best_score:
            best_score = score
            best_name = sheet_name
            best_df = df
    if best_df is None or best_name is None:
        raise ValueError("Could not find an NFP sheet in the workbook.")
    return best_name, best_df


def _find_component_col(df: pd.DataFrame, target: str) -> Optional[int]:
    # Bloomberg workbook usually stores component labels around row index 3.
    for row in range(min(8, len(df))):
        for col in range(df.shape[1]):
            label = base._normalize_label(df.iat[row, col])
            if not label:
                continue
            if target == "nfp":
                if label == "nfp" or label.startswith("nfp "):
                    return col
            elif target == "uer":
                if label == "uer" or label.startswith("uer ") or "unemployment rate" in label:
                    return col
    return None


def load_nfp_excel_components(excel_path: Path, start_date: str) -> pd.DataFrame:
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    wb = pd.read_excel(excel_path, sheet_name=None, header=None)
    sheet_name, df = _find_nfp_sheet(wb)
    logger.info("Using NFP sheet: %s", sheet_name)

    nfp_col = _find_component_col(df, "nfp")
    uer_col = _find_component_col(df, "uer")
    if nfp_col is None or uer_col is None:
        raise ValueError("Missing NFP/UER component blocks in workbook NFP sheet.")

    by_date: Dict[str, Dict[str, float]] = {}
    for key, base_col in [("nfp", nfp_col), ("uer", uer_col)]:
        date_col = base_col
        actual_col = base_col + 1
        consensus_col = base_col + 2
        std_col = base_col + 3
        for r in range(4, len(df)):
            date_str = base._parse_date_string(df.iat[r, date_col] if date_col < df.shape[1] else None)
            if not date_str or date_str < start_date:
                continue
            actual = base._safe_float(df.iat[r, actual_col] if actual_col < df.shape[1] else None)
            consensus = base._safe_float(
                df.iat[r, consensus_col] if consensus_col < df.shape[1] else None
            )
            std_val = base._safe_float(df.iat[r, std_col] if std_col < df.shape[1] else None)
            row = by_date.setdefault(date_str, {})
            if key == "nfp":
                if actual is not None:
                    row["nfp_actual"] = actual
                if consensus is not None:
                    row["nfp_consensus"] = consensus
                if std_val is not None:
                    row["nfp_cons_std"] = std_val
            else:
                if actual is not None:
                    row["uer_actual"] = actual
                if consensus is not None:
                    row["uer_consensus"] = consensus
                if std_val is not None:
                    row["uer_cons_std"] = std_val

    rows: List[Dict[str, object]] = []
    for date_str, vals in sorted(by_date.items()):
        rows.append({"date": date_str, **vals})
    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError("No NFP/UER rows parsed from Excel after start_date filter.")
    logger.info("Parsed %d NFP/UER rows from Excel", len(out))
    return out


def load_nfp_event_timestamps(db_path: Path, start_date: str) -> pd.DataFrame:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    sql = """
    SELECT timestamp
    FROM bbg_data
    WHERE date(timestamp) >= date(?)
      AND LOWER(db_event_name) = LOWER('Non Farm Payrolls')
    ORDER BY timestamp
    """
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(sql, (start_date,)).fetchall()
    if not rows:
        raise ValueError("No NFP timestamps found in bbg_data.")

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
    logger.info("Loaded %d NFP event timestamps from DB", len(out))
    return out


def build_training_table_nfp(nfp_df: pd.DataFrame, ts_df: pd.DataFrame, market_df: pd.DataFrame) -> pd.DataFrame:
    # Map NFP/UER columns onto the shared CPI-script schema so we can reuse UI + modeling helpers.
    x = nfp_df.copy()
    x["date_dt"] = pd.to_datetime(x["date"], errors="coerce")
    x = x.dropna(subset=["date_dt"]).sort_values("date_dt")

    ts = ts_df.copy()
    ts["date_dt"] = pd.to_datetime(ts["date"], errors="coerce")
    ts = ts.dropna(subset=["date_dt"]).sort_values("date_dt")

    m = market_df.copy()
    m["date_dt"] = pd.to_datetime(m["date"], errors="coerce")
    m = m.dropna(subset=["date_dt"]).sort_values("date_dt")

    x = x.rename(
        columns={
            "nfp_actual": "mom_actual",
            "nfp_consensus": "mom_consensus",
            "nfp_cons_std": "mom_cons_std",
            "uer_actual": "core_mom_actual",
            "uer_consensus": "core_mom_consensus",
            "uer_cons_std": "core_mom_cons_std",
        }
    )
    x["mom_yoy_level"] = x["mom_consensus"]
    x["core_yoy_level"] = x["core_mom_consensus"]

    df = ts.merge(
        x[
            [
                "date",
                "date_dt",
                "mom_actual",
                "mom_consensus",
                "mom_cons_std",
                "core_mom_actual",
                "core_mom_consensus",
                "core_mom_cons_std",
                "mom_yoy_level",
                "core_yoy_level",
            ]
        ],
        on=["date", "date_dt"],
        how="inner",
    )
    df = df.merge(
        m[
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
    required = [
        "mom_actual",
        "mom_consensus",
        "mom_cons_std",
        "core_mom_actual",
        "core_mom_consensus",
        "core_mom_cons_std",
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
    for c in required:
        if c not in df.columns:
            df[c] = np.nan
    df = df.dropna(subset=required).copy()
    df = df[(df["mom_cons_std"] > EPS) & (df["core_mom_cons_std"] > EPS)].copy()

    # Post-event uses explicit (actual, consensus, std dev) for both NFP and UER.
    df["mom_expected"] = df["mom_consensus"]
    df["core_mom_expected"] = df["core_mom_consensus"]
    df["mom_a"] = 0.0
    df["mom_b"] = 1.0
    df["core_a"] = 0.0
    df["core_b"] = 1.0
    df["mom_z_adj"] = (df["mom_actual"] - df["mom_consensus"]) / df["mom_cons_std"].clip(lower=EPS)
    df["core_mom_z_adj"] = (df["core_mom_actual"] - df["core_mom_consensus"]) / df[
        "core_mom_cons_std"
    ].clip(lower=EPS)
    df["mom_z_adj"] = base._winsorize_series(df["mom_z_adj"].to_numpy(dtype=float))
    df["core_mom_z_adj"] = base._winsorize_series(df["core_mom_z_adj"].to_numpy(dtype=float))
    df = df.dropna(subset=["mom_z_adj", "core_mom_z_adj"]).sort_values("event_dt").reset_index(drop=True)
    logger.info("Training rows after quality filters: %d", len(df))
    return df


def _retitle_html_for_nfp(html: str) -> str:
    replacements = {
        "CPI Two-Factor PCA Bucketing Dashboard": "NFP Two-Factor PCA Bucketing Dashboard",
        "CPI Two-Factor PCA Bucketing": "NFP Two-Factor PCA Bucketing",
        "Headline MoM Actual": "NFP Actual",
        "Headline MoM Consensus": "NFP Consensus",
        "Headline MoM Std Dev": "NFP Std Dev",
        "Core MoM Actual": "UER Actual",
        "Core MoM Consensus": "UER Consensus",
        "Core MoM Std Dev": "UER Std Dev",
        "Headline YoY Level": "NFP Consensus (Post)",
        "Core YoY Level": "UER Consensus (Post)",
        "Post-event trend proxy: rolling regression expected MoM from YoY (latest coefficients).": (
            "Post-event uses standardized surprises for each component: (actual - consensus) / std dev."
        ),
    }
    out = html
    for old, new in replacements.items():
        out = out.replace(old, new)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NFP two-factor PCA pre/post standalone dashboard")
    parser.add_argument("--excel", default=str(DEFAULT_EXCEL_PATH), help="Path to bbg_event_fields.xlsx")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to economic_events.db")
    parser.add_argument("--symbol", default="TY", help="Futures symbol prefix (e.g., TY)")
    parser.add_argument("--start-date", default="2022-01-01", help="Historical sample start date (YYYY-MM-DD)")
    parser.add_argument(
        "--bucket-count", type=int, default=5, help="Default quantile bucket count (>=2)"
    )
    parser.add_argument("--output", default=None, help="Optional output HTML path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    excel_path = base.resolve_excel_path(Path(args.excel))
    db_path = Path(args.db)

    logger.info("Loading NFP/UER components from %s", excel_path)
    nfp_df = load_nfp_excel_components(excel_path=excel_path, start_date=args.start_date)
    logger.info("Loading NFP timestamps from %s", db_path)
    ts_df = load_nfp_event_timestamps(db_path=db_path, start_date=args.start_date)

    symbol_pattern = f"{args.symbol}%"
    logger.info("Loading OHLCV for %s", symbol_pattern)
    ohlcv_df = base._prepare_ohlcv(symbol_pattern=symbol_pattern, start_date=args.start_date)
    market_df = base.load_market_windows(ohlcv_df=ohlcv_df, event_times=ts_df)

    train_df = build_training_table_nfp(nfp_df=nfp_df, ts_df=ts_df, market_df=market_df)
    if len(train_df) < 20:
        raise SystemExit(f"Insufficient cleaned events for stable PCA/regression: {len(train_df)}")

    warnings: List[str] = []
    pre_feature_source = "cons_std"
    X_pre = train_df[["mom_cons_std", "core_mom_cons_std"]].to_numpy(dtype=float)
    if (np.nanstd(X_pre[:, 0]) < EPS) and (np.nanstd(X_pre[:, 1]) < EPS):
        X_pre = train_df[["mom_consensus", "core_mom_consensus"]].to_numpy(dtype=float)
        pre_feature_source = "consensus_fallback"
        warnings.append(
            "Pre-event NFP/UER std-dev fields are effectively constant in this sample; "
            "fallback to consensus-level pre-factor inputs."
        )

    X_post = train_df[["mom_z_adj", "core_mom_z_adj"]].to_numpy(dtype=float)
    X_pre_mkt = train_df[["trail_range_bps", "trail_vol_bps"]].to_numpy(dtype=float)
    pre_pca = base.fit_pca_mode(X_pre)
    pre_mkt_pca = base.fit_pca_mode(X_pre_mkt)
    post_pca = base.fit_pca_mode(X_post)

    pre_factors = np.array(pre_pca["factor_scores"], dtype=float)
    pre_mkt_factors = np.array(pre_mkt_pca["factor_scores"], dtype=float)
    post_factors = np.array(post_pca["factor_scores"], dtype=float)
    pre_scores = np.array(pre_pca["composite_scores"], dtype=float)
    pre_mkt_scores = np.array(pre_mkt_pca["composite_scores"], dtype=float)
    post_scores = np.array(post_pca["composite_scores"], dtype=float)
    pre_bps = train_df["pre_bps"].to_numpy(dtype=float)
    post_bps = train_df["post_bps"].to_numpy(dtype=float)

    pre_impacts = {
        "linear": base.estimate_factor_impacts(pre_factors, pre_bps),
        "abs": base.estimate_factor_impacts_abs(pre_factors, pre_bps),
    }
    pre_mkt_impacts = {
        "linear": base.estimate_factor_impacts(pre_mkt_factors, pre_bps),
        "abs": base.estimate_factor_impacts_abs(pre_mkt_factors, pre_bps),
    }
    post_impacts = {
        "linear": base.estimate_factor_impacts(post_factors, post_bps),
        "abs": base.estimate_factor_impacts_abs(post_factors, post_bps),
    }
    pre_buckets = base.build_buckets(pre_scores, pre_bps, bucket_count=max(2, int(args.bucket_count)))
    pre_mkt_buckets = base.build_buckets(
        pre_mkt_scores, pre_bps, bucket_count=max(2, int(args.bucket_count))
    )
    post_buckets = base.build_buckets(post_scores, post_bps, bucket_count=max(2, int(args.bucket_count)))

    payload = base.build_html_payload(
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
        trend_window=0,
        symbol=args.symbol,
        pre_feature_source=pre_feature_source,
        warnings=warnings,
    )
    payload["models"]["interpretation"]["post"] = {
        "factor1": "Post-event Factor 1 captures common-direction standardized surprise pressure from NFP and UER.",
        "factor2": "Post-event Factor 2 captures relative imbalance between NFP and UER standardized surprises.",
    }

    html = _retitle_html_for_nfp(base.render_html(payload))
    output_path = (
        Path(args.output)
        if args.output
        else PROJECT_ROOT
        / "workspace"
        / "scenarios"
        / f"nfp_twofactor_pca_dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("Saved dashboard: %s", output_path)


if __name__ == "__main__":
    main()
