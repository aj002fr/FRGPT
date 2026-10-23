#!/usr/bin/env python3
"""
Economic release surprise -> TY move (bps) percentile buckets.

Supports Core/Headline CPI and PPI (MoM) via --series. For each historical
release this script computes:
  surprise (pp)     = actual - consensus
  std surprise (z)  = surprise / consensus_std_dev
  post move (bps)   = signed 8:30am -> close move on TY (post_cc_bps)
  max excursion(bps)= one-sided max excursion from 8:30am open (post_bps)

It then groups events into surprise percentile buckets (and discrete surprise
values) and reports the expected (mean/median) move and the max move in bps.

Reuses the data loaders from scripts/cpi_twofactor_pca_dashboard.py so the
bps conventions match the existing CPI/PPI dashboards.

Outputs (under workspace/analysis/<series>_surprise_buckets/):
  - <series>_surprise_buckets.xlsx   (by-value + percentile + events sheets)
  - <series>_surprise_buckets.db     (sqlite tables)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.cpi_twofactor_pca_dashboard import (  # noqa: E402
    DEFAULT_DB_PATH,
    _prepare_ohlcv,
    load_market_windows,
)

# series key -> (label, bbg_data event_name for actual/consensus/std)
SERIES = {
    "core_cpi": ("Core CPI MoM", "Core CPI MoM"),
    "headline_cpi": ("Headline CPI MoM", "CPI MoM"),
    "core_ppi": ("Core PPI MoM", "Core PPI MoM"),
    "headline_ppi": ("Headline PPI MoM", "PPI MoM"),
}

# Default percentile cut points (edges, 0..100) for surprise buckets.
DEFAULT_PCTL_EDGES = [0, 10, 25, 40, 60, 75, 90, 100]


def load_series_from_db(db_path: Path, event_name: str, start_date: str) -> pd.DataFrame:
    """actual/consensus/std + event timestamps from bbg_data for one series."""
    sql = """
    SELECT timestamp, date(timestamp) AS date, actual, consensus, consensus_std_dev
    FROM bbg_data
    WHERE date(timestamp) >= date(?)
      AND event_name = ?
      AND actual IS NOT NULL AND consensus IS NOT NULL
    ORDER BY timestamp
    """
    with sqlite3.connect(str(db_path)) as conn:
        df = pd.read_sql_query(sql, conn, params=(start_date, event_name))
    df = df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return df


def _build_event_times(core_df: pd.DataFrame) -> pd.DataFrame:
    """Event datetimes (tz-aware US/Eastern). load_market_windows resets to 8:30am."""
    ts = pd.to_datetime(core_df["timestamp"], errors="coerce")
    ts = ts.dt.tz_localize("US/Eastern")
    out = pd.DataFrame({"date": core_df["date"], "event_dt": ts})
    return out.dropna(subset=["event_dt"]).drop_duplicates(subset=["date"], keep="last")


def build_event_table(start_date: str, symbol: str, event_name: str) -> pd.DataFrame:
    db_path = Path(DEFAULT_DB_PATH)
    core_df = load_series_from_db(db_path=db_path, event_name=event_name, start_date=start_date)
    ts_df = _build_event_times(core_df)
    ohlcv_df = _prepare_ohlcv(symbol_pattern=f"{symbol}%", start_date=start_date)
    market_df = load_market_windows(ohlcv_df=ohlcv_df, event_times=ts_df)

    ev = core_df.merge(
        market_df[["date", "post_bps", "post_cc_bps"]], on="date", how="inner"
    )
    ev = ev.rename(
        columns={
            "actual": "core_actual",
            "consensus": "core_consensus",
            "consensus_std_dev": "core_cons_std",
        }
    )
    for c in ["core_actual", "core_consensus", "core_cons_std", "post_bps", "post_cc_bps"]:
        ev[c] = pd.to_numeric(ev[c], errors="coerce")
    ev["surprise_pp"] = ev["core_actual"] - ev["core_consensus"]
    ev["std_surprise_z"] = ev["surprise_pp"] / ev["core_cons_std"].replace(0, np.nan)
    # post_cc_bps = signed 8:30 -> 5pm close move; post_bps = max one-sided excursion.
    ev["move_signed_bps"] = ev["post_cc_bps"].astype(float)
    ev["move_abs_bps"] = ev["move_signed_bps"].abs()
    ev["max_excursion_bps"] = ev["post_bps"].astype(float)
    ev = ev.dropna(subset=["surprise_pp", "move_signed_bps", "max_excursion_bps"])
    ev = ev[["date", "core_actual", "core_consensus", "core_cons_std",
             "surprise_pp", "std_surprise_z", "move_signed_bps",
             "move_abs_bps", "max_excursion_bps"]]
    ev = ev.sort_values("surprise_pp").reset_index(drop=True)
    return ev


def assign_percentile_buckets(ev: pd.DataFrame, edges: list[int]) -> pd.DataFrame:
    s = ev["surprise_pp"].to_numpy(dtype=float)
    # Percentile rank of each event's surprise (0..100).
    ranks = pd.Series(s).rank(pct=True).to_numpy() * 100.0
    ev = ev.copy()
    ev["surprise_pctl"] = ranks

    labels = []
    lows = []
    highs = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        labels.append(f"P{lo:02d}-P{hi:02d}")
        lows.append(lo)
        highs.append(hi)

    def _bucket(p: float) -> str:
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            # last bucket inclusive of 100
            if (p >= lo and p < hi) or (hi == 100 and p >= lo):
                return labels[i]
        return labels[-1]

    ev["bucket"] = [_bucket(p) for p in ranks]
    ev["bucket"] = pd.Categorical(ev["bucket"], categories=labels, ordered=True)
    return ev


def summarize_buckets(ev: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for bucket, g in ev.groupby("bucket", observed=True):
        n = len(g)
        if n == 0:
            continue
        # index of worst-case event for reporting context
        imax = g["max_excursion_bps"].idxmax()
        rows.append(
            {
                "bucket": str(bucket),
                "n": n,
                "surprise_min_pp": round(float(g["surprise_pp"].min()), 3),
                "surprise_max_pp": round(float(g["surprise_pp"].max()), 3),
                "surprise_mean_pp": round(float(g["surprise_pp"].mean()), 3),
                "std_surprise_mean_z": round(float(g["std_surprise_z"].mean()), 2),
                # expected magnitude of move
                "expected_abs_move_bps": round(float(g["move_abs_bps"].mean()), 2),
                "median_abs_move_bps": round(float(g["move_abs_bps"].median()), 2),
                # signed direction (does sign line up with surprise?)
                "mean_signed_move_bps": round(float(g["move_signed_bps"].mean()), 2),
                # max move observed in bucket
                "max_abs_move_bps": round(float(g["move_abs_bps"].max()), 2),
                "avg_max_excursion_bps": round(float(g["max_excursion_bps"].mean()), 2),
                "max_excursion_bps": round(float(g["max_excursion_bps"].max()), 2),
                "max_excursion_date": str(g.loc[imax, "date"]),
            }
        )
    out = pd.DataFrame(rows)
    return out


def summarize_by_value(ev: pd.DataFrame) -> pd.DataFrame:
    """Group by discrete surprise value (rounded to 0.1pp) - directly actionable."""
    g = ev.copy()
    g["surprise_bucket_pp"] = g["surprise_pp"].round(1)
    rows = []
    for val, sub in g.groupby("surprise_bucket_pp"):
        n = len(sub)
        imax = sub["max_excursion_bps"].idxmax()
        rows.append(
            {
                "surprise_pp": round(float(val), 1),
                "n": n,
                "pctl_of_distribution": round(float((ev["surprise_pp"] < val).mean() * 100.0), 1),
                "mean_std_surprise_z": round(float(sub["std_surprise_z"].mean()), 2),
                "expected_abs_move_bps": round(float(sub["move_abs_bps"].mean()), 2),
                "median_abs_move_bps": round(float(sub["move_abs_bps"].median()), 2),
                "mean_signed_move_bps": round(float(sub["move_signed_bps"].mean()), 2),
                "max_abs_move_bps": round(float(sub["move_abs_bps"].max()), 2),
                "avg_max_excursion_bps": round(float(sub["max_excursion_bps"].mean()), 2),
                "max_excursion_bps": round(float(sub["max_excursion_bps"].max()), 2),
                "max_excursion_date": str(sub.loc[imax, "date"]),
            }
        )
    return pd.DataFrame(rows).sort_values("surprise_pp").reset_index(drop=True)


# Standardized-surprise (z = surprise / consensus_std_dev) bin edges in sigma.
DEFAULT_Z_EDGES = [-np.inf, -2.0, -1.0, -0.5, 0.5, 1.0, 2.0, np.inf]
Z_LABELS = ["<= -2sig", "-2..-1sig", "-1..-0.5sig", "-0.5..+0.5sig",
            "+0.5..+1sig", "+1..+2sig", ">= +2sig"]


def summarize_by_zscore(ev: pd.DataFrame) -> pd.DataFrame:
    """Group by standardized surprise z = surprise / consensus_std_dev (sigma bins)."""
    g = ev.dropna(subset=["std_surprise_z"]).copy()
    g["zbin"] = pd.cut(g["std_surprise_z"], bins=DEFAULT_Z_EDGES, labels=Z_LABELS)
    rows = []
    for zbin, sub in g.groupby("zbin", observed=True):
        n = len(sub)
        if n == 0:
            continue
        imax = sub["max_excursion_bps"].idxmax()
        rows.append(
            {
                "z_bucket": str(zbin),
                "n": n,
                "z_min": round(float(sub["std_surprise_z"].min()), 2),
                "z_max": round(float(sub["std_surprise_z"].max()), 2),
                "z_mean": round(float(sub["std_surprise_z"].mean()), 2),
                "surprise_mean_pp": round(float(sub["surprise_pp"].mean()), 3),
                "expected_abs_move_bps": round(float(sub["move_abs_bps"].mean()), 2),
                "median_abs_move_bps": round(float(sub["move_abs_bps"].median()), 2),
                "mean_signed_move_bps": round(float(sub["move_signed_bps"].mean()), 2),
                "max_abs_move_bps": round(float(sub["move_abs_bps"].max()), 2),
                "avg_max_excursion_bps": round(float(sub["max_excursion_bps"].mean()), 2),
                "max_excursion_bps": round(float(sub["max_excursion_bps"].max()), 2),
                "max_excursion_date": str(sub.loc[imax, "date"]),
            }
        )
    out = pd.DataFrame(rows)
    # keep sigma order
    order = {lab: i for i, lab in enumerate(Z_LABELS)}
    out = out.sort_values("z_bucket", key=lambda s: s.map(order)).reset_index(drop=True)
    return out


def write_outputs(
    ev: pd.DataFrame, buckets: pd.DataFrame, by_value: pd.DataFrame,
    by_z: pd.DataFrame, series: str,
) -> tuple[Path, Path]:
    out_dir = PROJECT_ROOT / "workspace" / "analysis" / f"{series}_surprise_buckets"
    out_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = out_dir / f"{series}_surprise_buckets.xlsx"
    db_path = out_dir / f"{series}_surprise_buckets.db"

    ev_out = ev.copy()
    ev_out["bucket"] = ev_out["bucket"].astype(str)
    round_cols = [
        "core_actual", "core_consensus", "core_cons_std", "surprise_pp",
        "std_surprise_z", "surprise_pctl", "move_signed_bps", "move_abs_bps",
        "max_excursion_bps",
    ]
    for c in round_cols:
        ev_out[c] = ev_out[c].astype(float).round(3)

    try:
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as xl:
            by_z.to_excel(xl, sheet_name="by_zscore", index=False)
            by_value.to_excel(xl, sheet_name="by_surprise_value", index=False)
            buckets.to_excel(xl, sheet_name="percentile_buckets", index=False)
            ev_out.to_excel(xl, sheet_name="events", index=False)
    except Exception as exc:  # pragma: no cover - fallback if no excel engine
        print(f"[warn] Excel export failed ({exc}); writing CSVs instead.")
        by_z.to_csv(out_dir / "by_zscore.csv", index=False)
        by_value.to_csv(out_dir / "by_surprise_value.csv", index=False)
        buckets.to_csv(out_dir / "percentile_buckets.csv", index=False)
        ev_out.to_csv(out_dir / "events.csv", index=False)

    with sqlite3.connect(str(db_path)) as conn:
        ev_out.to_sql("events", conn, if_exists="replace", index=False)
        buckets.to_sql("percentile_buckets", conn, if_exists="replace", index=False)
        by_value.to_sql("by_surprise_value", conn, if_exists="replace", index=False)
        by_z.to_sql("by_zscore", conn, if_exists="replace", index=False)

    return xlsx_path, db_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Economic surprise percentile buckets vs TY bps moves")
    p.add_argument("--series", default="core_cpi", choices=list(SERIES.keys()),
                   help="Which release series to analyze")
    p.add_argument("--start-date", default="2022-01-01", help="Sample start (YYYY-MM-DD)")
    p.add_argument("--symbol", default="TY", help="Futures symbol prefix")
    p.add_argument(
        "--edges",
        default=",".join(str(e) for e in DEFAULT_PCTL_EDGES),
        help="Comma-separated percentile edges 0..100",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    label, event_name = SERIES[args.series]
    edges = [int(x) for x in args.edges.split(",")]
    ev = build_event_table(start_date=args.start_date, symbol=args.symbol, event_name=event_name)
    ev = assign_percentile_buckets(ev, edges)
    buckets = summarize_buckets(ev)
    by_value = summarize_by_value(ev)
    by_z = summarize_by_zscore(ev)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    print(f"\n{label}: {len(ev)} events  (start={args.start_date}, symbol={args.symbol})")
    print(f"Surprise range: {ev['surprise_pp'].min():+.2f} .. {ev['surprise_pp'].max():+.2f} pp")
    print(f"\n=== By standardized surprise z (sigma) ({label} -> TY) ===")
    print(by_z.to_string(index=False))
    print(f"\n=== By surprise value ({label} -> TY) ===")
    print(by_value.to_string(index=False))
    print(f"\n=== Surprise percentile buckets ({label} -> TY) ===")
    print(buckets.to_string(index=False))

    xlsx_path, db_path = write_outputs(ev, buckets, by_value, by_z, args.series)
    print(f"\nWrote: {xlsx_path}")
    print(f"Wrote: {db_path}")


if __name__ == "__main__":
    main()
