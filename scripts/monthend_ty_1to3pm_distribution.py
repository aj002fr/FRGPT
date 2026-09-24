#!/usr/bin/env python3
"""
Month-end TY 1pm->3pm ET move distribution
===========================================

For every month in the available history, find the LAST business day of the
month and measure the TY (10Y future) price move from 1:00pm ET to 3:00pm ET.
Report the distribution of those moves in basis points.

bps convention: repo-standard `rules/treasury_price_conversion.points_to_bps`
(1.0 price point = 8 bps). Raw price-point moves are also printed so the move
can be re-scaled to the 16 bps/point (10Y DV01) convention if preferred.

Usage:
    py -3 scripts/monthend_ty_1to3pm_distribution.py
    py -3 scripts/monthend_ty_1to3pm_distribution.py --start-year 2022 --plot
"""
from __future__ import annotations

import argparse
import sys
import warnings
from datetime import datetime, date, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
import pandas as pd

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD  # noqa: E402
from rules.treasury_price_conversion import points_to_bps  # noqa: E402

ET = ZoneInfo("America/New_York")


def last_business_day(year: int, month: int) -> date:
    """Last Mon-Fri of a calendar month (no holiday calendar available)."""
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d -= timedelta(days=1)
    return d


def get_connection():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD,
    )


def fetch_window(conn, day: date, t_start: time, t_end: time, pad_min: int = 10) -> pd.DataFrame:
    """Fetch TY 1m bars around [t_start, t_end] ET for `day`, tz-aware ET index."""
    start_et = datetime.combine(day, t_start, tzinfo=ET) - timedelta(minutes=pad_min)
    end_et = datetime.combine(day, t_end, tzinfo=ET) + timedelta(minutes=pad_min)
    sql = """
        SELECT ts_event, symbol, close, volume
        FROM ohlcv_1m
        WHERE symbol ILIKE 'TY%%'
          AND ts_event >= %s AND ts_event <= %s
          AND close >= 80 AND close <= 150
        ORDER BY ts_event ASC
    """
    df = pd.read_sql_query(
        sql, conn,
        params=(start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)),
    )
    if df.empty:
        return df
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True).dt.tz_convert(ET)
    # Collapse multiple contracts per minute -> keep highest-volume (front month proxy)
    df = (
        df.sort_values(["ts_event", "volume"], ascending=[True, False])
          .drop_duplicates(subset="ts_event", keep="first")
          .set_index("ts_event")
          .sort_index()
    )
    return df


