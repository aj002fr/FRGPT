#!/usr/bin/env python3
"""Offline V3 contract-roll study.

Calendar roll windows (last Globex close of Feb/May/Aug/Nov, +/- N sessions), the
outgoing/incoming volume split inside them, overlap with V3 day tags, and separate
day-level OLS fits for the outgoing and incoming contract during the roll.

Reads the causal-backfill trade tape, day points, and frozen events read-only.
Writes only under workspace/analysis/aggression_v3_roll/. Does not import the V3 app,
write tag_beta.json, the backfill tree, any *_live store, or restart 5053.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.core import EASTERN, LIVE_STORE_DIR, _aggressor_signs, load_dv01_table
from scripts.aggression_dashboard_v3.tag_beta import (
    BPS_PER_POINT,
    TENOR_TAGS,
    TIER1_FAMILIES,
    TAGS,
    _TENOR_RE,
    _session_day,
    day_tag,
    is_month_end,
)

logger = logging.getLogger("v3.roll_research")

OUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v3_roll"
BACKFILL_DIR = LIVE_STORE_DIR / "historical_backfill"
ROOTS = {"ZT": "TU", "ZF": "FV", "ZN": "TY", "ZB": "US"}
PRICE_ROOT = "ZN"
MONTH_CODES = "FGHJKMNQUVXZ"
ROLL_MONTHS = (2, 5, 8, 11)
WINDOW_CANDIDATES = ((2, 2), (3, 3), (5, 5), (4, 0), (5, 0), (6, 0), (4, 1), (5, 1), (6, 1), (7, 1))
N_SCAN = 8
SPLIT_LO, SPLIT_HI = 0.2, 0.8
COVERAGE_TARGET = 0.95
EVAL_FROM = date(2012, 1, 1)
MIN_TRAIN = 20
BOOT_REPS = 2000
GENERATIONS = ("out", "in", "front")
VIEWS = ("ty", "curve")
TRADE_COLUMNS = ["ts", "price", "size", "side", "raw_symbol"]
OUTRIGHT_RE = re.compile(r"^(ZN|ZB|ZT|ZF)[FGHJKMNQUVXZ]\d{1,2}$")


# --- calendar ------------------------------------------------------------------------------------


def output_root(root: Optional[Path] = None) -> Path:
    path = Path(root) if root is not None else OUT_DIR
    live = LIVE_STORE_DIR.resolve()
    resolved = path.resolve()
    if resolved == live or live in resolved.parents:
        raise RuntimeError("Refusing to write roll research under the live V3 store.")
    path.mkdir(parents=True, exist_ok=True)
    return path


def globex_close_dates(first: date, last: date) -> List[date]:
    """Weekday Globex close dates; CME full-day closures are absent because they print nothing."""
    return [stamp.date() for stamp in pd.bdate_range(first, last)]


def roll_contracts(root: str, year: int, month: int) -> Tuple[str, str]:
    """(outgoing, incoming) outright for the roll anchored in `month` of `year`."""
    if month not in ROLL_MONTHS:
        raise ValueError(f"Roll month must be one of {ROLL_MONTHS}, got {month}.")
    out_month, out_year = month + 1, year
    in_month, in_year = out_month + 3, out_year
    if in_month > 12:
        in_month, in_year = in_month - 12, in_year + 1
    return (
        f"{root}{MONTH_CODES[out_month - 1]}{out_year % 10}",
        f"{root}{MONTH_CODES[in_month - 1]}{in_year % 10}",
    )


def build_windows(sessions: Sequence[date], n: int, after: Optional[int] = None) -> pd.DataFrame:
    """One row per (roll window, session): anchor is the last session of Feb/May/Aug/Nov.

    The window is `n` sessions before the anchor through `after` sessions after it (default `n`).
    """
    after = n if after is None else after
    ordered = sorted(set(sessions))
    index = {day: i for i, day in enumerate(ordered)}
    anchors: Dict[Tuple[int, int], date] = {}
    for day in ordered:
        if day.month in ROLL_MONTHS:
            key = (day.year, day.month)
            anchors[key] = max(anchors.get(key, day), day)
    rows = []
    for (year, month), anchor in sorted(anchors.items()):
        i = index[anchor]
        if i - n < 0 or i + after >= len(ordered):
            continue
        last_of_month = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
        if (last_of_month.date() - anchor).days > 4:
            continue  # month not fully covered by the session list
        for offset in range(-n, after + 1):
            rows.append({
                "window_id": f"{year}-{month:02d}",
                "anchor": anchor,
                "session": ordered[i + offset],
                "offset": offset,
                "roll_year": year,
                "roll_month": month,
            })
    return pd.DataFrame(rows, columns=["window_id", "anchor", "session", "offset", "roll_year", "roll_month"])


def reindex_offsets(windows: pd.DataFrame) -> pd.DataFrame:
    """Recount offsets from the anchor after sessions without prints were removed."""
    parts = []
    for _, group in windows.groupby("window_id"):
        group = group.sort_values("session").reset_index(drop=True)
        hits = group.index[group["session"] == group["anchor"].iloc[0]]
        if not len(hits):
            continue
        group["offset"] = group.index - hits[0]
        parts.append(group)
    return pd.concat(parts, ignore_index=True) if parts else windows.iloc[0:0]


# --- tape ----------------------------------------------------------------------------------------


def outright_mask(symbols: pd.Series) -> pd.Series:
    return symbols.astype(str).str.match(OUTRIGHT_RE)


def aggregate_contract_days(frame: pd.DataFrame, keep_days: Optional[Set[date]] = None) -> pd.DataFrame:
    """Per (session close date, outright): volume, net aggressor contracts, first/last print."""
    cols = ["day", "raw_symbol", "volume", "net", "prints", "first_ts", "first_px", "last_ts", "last_px"]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=cols)
    work = frame.loc[outright_mask(frame["raw_symbol"])].copy()
    if work.empty:
        return pd.DataFrame(columns=cols)
    work["ts"] = pd.to_datetime(work["ts"], utc=True)
    work.sort_values("ts", inplace=True, kind="stable")
    day = _session_day(work["ts"].dt.tz_convert(EASTERN))
    work = work.loc[day.notna()].copy()
    work["day"] = day.loc[work.index].dt.date
    if keep_days is not None:
        work = work.loc[work["day"].isin(keep_days)]
    if work.empty:
        return pd.DataFrame(columns=cols)
    size = work["size"].astype(float)
    work["signed"] = _aggressor_signs(work["side"].astype(str)).to_numpy() * size
    grouped = work.groupby(["day", "raw_symbol"], sort=False)
    out = grouped.agg(
        volume=("size", "sum"),
        net=("signed", "sum"),
        prints=("size", "size"),
        first_ts=("ts", "first"),
        first_px=("price", "first"),
        last_ts=("ts", "last"),
        last_px=("price", "last"),
    ).reset_index()
    return out[cols]


def merge_contract_days(parts: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """Combine partial aggregates of a session split across two monthly files."""
    frames = [part for part in parts if part is not None and not part.empty]
    if not frames:
        return pd.DataFrame(columns=["day", "raw_symbol", "volume", "net", "prints",
                                     "first_ts", "first_px", "last_ts", "last_px"])
    data = pd.concat(frames, ignore_index=True)
    keys = ["day", "raw_symbol"]
    sums = data.groupby(keys)[["volume", "net", "prints"]].sum()
    first = data.sort_values("first_ts").groupby(keys)[["first_ts", "first_px"]].first()
    last = data.sort_values("last_ts").groupby(keys)[["last_ts", "last_px"]].last()
    return sums.join(first).join(last).reset_index()


def months_for_sessions(sessions: Iterable[date]) -> Set[date]:
    """UTC months whose files can hold prints of these sessions (the open is the prior evening)."""
    out = set()
    for day in sessions:
        for stamp in (day, day - timedelta(days=1)):
            out.add(date(stamp.year, stamp.month, 1))
    return out


def _aggregate_file(path: Path, keep: Set[date]) -> pd.DataFrame:
    return aggregate_contract_days(pd.read_parquet(path, columns=TRADE_COLUMNS), keep)


def load_contract_days(
    windows: pd.DataFrame,
    *,
    backfill: Path,
    cache: Path,
    rebuild: bool = False,
    workers: int = 6,
) -> pd.DataFrame:
    if cache.is_file() and not rebuild:
        frame = pd.read_parquet(cache)
        frame["day"] = pd.to_datetime(frame["day"]).dt.date
        needed = set(windows["session"])
        if needed.issubset(set(frame["day"])):
            return frame
        logger.info("Contract-day cache misses %s sessions; rebuilding", len(needed - set(frame["day"])))
    keep = set(windows["session"])
    months = sorted(months_for_sessions(keep))
    paths = [backfill / "trades" / f"{root}_FUT" / f"{month:%Y-%m}.parquet" for root in ROOTS for month in months]
    paths = [path for path in paths if path.is_file()]
    parts = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_aggregate_file, path, keep): path for path in paths}
        for done, future in enumerate(as_completed(futures), 1):
            parts.append(future.result())
            if done % 20 == 0 or done == len(paths):
                logger.info("Aggregated %s/%s monthly files", done, len(paths))
    merged = merge_contract_days(parts)
    merged["root"] = merged["raw_symbol"].str[:2]
    tmp = cache.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False)
    os.replace(tmp, cache)
    return merged


# --- roll sessions + volume share ----------------------------------------------------------------


def _symbol_variants(symbol: str, year: int) -> Tuple[str, str]:
    return symbol, f"{symbol[:3]}{year % 100:02d}"


def roll_session_table(windows: pd.DataFrame, contract_days: pd.DataFrame) -> pd.DataFrame:
    """Per (window, session, root): outgoing / incoming / front volumes, prices, net."""
    lookup = {(row.day, row.raw_symbol): row for row in contract_days.itertuples(index=False)}
    by_root_day: Dict[Tuple[str, date], List[Any]] = {}
    for row in contract_days.itertuples(index=False):
        by_root_day.setdefault((row.raw_symbol[:2], row.day), []).append(row)
    rows = []
    for win in windows.itertuples(index=False):
        for root in ROOTS:
            out_sym, in_sym = roll_contracts(root, win.roll_year, win.roll_month)
            in_year = win.roll_year + (1 if win.roll_month == 11 else 0)

            def pick(symbol: str, year: int):
                for variant in _symbol_variants(symbol, year):
                    hit = lookup.get((win.session, variant))
                    if hit is not None:
                        return hit
                return None

            out_row = pick(out_sym, win.roll_year)
            in_row = pick(in_sym, in_year)
            day_rows = by_root_day.get((root, win.session), [])
            total = float(sum(r.volume for r in day_rows))
            front = max(day_rows, key=lambda r: r.volume) if day_rows else None
            vol_out = float(out_row.volume) if out_row is not None else 0.0
            vol_in = float(in_row.volume) if in_row is not None else 0.0
            pair = vol_out + vol_in
            record = {
                "window_id": win.window_id,
                "anchor": win.anchor,
                "session": win.session,
                "offset": win.offset,
                "root": root,
                "symbol": ROOTS[root],
                "out_contract": out_sym,
                "in_contract": in_sym,
                "front_contract": front.raw_symbol if front is not None else None,
                "vol_out": vol_out,
                "vol_in": vol_in,
                "vol_total": total,
                "share_in": vol_in / pair if pair > 0 else np.nan,
                "share_in_all": vol_in / total if total > 0 else np.nan,
                "pair_share_of_total": pair / total if total > 0 else np.nan,
            }
            for gen, hit in (("out", out_row), ("in", in_row), ("front", front)):
                record[f"net_{gen}"] = float(hit.net) if hit is not None else np.nan
                record[f"first_px_{gen}"] = float(hit.first_px) if hit is not None else np.nan
                record[f"last_px_{gen}"] = float(hit.last_px) if hit is not None else np.nan
                record[f"vol_{gen}_prints"] = int(hit.prints) if hit is not None else 0
            rows.append(record)
    return pd.DataFrame(rows)


def crossover_table(sessions: pd.DataFrame) -> pd.DataFrame:
    """First offset where the incoming contract carries more than half of the pair, per window/root."""
    rows = []
    for (window_id, root), group in sessions.groupby(["window_id", "root"]):
        group = group.sort_values("offset")
        crossed = group.loc[group["share_in"] > 0.5, "offset"]
        split = group.loc[group["share_in"].between(SPLIT_LO, SPLIT_HI), "offset"]
        rows.append({
            "window_id": window_id,
            "root": root,
            "crossover_offset": int(crossed.iloc[0]) if len(crossed) else None,
            "split_sessions": int(len(split)),
            "split_first": int(split.min()) if len(split) else None,
            "split_last": int(split.max()) if len(split) else None,
        })
    return pd.DataFrame(rows)


def window_stats(sessions: pd.DataFrame, specs: Sequence[Tuple[int, int]] = WINDOW_CANDIDATES) -> pd.DataFrame:
    """Per (before, after) window: coverage of split sessions and purity (split share of window sessions)."""
    is_split = sessions["share_in"].between(SPLIT_LO, SPLIT_HI)
    rows = []
    for before, after in specs:
        inside = sessions["offset"].between(-before, after)
        rows.append({
            "before": before,
            "after": after,
            "sessions": before + after + 1,
            "coverage": float((is_split & inside).sum() / is_split.sum()) if is_split.any() else np.nan,
            "purity": float((is_split & inside).sum() / inside.sum()) if inside.any() else np.nan,
        })
    return pd.DataFrame(rows)


def recommend_window(stats: pd.DataFrame, target: float = COVERAGE_TARGET) -> Tuple[int, int]:
    """Shortest window reaching the coverage target (ties: higher purity); else the best-covering one."""
    ok = stats.loc[stats["coverage"] >= target]
    pick = (ok.sort_values(["sessions", "purity"], ascending=[True, False]) if not ok.empty
            else stats.sort_values(["coverage", "purity"], ascending=False))
    row = pick.iloc[0]
    return int(row["before"]), int(row["after"])


# --- tags ----------------------------------------------------------------------------------------


def day_flags(day: date, events_by_date: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Every raw flag on a day, not only the tag that wins day_tag's priority."""
    hits = events_by_date.get(day.isoformat(), [])
    tenors = set()
    for event in hits:
        if event.get("family") == "auction":
            match = _TENOR_RE.search(str(event.get("event_name") or ""))
            tenors.add(f"{match.group(1)}Y" if match else "?")
    tier1 = sorted({str(event.get("family")) for event in hits if event.get("family") in TIER1_FAMILIES})
    tag, _ = day_tag(day, events_by_date)
    return {
        "tag": tag,
        "auction": bool(tenors),
        "auction_tenors": ",".join(sorted(tenors)),
        "month_end": bool(is_month_end(day)),
        "tier1": bool(tier1),
        "tier1_families": ",".join(tier1),
    }


