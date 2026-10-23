#!/usr/bin/env python3
"""Offline V2.7 alert-clock study: wall time vs print/volume/tick/adaptive clocks.

Guardrail: does not import, write to, or restart V1/V1.2/V2/V2.1/V2.2/V2.3/V2.6/V2.7/V3.
Reads B1_W10 walk-forward residuals and the published TBBO feature matrix.
Writes only under workspace/analysis/aggression_v2_alert_clocks/.
Not a live V2.7 retune and not an after-cost ship gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

OOS_PATH = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_short_horizon_fv" / "oos_predictions.csv"
FEATURE_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_alert_clocks"

CONSTRUCTION = "B1_W10"
TICK_SIZE = 1.0 / 64.0
WATCH_Z = 1.5
DISLOCATION_Z = 2.0
RESET_Z = 0.5
MIN_HOLD_MINUTES = 5
LOCKED_HOLDOUT_FRAC = 0.20
ADVERSE_TICK_CAP = 3.0
SWEET_DURATION_MIN = 5.0
SWEET_DURATION_MAX = 15.0

WALL_MINUTES = (3, 5, 8, 10, 15, 20, 30)
PRINT_TARGETS = (25, 50, 100, 150, 250, 400, 800, 1500)
VOLUME_TARGETS = (100, 250, 500, 1000, 2000, 4000, 8000)
TICK_MINUTE_TARGETS = (3, 5, 8, 10, 15)
ADAPTIVE_SPECS = (
    ("tod_p25_fwd5_prints", 5, 0.25, 15),
    ("tod_median_fwd5_prints", 5, 0.50, 15),
    ("tod_p75_fwd5_prints", 5, 0.75, 15),
    ("tod_median_fwd10_prints", 10, 0.50, 20),
    ("tod_p75_fwd10_prints", 10, 0.75, 20),
)
HYBRID_SPECS = (
    ("first_wall10_prints150", "first", (("wall", 10.0), ("prints", 150.0))),
    ("first_wall15_prints400", "first", (("wall", 15.0), ("prints", 400.0))),
    ("first_wall10_vol1000", "first", (("wall", 10.0), ("volume", 1000.0))),
    ("both_wall5_prints50", "both", (("wall", 5.0), ("prints", 50.0))),
    ("both_wall10_prints150", "both", (("wall", 10.0), ("prints", 150.0))),
    ("first_wall15_ticks8", "first", (("wall", 15.0), ("ticks", 8.0))),
)


@dataclass(frozen=True)
class Clock:
    id: str
    family: str
    kind: str
    target: float | None = None
    cap_minutes: float | None = None
    extra: dict[str, Any] | None = None


def frozen_clocks() -> list[Clock]:
    clocks = [Clock(f"wall_{m}m", "wall", "wall", float(m)) for m in WALL_MINUTES]
    clocks.extend(Clock(f"prints_{n}", "prints", "prints", float(n)) for n in PRINT_TARGETS)
    clocks.extend(Clock(f"volume_{n}", "volume", "volume", float(n)) for n in VOLUME_TARGETS)
    clocks.extend(Clock(f"tickmins_{n}", "ticks", "ticks", float(n)) for n in TICK_MINUTE_TARGETS)
    for name, fwd, q, cap in ADAPTIVE_SPECS:
        clocks.append(Clock(name, "adaptive", "adaptive_prints", None, float(cap), {"forward_min": fwd, "quantile": q}))
    for name, mode, parts in HYBRID_SPECS:
        clocks.append(Clock(name, "hybrid", "hybrid", None, extra={"mode": mode, "parts": parts}))
    return clocks


def locked_holdout_dates(dates: list[str], frac: float = LOCKED_HOLDOUT_FRAC) -> list[str]:
    if not dates:
        return []
    start = int(np.floor(len(dates) * (1.0 - frac)))
    return list(dates[start:])


def _et_tod_minute(ts: pd.Series) -> pd.Series:
    et = pd.to_datetime(ts, utc=True).dt.tz_convert("America/New_York")
    return (et.dt.hour * 60 + et.dt.minute).astype(int)


def load_panel(oos_path: Path, feature_path: Path) -> pd.DataFrame:
    oos = pd.read_csv(oos_path, parse_dates=["bucket_ts"])
    oos = oos[oos["construction"] == CONSTRUCTION].copy()
    oos["bucket_ts"] = pd.to_datetime(oos["bucket_ts"], utc=True)
    oos = oos.dropna(subset=["price", "fair_value", "residual", "residual_z"])
    feat_cols = pd.read_csv(feature_path, nrows=0).columns.tolist()
    keep = ["bucket_ts", "et_date", "trade_intensity", "avg_trade_size"]
    if "vol_per_min" in feat_cols:
        keep.append("vol_per_min")
    if "TY_contracts" in feat_cols:
        keep.append("TY_contracts")
    feat = pd.read_csv(feature_path, usecols=[c for c in keep if c in feat_cols], parse_dates=["bucket_ts"])
    feat["bucket_ts"] = pd.to_datetime(feat["bucket_ts"], utc=True)
    panel = oos.merge(feat, on=["bucket_ts", "et_date"], how="left", suffixes=("", "_feat"))
    prints = panel["trade_intensity"].astype(float).fillna(0.0).clip(lower=0.0)
    if "vol_per_min" in panel.columns:
        volume = panel["vol_per_min"].astype(float).fillna(0.0).clip(lower=0.0)
    elif "TY_contracts" in panel.columns:
        volume = panel["TY_contracts"].astype(float).fillna(0.0).clip(lower=0.0)
    else:
        volume = prints * panel["avg_trade_size"].astype(float).fillna(0.0).clip(lower=0.0)
    panel["prints"] = prints
    panel["volume"] = volume
    panel = panel.sort_values(["et_date", "bucket_ts"]).reset_index(drop=True)
    panel["tod_minute"] = _et_tod_minute(panel["bucket_ts"])
    panel["price_tick"] = (
        panel.groupby("et_date", sort=False)["price"].diff().abs() >= (0.5 * TICK_SIZE)
    ).astype(float)
    return panel


def discovery_tod_targets(panel: pd.DataFrame, discovery_dates: set[str]) -> dict[tuple[int, int, float], dict[int, float]]:
    disc = panel[panel["et_date"].astype(str).isin(discovery_dates)].copy()
    if disc.empty:
        return {}
    out: dict[tuple[int, int, float], dict[int, float]] = {}
    for fwd, q in {(int(spec[1]), float(spec[2])) for spec in ADAPTIVE_SPECS}:
        frames = []
        for _, day in disc.groupby("et_date", sort=False):
            day = day.sort_values("bucket_ts")
            prints = day["prints"].to_numpy(dtype=float)
            c = np.concatenate([[0.0], np.cumsum(prints)])
            idx = np.arange(len(day))
            end = np.minimum(idx + fwd, len(day) - 1)
            # prints strictly after this bar, through +fwd bars
            fwd_sum = c[end + 1] - c[idx + 1]
            fwd_sum[idx + fwd > len(day) - 1] = np.nan
            frames.append(pd.DataFrame({"tod_minute": day["tod_minute"].to_numpy(), "fwd": fwd_sum}))
        stacked = pd.concat(frames, ignore_index=True)
        grouped = stacked.groupby("tod_minute")["fwd"].quantile(q)
        out[(fwd, int(round(q * 100)), q)] = {int(k): float(v) for k, v in grouped.dropna().items()}
    return out


def _first_reach_cumsum(values: np.ndarray, start: int, target: float) -> int | None:
    if start >= len(values) - 1 or not np.isfinite(target) or target <= 0:
        return None
    c = np.cumsum(values)
    base = c[start]
    after = c[start + 1 :] - base
    hit = np.flatnonzero(after >= target)
    if hit.size == 0:
        return None
    return int(start + 1 + hit[0])


def _first_reach_wall(ts: np.ndarray, start: int, minutes: float) -> int | None:
    target = ts[start] + np.timedelta64(int(round(minutes * 60)), "s")
    hit = np.flatnonzero(ts[start:] >= target)
    if hit.size == 0:
        return None
    return int(start + hit[0])


def _hybrid_reach(
    ts: np.ndarray,
    prints: np.ndarray,
    volume: np.ndarray,
    ticks: np.ndarray,
    start: int,
    mode: str,
    parts: tuple[tuple[str, float], ...],
) -> int | None:
    reaches = []
    for kind, target in parts:
        if kind == "wall":
            reaches.append(_first_reach_wall(ts, start, target))
        elif kind == "prints":
            reaches.append(_first_reach_cumsum(prints, start, target))
        elif kind == "volume":
            reaches.append(_first_reach_cumsum(volume, start, target))
        elif kind == "ticks":
            reaches.append(_first_reach_cumsum(ticks, start, target))
        else:
            reaches.append(None)
    known = [r for r in reaches if r is not None]
    if mode == "first":
        return min(known) if known else None
    if any(r is None for r in reaches):
        return None
    return max(reaches)  # type: ignore[arg-type]


def resolve_index(abs_z: np.ndarray, ts: np.ndarray, start: int, min_hold_minutes: float = MIN_HOLD_MINUTES) -> int | None:
    hold_ts = ts[start] + np.timedelta64(int(round(min_hold_minutes * 60)), "s")
    after_hold = np.flatnonzero((ts[start:] >= hold_ts) & (abs_z[start:] < RESET_Z))
    if after_hold.size == 0:
        return None
    return int(start + after_hold[0])


def deadline_index(
    clock: Clock,
    ts: np.ndarray,
    prints: np.ndarray,
    volume: np.ndarray,
    ticks: np.ndarray,
    tod: np.ndarray,
    start: int,
    tod_lookup: dict[tuple[int, int, float], dict[int, float]],
) -> int | None:
    if clock.kind == "wall":
        return _first_reach_wall(ts, start, float(clock.target or 0.0))
    if clock.kind == "prints":
        return _first_reach_cumsum(prints, start, float(clock.target or 0.0))
    if clock.kind == "volume":
        return _first_reach_cumsum(volume, start, float(clock.target or 0.0))
    if clock.kind == "ticks":
        return _first_reach_cumsum(ticks, start, float(clock.target or 0.0))
    if clock.kind == "hybrid":
        extra = clock.extra or {}
        return _hybrid_reach(ts, prints, volume, ticks, start, str(extra["mode"]), tuple(extra["parts"]))
    if clock.kind == "adaptive_prints":
        extra = clock.extra or {}
        fwd = int(extra["forward_min"])
        q = float(extra["quantile"])
        table = tod_lookup.get((fwd, int(round(q * 100)), q), {})
        target = table.get(int(tod[start]))
        if target is None or not np.isfinite(target) or target <= 0:
            target = float(np.nanmedian(list(table.values()))) if table else 50.0
        reached = _first_reach_cumsum(prints, start, float(target))
        if clock.cap_minutes is not None:
            cap = _first_reach_wall(ts, start, float(clock.cap_minutes))
            if reached is None:
                return cap
            if cap is None:
                return reached
            return min(reached, cap)
        return reached
    raise ValueError(clock.kind)


def price_share(open_residual: float, open_price: float, close_price: float) -> float | None:
    if abs(open_residual) < TICK_SIZE:
        return None
    sign = 1.0 if open_residual > 0 else -1.0
    share = sign * (open_price - close_price) / abs(open_residual)
    return float(max(-1.0, min(2.0, share)))


def replay_day(
    day: pd.DataFrame,
    clock: Clock,
    tod_lookup: dict[tuple[int, int, float], dict[int, float]],
) -> list[dict[str, Any]]:
    day = day.sort_values("bucket_ts")
    ts = day["bucket_ts"].to_numpy(dtype="datetime64[ns]")
    price = day["price"].to_numpy(dtype=float)
    fv = day["fair_value"].to_numpy(dtype=float)
    residual = day["residual"].to_numpy(dtype=float)
    z = day["residual_z"].to_numpy(dtype=float)
    abs_z = np.abs(z)
    prints = day["prints"].to_numpy(dtype=float)
    volume = day["volume"].to_numpy(dtype=float)
    ticks = day["price_tick"].to_numpy(dtype=float)
    tod = day["tod_minute"].to_numpy(dtype=int)
    et_date = str(day["et_date"].iloc[0])
    episodes: list[dict[str, Any]] = []
    i = 0
    n = len(day)
    while i < n:
        if not np.isfinite(abs_z[i]) or abs_z[i] < WATCH_Z:
            i += 1
            continue
        end = deadline_index(clock, ts, prints, volume, ticks, tod, i, tod_lookup)
        resolved_at = resolve_index(abs_z, ts, i)
        if end is None:
            reversion = "censored"
            status = "censored"
            close_i = None
        elif resolved_at is not None and resolved_at <= end:
            reversion = "resolved"
            status = "finalized"
            close_i = resolved_at
        else:
            reversion = "expired"
            status = "finalized"
            close_i = None
        score_i = end
        direction_sign = -1.0 if residual[i] > 0 else 1.0
        direction = "SHORT_RICH" if residual[i] > 0 else "LONG_CHEAP"
        row: dict[str, Any] = {
            "clock_id": clock.id,
            "family": clock.family,
            "et_date": et_date,
            "opened_ts": pd.Timestamp(ts[i]).isoformat(),
            "state": "DISLOCATION" if abs_z[i] >= DISLOCATION_Z else "WATCH",
            "direction": direction,
            "direction_sign": direction_sign,
            "open_residual_z": float(z[i]),
            "open_residual": float(residual[i]),
            "open_price": float(price[i]),
            "open_fair_value": float(fv[i]),
            "open_prints": float(prints[i]),
            "open_volume": float(volume[i]),
            "status": status,
            "reversion_status": reversion,
        }
        if close_i is not None:
            row["closed_ts"] = pd.Timestamp(ts[close_i]).isoformat()
            row["minutes_to_close"] = float((ts[close_i] - ts[i]) / np.timedelta64(1, "m"))
            row["close_price"] = float(price[close_i])
            row["close_fair_value"] = float(fv[close_i])
            row["price_share_of_reversion"] = price_share(float(residual[i]), float(price[i]), float(price[close_i]))
            path = price[i : close_i + 1]
            path_pnl = direction_sign * (path - price[i])
            row["mae_to_resolve_ticks"] = float(abs(min(float(path_pnl.min()), 0.0)) / TICK_SIZE)
        if score_i is not None:
            row["deadline_ts"] = pd.Timestamp(ts[score_i]).isoformat()
            row["minutes_to_deadline"] = float((ts[score_i] - ts[i]) / np.timedelta64(1, "m"))
            row["prints_to_deadline"] = float(prints[i + 1 : score_i + 1].sum()) if score_i > i else 0.0
            row["volume_to_deadline"] = float(volume[i + 1 : score_i + 1].sum()) if score_i > i else 0.0
            row["ticks_to_deadline"] = float(ticks[i + 1 : score_i + 1].sum()) if score_i > i else 0.0
            end_price = float(price[score_i])
            path = price[i : score_i + 1]
            path_pnl = direction_sign * (path - price[i])
            row["dir_pnl"] = float(direction_sign * (end_price - price[i]))
            row["adverse"] = float(path_pnl.min())
            row["adverse_ticks"] = float(abs(min(float(path_pnl.min()), 0.0)) / TICK_SIZE)
            row["dir_pnl_ticks"] = float(row["dir_pnl"] / TICK_SIZE)
        episodes.append(row)
        i = (end + 1) if end is not None else n
    return episodes


def replay_panel(panel: pd.DataFrame, clock: Clock, tod_lookup: dict) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, day in panel.groupby("et_date", sort=True):
        rows.extend(replay_day(day, clock, tod_lookup))
    return rows


def _series(episodes: list[dict[str, Any]], key: str) -> pd.Series:
    return pd.Series([e.get(key) for e in episodes if e.get(key) is not None], dtype=float)


def clock_stats(episodes: list[dict[str, Any]], split: str, clock: Clock) -> dict[str, Any]:
    finalized = [e for e in episodes if e.get("status") == "finalized"]
    resolved = [e for e in episodes if e.get("reversion_status") == "resolved"]
    expired = [e for e in episodes if e.get("reversion_status") == "expired"]
    censored = [e for e in episodes if e.get("reversion_status") == "censored"]
    pnl = _series(finalized, "dir_pnl")
    pnl_ticks = _series(finalized, "dir_pnl_ticks")
    adv_ticks = _series(finalized, "adverse_ticks")
    share = _series(resolved, "price_share_of_reversion")
    minutes = _series(resolved, "minutes_to_close")
    deadline_min = _series(finalized, "minutes_to_deadline")
    return {
        "clock_id": clock.id,
        "family": clock.family,
        "split": split,
        "n_issued": len(episodes),
        "n_watch": sum(1 for e in episodes if e.get("state") == "WATCH"),
        "n_dislocation": sum(1 for e in episodes if e.get("state") == "DISLOCATION"),
        "n_finalized": len(finalized),
        "n_resolved": len(resolved),
        "n_expired": len(expired),
        "n_censored": len(censored),
        "resolution_rate": (len(resolved) / len(episodes)) if episodes else None,
        "scored_hit_rate": float((pnl > 0).mean()) if not pnl.empty else None,
        "avg_dir_pnl_ticks": float(pnl_ticks.mean()) if not pnl_ticks.empty else None,
        "median_dir_pnl_ticks": float(pnl_ticks.median()) if not pnl_ticks.empty else None,
        "p_adverse_le_3": float((adv_ticks <= ADVERSE_TICK_CAP).mean()) if not adv_ticks.empty else None,
        "median_adverse_ticks": float(adv_ticks.median()) if not adv_ticks.empty else None,
        "median_price_share": float(share.median()) if not share.empty else None,
        "mean_price_share": float(share.mean()) if not share.empty else None,
        "median_minutes_to_close": float(minutes.median()) if not minutes.empty else None,
        "median_minutes_to_deadline": float(deadline_min.median()) if not deadline_min.empty else None,
        "p_deadline_in_5_15m": float(((deadline_min >= SWEET_DURATION_MIN) & (deadline_min <= SWEET_DURATION_MAX)).mean()) if not deadline_min.empty else None,
        "median_prints_to_deadline": float(_series(finalized, "prints_to_deadline").median()) if finalized else None,
        "median_mae_to_resolve_ticks": float(_series(resolved, "mae_to_resolve_ticks").median()) if resolved else None,
    }


def _usable_holdout(row: dict[str, Any]) -> bool:
    deadline = row.get("median_minutes_to_deadline")
    resolved = row.get("n_resolved") or 0
    return (
        row.get("split") == "holdout"
        and row.get("scored_hit_rate") is not None
        and resolved >= 50
        and deadline is not None
        and SWEET_DURATION_MIN <= float(deadline) <= SWEET_DURATION_MAX
    )


def rank_holdout(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = [r for r in rows if _usable_holdout(r)]
    ranked = sorted(
        scored,
        key=lambda r: (
            float(r.get("scored_hit_rate") or 0.0),
            float(r.get("avg_dir_pnl_ticks") or 0.0),
            float(r.get("p_adverse_le_3") or 0.0),
        ),
        reverse=True,
    )
    return [{**r, "holdout_usable_rank": i} for i, r in enumerate(ranked, start=1)]


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    panel = load_panel(Path(args.oos), Path(args.features))
    dates = sorted(panel["et_date"].astype(str).unique().tolist())
    holdout = locked_holdout_dates(dates)
    holdout_set = set(holdout)
    discovery = [d for d in dates if d not in holdout_set]
    tod_lookup = discovery_tod_targets(panel, set(discovery))
    clocks = frozen_clocks()
    all_eps: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    for clock in clocks:
        episodes = replay_panel(panel, clock, tod_lookup)
        for episode in episodes:
            episode["split"] = "holdout" if episode["et_date"] in holdout_set else "discovery"
        all_eps.extend(episodes)
        hold_eps = [e for e in episodes if e["et_date"] in holdout_set]
        metrics.append(clock_stats(episodes, "all_oos", clock))
        metrics.append(clock_stats(hold_eps, "holdout", clock))
        print(f"{clock.id}: issued={len(episodes)} holdout={len(hold_eps)}", flush=True)

    ranked = rank_holdout(metrics)
    baseline = next((m for m in metrics if m["clock_id"] == "wall_10m" and m["split"] == "holdout"), None)
    best = ranked[0] if ranked else None
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "construction": CONSTRUCTION,
        "enter_z": WATCH_Z,
        "exit_z": RESET_Z,
        "min_hold_minutes": MIN_HOLD_MINUTES,
        "n_dates": len(dates),
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
        "n_discovery_dates": len(discovery),
        "n_holdout_dates": len(holdout),
        "holdout_dates": holdout,
        "n_clocks": len(clocks),
        "clocks": [c.id for c in clocks],
        "ship_gate": False,
        "true_mbp1_updates": False,
        "proxies": ["trade_intensity prints", "intensity*avg_trade_size volume", "1m price-tick minutes"],
        "baseline_holdout_wall_10m": baseline,
        "ranking_rule": "usable holdout: n_resolved>=50 and median deadline in [5,15]; rank by hit then avg pnl ticks",
        "best_usable_holdout": best,
        "note": "Judge clocks by holdout % via price and fade hit. Resolve rate is not a fade edge.",
    }
    metrics_df = pd.DataFrame(metrics)
    ranked_df = pd.DataFrame(ranked)
    metrics_df.to_csv(output_dir / "clock_metrics.csv", index=False)
    ranked_df.to_csv(output_dir / "clock_holdout_usable_ranked.csv", index=False)
    pd.DataFrame(all_eps).to_csv(output_dir / "clock_episodes.csv", index=False)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay V2.7 alerts under frozen wall/event clocks")
    parser.add_argument("--oos", default=str(OOS_PATH))
    parser.add_argument("--features", default=str(FEATURE_MATRIX))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    summary = run(parser.parse_args())
    print(json.dumps({
        "n_clocks": summary["n_clocks"],
        "n_dates": summary["n_dates"],
        "holdout_dates": [summary["holdout_dates"][0], summary["holdout_dates"][-1]] if summary["holdout_dates"] else [],
        "baseline": summary["baseline_holdout_wall_10m"],
        "best": summary["best_usable_holdout"],
        "ship_gate": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