def price_asof(df: pd.DataFrame, day: date, hh: int, mm: int, tol_min: int = 10) -> float | None:
    """Last close at or before target time; fall back to nearest within tol_min."""
    if df.empty:
        return None
    target = pd.Timestamp(datetime.combine(day, time(hh, mm), tzinfo=ET))
    at_or_before = df[df.index <= target]
    if not at_or_before.empty and (target - at_or_before.index[-1]) <= timedelta(minutes=tol_min):
        return float(at_or_before["close"].iloc[-1])
    # nearest bar within tolerance
    deltas = (df.index - target).total_seconds().__abs__()
    idx = deltas.argmin()
    if deltas[idx] <= tol_min * 60:
        return float(df["close"].iloc[idx])
    return None


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start-year", type=int, default=None, help="Earliest year to include")
    ap.add_argument("--from-time", default="13:00", help="Window start ET HH:MM (default 13:00)")
    ap.add_argument("--to-time", default="15:00", help="Window end ET HH:MM (default 15:00)")
    ap.add_argument("--plot", action="store_true", help="Save histogram PNG")
    args = ap.parse_args()

    t_start = _parse_hhmm(args.from_time)
    t_end = _parse_hhmm(args.to_time)
    win = f"{args.from_time}->{args.to_time}"
    tag = f"{args.from_time.replace(':','')}_{args.to_time.replace(':','')}"

    conn = get_connection()

    # Determine available date range for TY
    cur = conn.cursor()
    cur.execute("SELECT MIN(ts_event), MAX(ts_event) FROM ohlcv_1m WHERE symbol ILIKE 'TY%%'")
    dmin, dmax = cur.fetchone()
    if dmin is None:
        print("No TY data found in ohlcv_1m.")
        return 1
    dmin_et = pd.Timestamp(dmin).tz_convert(ET).date()
    dmax_et = pd.Timestamp(dmax).tz_convert(ET).date()
    print(f"TY data coverage: {dmin_et} -> {dmax_et}")

    start_year = args.start_year or dmin_et.year

    # Build list of month-end business days within coverage
    months: list[date] = []
    y, m = start_year, 1
    while date(y, m, 1) <= dmax_et:
        lbd = last_business_day(y, m)
        if dmin_et <= lbd <= dmax_et:
            months.append(lbd)
        m += 1
        if m > 12:
            m, y = 1, y + 1

    rows = []
    for d in months:
        df = fetch_window(conn, d, t_start, t_end)
        p1 = price_asof(df, d, t_start.hour, t_start.minute)
        p3 = price_asof(df, d, t_end.hour, t_end.minute)
        if p1 is None or p3 is None:
            rows.append({"date": d, "p_start": p1, "p_end": p3, "move_pts": None, "move_bps": None})
            continue
        move_pts = p3 - p1
        rows.append({
            "date": d,
            "p_start": round(p1, 5),
            "p_end": round(p3, 5),
            "move_pts": round(move_pts, 5),
            "move_bps": round(points_to_bps(move_pts), 3),
        })
    conn.close()

    res = pd.DataFrame(rows)
    valid = res.dropna(subset=["move_bps"]).copy()

    print(f"\nMonth-end days evaluated: {len(res)}  |  with valid {win} prices: {len(valid)}")
    if res["move_bps"].isna().any():
        missing = res[res["move_bps"].isna()]["date"].tolist()
        print(f"Missing data on {len(missing)} month-ends (likely holidays / no coverage): "
              + ", ".join(str(x) for x in missing[:12]) + (" ..." if len(missing) > 12 else ""))

    print(f"\nPer month-end TY move ({win} ET):")
    with pd.option_context("display.max_rows", None, "display.width", 120):
        print(valid[["date", "p_start", "p_end", "move_pts", "move_bps"]].to_string(index=False))

    b = valid["move_bps"].astype(float)
    n = len(b)
    if n == 0:
        print("\nNo valid observations.")
        return 1

    print("\n" + "=" * 60)
    print(f"DISTRIBUTION OF TY MOVE, month-end {win} ET  (bps; +=rally)")
    print("=" * 60)
    print(f"  N            : {n}")
    print(f"  Mean         : {b.mean():+.2f} bps")
    print(f"  Median       : {b.median():+.2f} bps")
    print(f"  Std dev      : {b.std(ddof=1):.2f} bps")
    print(f"  Min          : {b.min():+.2f} bps  ({valid.loc[b.idxmin(),'date']})")
    print(f"  Max          : {b.max():+.2f} bps  ({valid.loc[b.idxmax(),'date']})")
    print(f"  Mean |move|  : {b.abs().mean():.2f} bps")
    for q in (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95):
        print(f"  p{int(q*100):<3d}        : {b.quantile(q):+.2f} bps")
    up = (b > 0).sum(); dn = (b < 0).sum(); fl = (b == 0).sum()
    print(f"  Up / Down / Flat : {up} / {dn} / {fl}  ({up/n*100:.0f}% up)")

    # ASCII histogram
    print("\nHistogram (bin width = 2 bps):")
    lo = float(b.min()); hi = float(b.max())
    import math
    bin_w = 2.0
    start = math.floor(lo / bin_w) * bin_w
    counts: dict[float, int] = {}
    edge = start
    while edge <= hi + bin_w:
        counts[edge] = int(((b >= edge) & (b < edge + bin_w)).sum())
        edge += bin_w
    maxc = max(counts.values()) or 1
    for edge in sorted(counts):
        c = counts[edge]
        bar = "#" * int(round(c / maxc * 40))
        print(f"  [{edge:+5.0f},{edge+bin_w:+5.0f}) {c:3d} {bar}")

    print("\nNote: 1.0 price point = 8 bps (repo rules/treasury_price_conversion).")
    print("      For the 10Y DV01 convention (1 pt = 16 bps), multiply move_bps by 2.")

    # Directional sub-distributions
    rally = b[b > 0]
    selloff = b[b < 0]

    def _dir_stats(label: str, s: pd.Series) -> None:
        if len(s) == 0:
            print(f"\n{label}: no observations")
            return
        a = s.abs()
        print(f"\n{label}  (N={len(s)}, {len(s)/n*100:.0f}% of all months)")
        print(f"  Mean |move|  : {a.mean():.2f} bps")
        print(f"  Median |move|: {a.median():.2f} bps")
        print(f"  Std dev      : {s.std(ddof=1) if len(s) > 1 else 0:.2f} bps")
        print(f"  Max |move|   : {a.max():.2f} bps  ({valid.loc[s.abs().idxmax(),'date']})")
        for q in (0.50, 0.75, 0.90, 0.95):
            print(f"  p{int(q*100):<3d} |move|  : {a.quantile(q):.2f} bps")

    print("\n" + "=" * 60)
    print("DIRECTIONAL BREAKDOWN")
    print("=" * 60)
    _dir_stats("RALLY  (price up, move > 0)", rally)
    _dir_stats("SELL-OFF (price down, move < 0)", selloff)

    if args.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            out_dir = ROOT / "workspace" / "analysis"
            out_dir.mkdir(parents=True, exist_ok=True)

            def _save(series, title, color, fname, use_abs=False):
                data = series.abs() if use_abs else series
                fig, ax = plt.subplots(figsize=(9, 5))
                ax.hist(data, bins=18, color=color, edgecolor="white")
                if len(data):
                    ax.axvline(data.mean(), color="black", ls="--", lw=1.5,
                               label=f"mean {data.mean():+.2f} bps  (N={len(data)})")
                    ax.legend()
                ax.set_title(title)
                ax.set_xlabel("|move| (bps)" if use_abs else "move (bps)")
                ax.set_ylabel("count")
                fig.tight_layout()
                p = out_dir / fname
                fig.savefig(p, dpi=130)
                plt.close(fig)
                return p

            p_all = _save(b, f"Month-end TY move {win} ET (bps, +=rally)",
                          "#2c7fb8", f"monthend_ty_{tag}_hist.png")
            p_rally = _save(rally, f"Month-end TY RALLY {win} ET (price up, bps)",
                            "#1a9850", f"monthend_ty_{tag}_rally.png")
            p_sell = _save(selloff, f"Month-end TY SELL-OFF {win} ET (price down, bps)",
                           "#d73027", f"monthend_ty_{tag}_selloff.png")
            print(f"\nSaved combined  -> {p_all}")
            print(f"Saved rally     -> {p_rally}")
            print(f"Saved sell-off  -> {p_sell}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n(plot skipped: {exc})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
