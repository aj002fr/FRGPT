#!/usr/bin/env python3
"""
Month-end TY: 3pm->5pm move conditioned on the 1pm->3pm move
============================================================

For each month-end (last business day), measure:
  leg1 = 1:00pm -> 3:00pm ET move
  leg2 = 3:00pm -> 5:00pm ET move
Then split month-ends into "1-3 SELL-OFF" (leg1 < 0) vs "1-3 RALLY" (leg1 > 0)
and report the distribution of the subsequent 3-5pm move (leg2) for each group.

bps convention: rules/treasury_price_conversion.points_to_bps (1 pt = 8 bps).

Usage:
    py -3 scripts/monthend_ty_conditional_3to5.py
    py -3 scripts/monthend_ty_conditional_3to5.py --start-year 2022 --plot
"""
from __future__ import annotations

import argparse
import sys
import warnings
from datetime import date, time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

from rules.treasury_price_conversion import points_to_bps  # noqa: E402
from scripts.monthend_ty_1to3pm_distribution import (  # noqa: E402
    ET, last_business_day, get_connection, fetch_window, price_asof,
)

T1, T3 = time(13, 0), time(15, 0)  # conditioning leg = 1pm->3pm


def _hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def describe(label: str, s: pd.Series, dates: pd.Series) -> None:
    s = s.astype(float)
    n = len(s)
    print(f"\n{label}  (N={n})")
    if n == 0:
        print("  (no observations)")
        return
    up = (s > 0).sum(); dn = (s < 0).sum(); fl = (s == 0).sum()
    print(f"  Mean         : {s.mean():+.2f} bps")
    print(f"  Median       : {s.median():+.2f} bps")
    print(f"  Std dev      : {s.std(ddof=1) if n > 1 else 0:.2f} bps")
    print(f"  Mean |move|  : {s.abs().mean():.2f} bps")
    print(f"  Min / Max    : {s.min():+.2f} / {s.max():+.2f} bps")
    for q in (0.10, 0.25, 0.50, 0.75, 0.90):
        print(f"  p{int(q*100):<3d}        : {s.quantile(q):+.2f} bps")
    print(f"  3-5 Up/Down/Flat : {up}/{dn}/{fl}  ({up/n*100:.0f}% up)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start-year", type=int, default=None)
    ap.add_argument("--out-from", default="15:00", help="Outcome leg start ET HH:MM (default 15:00)")
    ap.add_argument("--out-to", default="17:00", help="Outcome leg end ET HH:MM (default 17:00)")
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()

    o_start, o_end = _hhmm(args.out_from), _hhmm(args.out_to)
    owin = f"{args.out_from}-{args.out_to}"
    otag = f"{args.out_from.replace(':','')}_{args.out_to.replace(':','')}"

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT MIN(ts_event), MAX(ts_event) FROM ohlcv_1m WHERE symbol ILIKE 'TY%%'")
    dmin, dmax = cur.fetchone()
    dmin_et = pd.Timestamp(dmin).tz_convert(ET).date()
    dmax_et = pd.Timestamp(dmax).tz_convert(ET).date()
    print(f"TY data coverage: {dmin_et} -> {dmax_et}")
    start_year = args.start_year or dmin_et.year

    months: list[date] = []
    y, m = start_year, 1
    while date(y, m, 1) <= dmax_et:
        lbd = last_business_day(y, m)
        if dmin_et <= lbd <= dmax_et:
            months.append(lbd)
        m += 1
        if m > 12:
            m, y = 1, y + 1

    fetch_end = max(T3, o_end)
    rows = []
    for d in months:
        df = fetch_window(conn, d, T1, fetch_end)
        p1 = price_asof(df, d, 13, 0)
        p3 = price_asof(df, d, 15, 0)
        po1 = price_asof(df, d, o_start.hour, o_start.minute)
        po2 = price_asof(df, d, o_end.hour, o_end.minute)
        if None in (p1, p3, po1, po2):
            continue
        rows.append({
            "date": d,
            "leg1_bps": round(points_to_bps(p3 - p1), 3),
            "leg2_bps": round(points_to_bps(po2 - po1), 3),
            "full_bps": round(points_to_bps(po2 - p1), 3),
        })
    conn.close()

    res = pd.DataFrame(rows)
    if res.empty:
        print("No complete 1pm/3pm/5pm observations.")
        return 1

    print(f"\nMonth-ends with full 1pm/3pm + {owin} prices: {len(res)}")

    sell = res[res["leg1_bps"] < 0]
    rally = res[res["leg1_bps"] > 0]
    flat = res[res["leg1_bps"] == 0]

    print("\n" + "=" * 62)
    print(f"{owin}pm TY move CONDITIONED on the 1pm->3pm move  (bps; +=rally)")
    print("=" * 62)
    print(f"  1-3 sell-offs: {len(sell)}   1-3 rallies: {len(rally)}   1-3 flat: {len(flat)}")

    describe(f"GIVEN 1-3 SOLD OFF  -> subsequent {owin} move", sell["leg2_bps"], sell["date"])
    if len(sell):
        cont = (sell["leg2_bps"] < 0).mean() * 100
        print(f"  -> continued lower ({owin} also down): {cont:.0f}%   reversed up: {100-cont-(sell['leg2_bps']==0).mean()*100:.0f}%")

    describe(f"GIVEN 1-3 RALLIED   -> subsequent {owin} move", rally["leg2_bps"], rally["date"])
    if len(rally):
        cont = (rally["leg2_bps"] > 0).mean() * 100
        print(f"  -> continued higher ({owin} also up): {cont:.0f}%   reversed down: {100-cont-(rally['leg2_bps']==0).mean()*100:.0f}%")

    print(f"\n--- Full 1pm->{args.out_to} move by 1-3 direction ---")
    describe(f"GIVEN 1-3 SOLD OFF  -> full 1pm-{args.out_to} move", sell["full_bps"], sell["date"])
    describe(f"GIVEN 1-3 RALLIED   -> full 1pm-{args.out_to} move", rally["full_bps"], rally["date"])

    if args.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            out_dir = ROOT / "workspace" / "analysis"
            out_dir.mkdir(parents=True, exist_ok=True)

            fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True, sharex=True)
            for ax, grp, title, color in (
                (axes[0], sell, "GIVEN 1-3 SOLD OFF", "#d73027"),
                (axes[1], rally, "GIVEN 1-3 RALLIED", "#1a9850"),
            ):
                data = grp["leg2_bps"].astype(float)
                ax.hist(data, bins=16, color=color, edgecolor="white")
                if len(data):
                    ax.axvline(data.mean(), color="black", ls="--", lw=1.5,
                               label=f"{owin} mean {data.mean():+.2f} (N={len(data)})")
                    ax.legend()
                ax.axvline(0, color="grey", lw=0.8)
                ax.set_title(f"{title}\n-> {owin}pm move")
                ax.set_xlabel(f"{owin}pm move (bps)")
            axes[0].set_ylabel("count")
            fig.suptitle(f"Month-end TY: {owin}pm move conditioned on 1-3pm direction", y=1.02)
            fig.tight_layout()
            p = out_dir / f"monthend_ty_{otag}_given_1to3.png"
            fig.savefig(p, dpi=130, bbox_inches="tight")
            plt.close(fig)
            print(f"\nSaved conditional plot -> {p}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n(plot skipped: {exc})")

    print("\nNote: 1.0 price point = 8 bps; double for 10Y-DV01 (16 bps/pt) basis.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