def load_events(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: Dict[str, List[Dict[str, Any]]] = {}
    for event in payload.get("events") or []:
        if event.get("date"):
            out.setdefault(str(event["date"])[:10], []).append(event)
    return out


def tag_overlap(roll_days: pd.DataFrame, events_by_date: Dict[str, List[Dict[str, Any]]]) -> pd.DataFrame:
    rows = []
    for win in roll_days.drop_duplicates(["window_id", "session"]).itertuples(index=False):
        rows.append({"window_id": win.window_id, "session": win.session, "offset": win.offset,
                     **day_flags(win.session, events_by_date)})
    return pd.DataFrame(rows)


# --- day points ----------------------------------------------------------------------------------


def roll_day_points(sessions: pd.DataFrame, dv01: Dict[str, float]) -> pd.DataFrame:
    """One point per (window, session, generation, view): y = TY-contract day bps, x = net DV01."""
    rows = []
    for (window_id, session), group in sessions.groupby(["window_id", "session"]):
        by_root = {row.root: row for row in group.itertuples(index=False)}
        price = by_root.get(PRICE_ROOT)
        if price is None:
            continue
        for gen in GENERATIONS:
            first = getattr(price, f"first_px_{gen}")
            last = getattr(price, f"last_px_{gen}")
            if not np.isfinite(first) or not np.isfinite(last):
                continue
            nets = {root: getattr(row, f"net_{gen}") for root, row in by_root.items()}
            ty_net = nets.get(PRICE_ROOT, np.nan)
            curve_parts = [nets[r] * dv01[ROOTS[r]] for r in ROOTS if r in nets and np.isfinite(nets[r])]
            x_by_view = {
                "ty": ty_net * dv01["TY"] if np.isfinite(ty_net) else np.nan,
                "curve": float(sum(curve_parts)) if len(curve_parts) == len(ROOTS) else np.nan,
            }
            for view, x in x_by_view.items():
                if not np.isfinite(x):
                    continue
                rows.append({
                    "window_id": window_id,
                    "date": session.isoformat(),
                    "offset": int(price.offset),
                    "generation": gen,
                    "view": view,
                    "contract": getattr(price, f"{gen}_contract"),
                    "x_net": float(x),
                    "y_bps": float(last - first) * BPS_PER_POINT,
                    "share_in": float(price.share_in) if np.isfinite(price.share_in) else np.nan,
                })
    return pd.DataFrame(rows)


def load_front_points(backfill: Path, view: str) -> pd.DataFrame:
    path = backfill / "day_points" / f"{view}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    frame = pd.DataFrame(rows)
    frame["view"] = view
    return frame


# --- fits ----------------------------------------------------------------------------------------


def ols(x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    n = len(x)
    if n < 2:
        return {"n": n, "slope_bps_per_mm": None, "intercept_bps": None, "resid_std_bps": None, "r2": None}
    var_x = float(((x - x.mean()) ** 2).sum())
    if var_x == 0:
        return {"n": n, "slope_bps_per_mm": None, "intercept_bps": float(y.mean()),
                "resid_std_bps": float(y.std(ddof=1)), "r2": None}
    slope = float(((x - x.mean()) * (y - y.mean())).sum() / var_x)
    intercept = float(y.mean() - slope * x.mean())
    resid = y - (intercept + slope * x)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {
        "n": n,
        "slope_bps_per_mm": slope * 1e6,
        "intercept_bps": intercept,
        "resid_std_bps": float(resid.std(ddof=2)) if n > 2 else None,
        "r2": 1 - float((resid ** 2).sum()) / ss_tot if ss_tot > 0 else None,
    }


def walk_forward_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    group: Optional[str] = None,
    min_train: int = MIN_TRAIN,
) -> np.ndarray:
    """Out-of-sample prediction for each test row from train rows dated strictly before it.

    `group` restricts training to rows sharing that column's value with the test row.
    """
    preds = np.full(len(test), np.nan)
    if test.empty or train.empty:
        return preds
    keys = [None] if group is None else list(pd.unique(test[group]))
    for key in keys:
        tr = train if key is None else train.loc[train[group] == key]
        mask = np.ones(len(test), dtype=bool) if key is None else (test[group] == key).to_numpy()
        if tr.empty or not mask.any():
            continue
        tr = tr.sort_values("date")
        dates = tr["date"].to_numpy(dtype=str)
        x = tr["x_net"].to_numpy(dtype=float)
        y = tr["y_bps"].to_numpy(dtype=float)
        n_c = np.arange(1, len(x) + 1)
        sx, sy = np.cumsum(x), np.cumsum(y)
        sxx, sxy = np.cumsum(x * x), np.cumsum(x * y)
        test_dates = test.loc[mask, "date"].to_numpy(dtype=str)
        test_x = test.loc[mask, "x_net"].to_numpy(dtype=float)
        count = np.searchsorted(dates, test_dates, side="left")
        out = np.full(len(test_dates), np.nan)
        ok = count >= min_train
        k = count[ok] - 1
        n = n_c[k].astype(float)
        var = sxx[k] - sx[k] ** 2 / n
        cov = sxy[k] - sx[k] * sy[k] / n
        slope = np.where(var > 0, cov / np.where(var > 0, var, 1.0), np.nan)
        intercept = (sy[k] - slope * sx[k]) / n
        out[ok] = intercept + slope * test_x[ok]
        preds[mask] = out
    return preds


def score(y: np.ndarray, pred: np.ndarray) -> Dict[str, Any]:
    ok = np.isfinite(pred) & np.isfinite(y)
    if ok.sum() == 0:
        return {"n_oos": 0, "rmse_bps": np.nan, "mae_bps": np.nan, "oos_r2_vs_zero": np.nan}
    err = y[ok] - pred[ok]
    denom = float((y[ok] ** 2).sum())
    return {
        "n_oos": int(ok.sum()),
        "rmse_bps": float(np.sqrt((err ** 2).mean())),
        "mae_bps": float(np.abs(err).mean()),
        "oos_r2_vs_zero": 1 - float((err ** 2).sum()) / denom if denom > 0 else np.nan,
    }


def clustered_slope_ci(points: pd.DataFrame, reps: int = BOOT_REPS, seed: int = 20261020) -> Tuple[Optional[float], Optional[float]]:
    """95% percentile CI of the pooled slope, resampling whole roll windows."""
    groups = [g for _, g in points.groupby("window_id")]
    if len(groups) < 5:
        return None, None
    rng = np.random.default_rng(seed)
    xs = [g["x_net"].to_numpy(dtype=float) for g in groups]
    ys = [g["y_bps"].to_numpy(dtype=float) for g in groups]
    slopes = []
    for _ in range(reps):
        pick = rng.integers(0, len(groups), len(groups))
        x = np.concatenate([xs[i] for i in pick])
        y = np.concatenate([ys[i] for i in pick])
        var = float(((x - x.mean()) ** 2).sum())
        if var > 0:
            slopes.append(float(((x - x.mean()) * (y - y.mean())).sum() / var) * 1e6)
    if not slopes:
        return None, None
    lo, hi = np.percentile(slopes, [2.5, 97.5])
    return float(lo), float(hi)


def regular_fit_comparison(front: pd.DataFrame, roll_dates: Set[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Per tag and view: full-sample fit with vs without roll days, plus walk-forward OOS on non-roll days."""
    fits, oos = [], []
    for view, frame in front.groupby("view"):
        frame = frame.sort_values("date").reset_index(drop=True)
        frame["is_roll"] = frame["date"].isin(roll_dates)
        clean = frame.loc[~frame["is_roll"]]
        test = frame.loc[(~frame["is_roll"]) & (frame["date"] >= EVAL_FROM.isoformat())].reset_index(drop=True)
        pred_with = walk_forward_predict(frame, test, group="tag")
        pred_without = walk_forward_predict(clean, test, group="tag")
        for tag in TAGS:
            all_rows = frame.loc[frame["tag"] == tag]
            clean_rows = clean.loc[clean["tag"] == tag]
            with_fit = ols(all_rows["x_net"].to_numpy(float), all_rows["y_bps"].to_numpy(float))
            without_fit = ols(clean_rows["x_net"].to_numpy(float), clean_rows["y_bps"].to_numpy(float))
            fits.append({
                "view": view, "tag": tag,
                "n_with": with_fit["n"], "n_without": without_fit["n"],
                "roll_days_removed": int(all_rows["is_roll"].sum()),
                "slope_with": with_fit["slope_bps_per_mm"], "slope_without": without_fit["slope_bps_per_mm"],
                "resid_std_with": with_fit["resid_std_bps"], "resid_std_without": without_fit["resid_std_bps"],
                "r2_with": with_fit["r2"], "r2_without": without_fit["r2"],
            })
            mask = (test["tag"] == tag).to_numpy()
            y = test.loc[mask, "y_bps"].to_numpy(float)
            s_with, s_without = score(y, pred_with[mask]), score(y, pred_without[mask])
            oos.append({"view": view, "tag": tag,
                        **{f"{k}_with": v for k, v in s_with.items()},
                        **{f"{k}_without": v for k, v in s_without.items()}})
    return pd.DataFrame(fits), pd.DataFrame(oos)


def roll_fit_comparison(
    roll_points: pd.DataFrame,
    front: pd.DataFrame,
    crossovers: pd.DataFrame,
    events_by_date: Dict[str, List[Dict[str, Any]]],
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Pooled roll fits per generation/view, their OOS skill against the current tag fit, and yearly slopes."""
    cross = crossovers.loc[crossovers["root"] == PRICE_ROOT].set_index("window_id")["crossover_offset"]
    points = roll_points.copy()
    points["crossover"] = points["window_id"].map(cross)
    points["phase"] = np.where(points["offset"] < points["crossover"].fillna(0), "pre", "post")
    points["tag"] = [day_tag(date.fromisoformat(d), events_by_date)[0] for d in points["date"]]
    fits, oos, yearly = [], [], []
    for (gen, view), group in points.groupby(["generation", "view"]):
        group = group.sort_values("date").reset_index(drop=True)
        full = ols(group["x_net"].to_numpy(float), group["y_bps"].to_numpy(float))
        lo, hi = clustered_slope_ci(group)
        fits.append({"model": f"ROLL_{gen.upper()}", "generation": gen, "view": view,
                     "windows": int(group["window_id"].nunique()), **full,
                     "slope_ci_lo": lo, "slope_ci_hi": hi})
        for phase, sub in group.groupby("phase"):
            fit = ols(sub["x_net"].to_numpy(float), sub["y_bps"].to_numpy(float))
            fits.append({"model": f"ROLL_{gen.upper()}_{phase.upper()}", "generation": gen, "view": view,
                         "windows": int(sub["window_id"].nunique()), **fit,
                         "slope_ci_lo": None, "slope_ci_hi": None})
        for year, sub in group.groupby(group["date"].str[:4]):
            fit = ols(sub["x_net"].to_numpy(float), sub["y_bps"].to_numpy(float))
            yearly.append({"generation": gen, "view": view, "year": int(year), **fit})
        test = group.loc[group["date"] >= EVAL_FROM.isoformat()].reset_index(drop=True)
        y = test["y_bps"].to_numpy(float)
        tag_train = front.loc[front["view"] == view]
        pred_tag = walk_forward_predict(tag_train, test, group="tag")
        pred_roll = walk_forward_predict(group, test)
        pred_phase = walk_forward_predict(group, test, group="phase")
        for name, pred in (("current_tag_fit", pred_tag), ("pooled_roll_fit", pred_roll),
                           ("phase_split_roll_fit", pred_phase), ("zero", np.zeros(len(test)))):
            oos.append({"generation": gen, "view": view, "predictor": name, **score(y, pred)})
    return pd.DataFrame(fits), pd.DataFrame(oos), pd.DataFrame(yearly)


# --- readout -------------------------------------------------------------------------------------


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _md_table(frame: pd.DataFrame, digits: int = 2) -> str:
    if frame.empty:
        return "_none_\n"
    cols = list(frame.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in frame.itertuples(index=False):
        lines.append("| " + " | ".join(_fmt(v, digits) for v in row) + " |")
    return "\n".join(lines) + "\n"


def write_readout(out: Path, summary: Dict[str, Any], tables: Dict[str, pd.DataFrame]) -> None:
    s = summary
    lines = [
        "# V3 contract-roll study",
        "",
        "Offline only. Source: causal-backfill trade tape (all outrights) and front-month day points; "
        "frozen backfill events. Nothing here changes live V3.",
        "",
        f"- Roll windows: anchored on the last Globex close of Feb/May/Aug/Nov. "
        f"{s['windows']} windows from {s['first_window']} to {s['last_window']}.",
        f"- Split session: incoming share of the outgoing+incoming pair between {SPLIT_LO:.0%} and {SPLIT_HI:.0%}. "
        f"Offsets scanned: +/-{N_SCAN}.",
        f"- Recommended window: **anchor-{s['window']['before']} .. anchor+{s['window']['after']}** "
        f"({s['window']['sessions']} sessions), the shortest candidate covering >= {COVERAGE_TARGET:.0%} "
        "of split sessions.",
        f"- Roots crossing 50% on the same offset as ZN: {_fmt(s['same_crossover_share'] * 100, 1)}% of window-roots.",
        "",
        "## Window candidates (coverage = split sessions inside / all split sessions; "
        "purity = split sessions inside / sessions inside)",
        "",
        _md_table(tables["window_candidates"], 3),
        "## Incoming share by offset (median across windows)",
        "",
        _md_table(tables["share_profile"], 2),
        "## Crossover offset (first session the incoming contract has >50%)",
        "",
        _md_table(tables["crossover_dist"], 0),
        "## Tag overlap (recommended window)",
        "",
        f"{s['overlap']['roll_sessions']} roll sessions. Winning day tag:",
        "",
        _md_table(tables["overlap_tags"], 2),
        "Raw flags by offset (share of sessions):",
        "",
        _md_table(tables["overlap_flags"], 2),
        "## Regular tag regression with vs without roll days (front-month day points)",
        "",
        "Full sample, slope in bps per 1mm net DV01:",
        "",
        _md_table(tables["regular_fits_view"], 3),
        f"Walk-forward OOS on non-roll days from {EVAL_FROM}, min {MIN_TRAIN} same-tag training days:",
        "",
        _md_table(tables["regular_oos_view"], 3),
        "## Roll-period regressions (TY-contract bps on net DV01)",
        "",
        "Pooled over roll sessions; CI is a 95% bootstrap over whole windows.",
        "",
        _md_table(tables["roll_fits_view"], 3),
        f"Walk-forward OOS on roll sessions from {EVAL_FROM} (RMSE bps, lower is better):",
        "",
        _md_table(tables["roll_oos_view"], 3),
        "Yearly pooled slope, TY view (bps per 1mm net DV01):",
        "",
        _md_table(tables["yearly"], 3),
        "## Recommendation",
        "",
    ]
    lines += [f"- {item}" for item in s["recommendations"]]
    lines += ["", "Files: roll_sessions.csv, volume_share.csv, crossovers.csv, tag_overlap.csv, "
              "day_points_roll.csv, fits.csv, walkforward.csv, yearly_slopes.csv, summary.json, phase2_design.md.", ""]
    (out / "readout.md").write_text("\n".join(lines), encoding="utf-8")


def recommendations(summary: Dict[str, Any], roll_oos: pd.DataFrame, regular_oos: pd.DataFrame,
                    overlap_tags: pd.DataFrame) -> List[str]:
    w = summary["window"]
    recs = [f"Use the calendar window anchor-{w['before']}..anchor+{w['after']} ({w['sessions']} sessions); "
            "the split sits before the anchor, so symmetric windows mostly add sessions that are already rolled."]
    non_regular = overlap_tags.loc[overlap_tags["tag"] != "REGULAR", "share"].sum()
    recs.append(
        f"{non_regular:.0%} of roll sessions carry a non-REGULAR tag (mostly month-end/auctions). "
        "Make ROLL the beta tag on those days (override) and keep the original tag as a displayed flag, "
        "so removing roll days does not need a ROLL x tag grid that would have too few days."
    )
    for gen in ("out", "in"):
        for view in VIEWS:
            sub = roll_oos.loc[(roll_oos["generation"] == gen) & (roll_oos["view"] == view)].set_index("predictor")
            if sub.empty or "pooled_roll_fit" not in sub.index:
                continue
            cur, pooled = sub.loc["current_tag_fit", "rmse_bps"], sub.loc["pooled_roll_fit", "rmse_bps"]
            if not (np.isfinite(cur) and np.isfinite(pooled)):
                continue
            verdict = "beats" if pooled < cur else "does not beat"
            recs.append(f"{gen.upper()} contract, {view} view: pooled roll fit {verdict} the current tag fit "
                        f"OOS ({pooled:.3f} vs {cur:.3f} bps RMSE).")
    reg = regular_oos.dropna(subset=["rmse_bps_with", "rmse_bps_without"])
    if not reg.empty:
        better = int((reg["rmse_bps_without"] < reg["rmse_bps_with"]).sum())
        recs.append(f"Removing roll days from the regular tag fits lowers OOS RMSE in {better}/{len(reg)} tag-views.")
    recs.append("Treat these as display/beta-reference evidence only, not an after-cost or directional signal.")
    return recs


PHASE2 = """# Phase 2 design: live V3 roll pair (not implemented)

Status: design only. Needs the user's review of readout.md and explicit approval to edit live V3
(port 5053, workspace/analysis/aggression_v3_live/). No live file was changed by this study.

1. Roll calendar. Add `roll_window(day, n)` (last Globex close of Feb/May/Aug/Nov +/- N, N from
   summary.json) and `roll_contracts(root, year, month)` to a small pure module, reused by tag_beta,
   core, and the causal backfill.
2. tag_beta.py. ROLL overrides the day tag for the beta (original tag kept as `tag_detail`).
   Regular buckets drop roll days. Add ROLL_OUT and ROLL_IN fits whose day points come from the
   print tape (backfill trades + trades_v3_* cache, which keep every outright); V1 flow_history has
   no contract id. Version bump of TAG_BETA_VERSION so old tables rebuild.
3. core.py. Inside a roll window select the outgoing/incoming pair per root instead of the single
   highest-volume outright, and build one print frame per contract. Outside the window nothing changes.
4. Live feed. AccessAPI publishes only `.v.0`. Options: (a) Databento Live `ZN.v.1`-style subscription
   opened only inside roll windows, reusing DatabentoLiveBackup plumbing; (b) ask for `.v.1` on AccessAPI.
   Decision needed before build.
5. Panels/banner. During a roll, p1 and p4 add the second contract's price and implied path under its
   own ROLL beta; the banner shows both contracts' states with labels. Otherwise unchanged.
6. Causal backfill. Bump LABEL_VERSION; emit roll-pair states; extend tests/test_v3_*.py.
7. Live V3 picks the change up only on a manual restart.
"""


# --- main ----------------------------------------------------------------------------------------


def run(out_dir: Optional[Path] = None, backfill: Path = BACKFILL_DIR, rebuild_cache: bool = False,
        last: Optional[date] = None) -> Dict[str, Any]:
    out = output_root(out_dir)
    events_by_date = load_events(backfill / "events" / "events_tagged_backfill.json")
    front = pd.concat([load_front_points(backfill, view) for view in VIEWS], ignore_index=True)
    first_day = date.fromisoformat(front["date"].min())
    last_day = last or date.fromisoformat(front["date"].max())
    sessions = globex_close_dates(first_day, last_day)
    scan = build_windows(sessions, N_SCAN)
    contract_days = load_contract_days(scan, backfill=backfill, cache=out / "contract_days.parquet",
                                       rebuild=rebuild_cache)
    traded = set(contract_days["day"])
    scan = reindex_offsets(scan.loc[scan["session"].isin(traded)])  # drop CME closures (no prints)
    table = roll_session_table(scan, contract_days)
    table.to_csv(out / "volume_share.csv", index=False)
    crossovers = crossover_table(table)
    crossovers.to_csv(out / "crossovers.csv", index=False)
    stats = window_stats(table)
    stats.to_csv(out / "window_candidates.csv", index=False)
    before, after = recommend_window(stats)

    windows = build_windows([d for d in sessions if d in traded], before, after)
    roll_rows = table.merge(windows[["window_id", "session"]], on=["window_id", "session"])
    roll_rows.to_csv(out / "roll_sessions.csv", index=False)
    roll_dates = {d.isoformat() for d in windows["session"]}

    overlap = tag_overlap(windows, events_by_date)
    overlap.to_csv(out / "tag_overlap.csv", index=False)
    dv01 = load_dv01_table()
    points = roll_day_points(roll_rows, dv01)
    points.to_csv(out / "day_points_roll.csv", index=False)
    reg_fits, reg_oos = regular_fit_comparison(front, roll_dates)
    roll_fits, roll_oos, yearly = roll_fit_comparison(points, front, crossovers, events_by_date)
    fits_all = pd.concat([roll_fits.assign(kind="roll"), reg_fits.assign(kind="regular")], ignore_index=True)
    fits_all.to_csv(out / "fits.csv", index=False)
    pd.concat([roll_oos.assign(kind="roll"), reg_oos.assign(kind="regular")],
              ignore_index=True).to_csv(out / "walkforward.csv", index=False)
    yearly.to_csv(out / "yearly_slopes.csv", index=False)

    zn_cross = crossovers.loc[crossovers["root"] == PRICE_ROOT].set_index("window_id")["crossover_offset"]
    others = crossovers.loc[crossovers["root"] != PRICE_ROOT].copy()
    others["zn"] = others["window_id"].map(zn_cross)
    same = float((others["crossover_offset"] == others["zn"]).mean()) if len(others) else float("nan")

    share_profile = (table.groupby(["root", "offset"])["share_in"].median().unstack("root")
                     .reset_index()[["offset", *ROOTS]])
    crossover_dist = (crossovers.groupby(["root", "crossover_offset"]).size().unstack("root", fill_value=0)
                      .reset_index())
    overlap_tags = overlap["tag"].value_counts().rename_axis("tag").reset_index(name="sessions")
    overlap_tags["share"] = overlap_tags["sessions"] / overlap_tags["sessions"].sum()
    overlap_flags = overlap.groupby("offset")[["auction", "month_end", "tier1"]].mean().reset_index()
    tables = {
        "share_profile": share_profile,
        "crossover_dist": crossover_dist,
        "overlap_tags": overlap_tags,
        "overlap_flags": overlap_flags,
        "regular_fits_view": reg_fits[["view", "tag", "n_with", "n_without", "slope_with", "slope_without",
                                       "resid_std_with", "resid_std_without"]],
        "regular_oos_view": reg_oos[["view", "tag", "n_oos_with", "rmse_bps_with", "rmse_bps_without"]],
        "roll_fits_view": roll_fits[["model", "view", "windows", "n", "slope_bps_per_mm", "slope_ci_lo",
                                     "slope_ci_hi", "resid_std_bps", "r2"]],
        "roll_oos_view": roll_oos.pivot_table(index=["generation", "view"], columns="predictor",
                                              values="rmse_bps").reset_index(),
        "window_candidates": stats,
        "yearly": (yearly.loc[yearly["view"] == "ty"]
                   .pivot_table(index="year", columns="generation", values="slope_bps_per_mm").reset_index()),
    }
    summary: Dict[str, Any] = {
        "version": "v3_roll_research_v1",
        "windows": int(windows["window_id"].nunique()),
        "first_window": str(windows["window_id"].min()),
        "last_window": str(windows["window_id"].max()),
        "window": {"before": before, "after": after, "sessions": before + after + 1},
        "window_candidates": stats.to_dict(orient="records"),
        "same_crossover_share": same,
        "zn_crossover_offsets": {str(k): int(v) for k, v in zn_cross.value_counts().sort_index().items()},
        "overlap": {
            "roll_sessions": int(len(overlap)),
            "tags": {str(t): int(n) for t, n in zip(overlap_tags["tag"], overlap_tags["sessions"])},
            "month_end_share": float(overlap["month_end"].mean()),
            "auction_share": float(overlap["auction"].mean()),
            "tier1_share": float(overlap["tier1"].mean()),
        },
        "eval_from": EVAL_FROM.isoformat(),
        "min_train": MIN_TRAIN,
        "ship_gate": False,
    }
    summary["recommendations"] = recommendations(summary, roll_oos, reg_oos, overlap_tags)
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    write_readout(out, summary, tables)
    (out / "phase2_design.md").write_text(PHASE2, encoding="utf-8")
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--backfill", type=Path, default=BACKFILL_DIR)
    parser.add_argument("--rebuild-cache", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run(args.out, args.backfill, args.rebuild_cache)
    print(json.dumps({k: summary[k] for k in ("windows", "window", "overlap")}, indent=1,
                     default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
