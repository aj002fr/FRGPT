#!/usr/bin/env python3
"""Offline trade-spec harness for DISLOCATION x LIFO FRIENDLY.

Converts the V2 residual alert into an executable trade spec (next-bar entry,
stop, target, time stop, one position at a time, explicit cost) and scores it
through the shared date-clustered bootstrap / BH-FDR gate.

Read-only research. Writes only under
workspace/analysis/aggression_v2_lifo_dislocation_trade_spec/.
Does not import, write to, or restart any live V1/V2/V3 surface.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_gate_lib import (  # noqa: E402
    benjamini_hochberg,
    bootstrap_ci,
    classify,
    cost_sweep,
    one_sided_p,
)

PREDICTIONS = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models" / "walkforward_predictions.csv"
FEATURES = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
OUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_lifo_dislocation_trade_spec"

TICK = 1.0 / 64.0
MODEL = "ridge"

# Causal z + LIFO constants mirrored from the live V1.2/V2.2 context module.
Z_MIN_PERIODS = 20
TRADE_INT_LO_QUANTILE = 0.33

MAX_HORIZON = 60
HORIZONS = (15, 30, 60)
Z_ENTRIES = (1.5, 2.0)
STOPS: tuple[float | None, ...] = (2.0, 3.0, 4.0, None)
TARGETS: tuple[float | None, ...] = (1.0, 2.0, 3.0, None)
COSTS = (0.25, 0.5, 1.0)
HEADLINE_COST = 0.5
TRAIN_FRAC = 0.70
MIN_N = 30
BH_Q = 0.10


def load_panel() -> pd.DataFrame:
    pred = pd.read_csv(PREDICTIONS, parse_dates=["bucket_ts"])
    pred = pred[pred["model"] == MODEL].copy()
    feat = pd.read_csv(FEATURES, parse_dates=["bucket_ts"], usecols=["bucket_ts", "et_date", "trade_intensity"])
    pred["bucket_ts"] = pd.to_datetime(pred["bucket_ts"], utc=True)
    feat["bucket_ts"] = pd.to_datetime(feat["bucket_ts"], utc=True)
    df = pred.merge(feat, on=["bucket_ts", "et_date"], how="left")
    df.sort_values(["et_date", "bucket_ts"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def add_causal_context(df: pd.DataFrame) -> pd.DataFrame:
    """Session-expanding causal residual z and the frozen LIFO low-intensity flag."""
    out = df.copy()
    grp = out.groupby("et_date", sort=False)["residual"]
    mean = grp.transform(lambda s: s.shift(1).expanding(min_periods=Z_MIN_PERIODS).mean())
    std = grp.transform(lambda s: s.shift(1).expanding(min_periods=Z_MIN_PERIODS).std(ddof=1))
    out["residual_z"] = (out["residual"] - mean) / std.replace(0.0, np.nan)

    trade_int = pd.to_numeric(out["trade_intensity"], errors="coerce")
    lo_thr = trade_int.groupby(out["et_date"], sort=False).transform(
        lambda s: s.shift(1).expanding(min_periods=Z_MIN_PERIODS).quantile(TRADE_INT_LO_QUANTILE)
    )
    out["trade_int_lo"] = (trade_int <= lo_thr).fillna(False)
    return out


def build_forward_matrix(df: pd.DataFrame) -> np.ndarray:
    """F[i, k] = price at bar (i+1+k) minus price at bar (i+1), same session only.

    Entry is the NEXT bar after the signal bar, so there is no signal-bar fill.
    """
    n = len(df)
    price = df["price"].to_numpy(dtype=float)
    date_codes = pd.factorize(df["et_date"])[0]
    fwd = np.full((n, MAX_HORIZON), np.nan, dtype=float)
    for k in range(MAX_HORIZON):
        src = np.full(n, np.nan)
        end = n - (k + 1)
        if end <= 0:
            continue
        src[:end] = price[k + 1 : n]
        same_day = np.full(n, False)
        same_day[:end] = date_codes[k + 1 : n] == date_codes[:end]
        src[~same_day] = np.nan
        fwd[:, k] = src
    entry = fwd[:, 0].copy()
    return fwd - entry[:, None]


def simulate_cell(
    df: pd.DataFrame,
    fwd: np.ndarray,
    entry_price: np.ndarray,
    mask: np.ndarray,
    direction: np.ndarray,
    horizon: int,
    stop: float | None,
    target: float | None,
) -> pd.DataFrame:
    """Walk candidate signals with one-position-at-a-time declustering."""
    idx = np.flatnonzero(mask & np.isfinite(entry_price))
    date_codes = pd.factorize(df["et_date"])[0]
    dates = df["et_date"].to_numpy()

    rows: list[dict[str, Any]] = []
    blocked_until = -1
    blocked_day = -1
    for i in idx:
        if date_codes[i] == blocked_day and i <= blocked_until:
            continue
        path = fwd[i, :horizon] * direction[i] / TICK
        valid = np.flatnonzero(np.isfinite(path))
        if not len(valid):
            continue
        last_k = int(valid[-1])
        path = path[: last_k + 1]

        stop_k = int(np.argmax(path <= -stop)) if stop is not None and np.any(path <= -stop) else None
        tgt_k = int(np.argmax(path >= target)) if target is not None and np.any(path >= target) else None

        # Conservative: if both trigger in the same bar, assume the stop filled first.
        if stop_k is not None and (tgt_k is None or stop_k <= tgt_k):
            exit_k, reason, gross = stop_k, "stop", -float(stop)
        elif tgt_k is not None:
            exit_k, reason, gross = tgt_k, "target", float(target)
        else:
            exit_k, reason, gross = last_k, "time", float(path[last_k])

        rows.append(
            {
                "et_date": dates[i],
                "bucket_ts": df["bucket_ts"].iloc[i],
                "direction": float(direction[i]),
                "entry_price": float(entry_price[i]),
                "gross_ticks": float(gross),
                "exit_reason": reason,
                "bars_held": int(exit_k) + 1,
                "mae_ticks": float(np.min(path[: exit_k + 1])),
            }
        )
        blocked_day = date_codes[i]
        blocked_until = i + exit_k + 1
    return pd.DataFrame(rows)


def score_trades(trades: pd.DataFrame, label: dict[str, Any]) -> dict[str, Any]:
    cell = dict(label)
    n = len(trades)
    cell["n"] = int(n)
    if n < 2:
        cell.update({"mean_gross_ticks": np.nan, "mean_after_cost_ticks": np.nan, "verdict": "FAIL"})
        return cell

    gross = trades["gross_ticks"].to_numpy(dtype=float)
    net = gross - HEADLINE_COST
    cell["n_dates"] = int(trades["et_date"].nunique())
    cell["mean_gross_ticks"] = float(np.mean(gross))
    cell["hit_rate"] = float(np.mean(gross > 0))
    cell["mean_mae_ticks"] = float(trades["mae_ticks"].mean())
    cell["median_bars_held"] = float(trades["bars_held"].median())
    for reason in ("stop", "target", "time"):
        cell[f"frac_{reason}"] = float((trades["exit_reason"] == reason).mean())

    ci = bootstrap_ci(net, n_boot=2_000, groups=trades["et_date"].tolist())
    cell["mean_after_cost_ticks"] = float(ci["mean"])
    cell["ci_low"] = float(ci["ci_low"])
    cell["ci_high"] = float(ci["ci_high"])
    cell["one_sided_lower"] = float(ci["one_sided_lower"])
    cell["p_value"] = float(one_sided_p(net))
    cell.update(cost_sweep(gross, costs=COSTS))
    return cell


def stability_by_half(trades: pd.DataFrame) -> bool:
    """Both halves of the date range must be non-negative after cost."""
    if trades.empty:
        return False
    dates = sorted(trades["et_date"].unique())
    if len(dates) < 4:
        return False
    mid = len(dates) // 2
    first = trades[trades["et_date"].isin(dates[:mid])]["gross_ticks"] - HEADLINE_COST
    second = trades[trades["et_date"].isin(dates[mid:])]["gross_ticks"] - HEADLINE_COST
    if first.empty or second.empty:
        return False
    return bool(first.mean() >= 0.0 and second.mean() >= 0.0)


def build_variants(df: pd.DataFrame, z_entry: float) -> dict[str, np.ndarray]:
    z = pd.to_numeric(df["residual_z"], errors="coerce")
    lo = df["trade_int_lo"].to_numpy(dtype=bool)
    large = (z.abs() >= z_entry).fillna(False).to_numpy(dtype=bool)
    return {
        "DISLOCATION_ONLY": large,
        "DISLOCATION_x_LIFO": large & lo,
        "DISLOCATION_x_NOT_LIFO": large & ~lo,
    }


def run_grid(df: pd.DataFrame, fwd: np.ndarray, entry_price: np.ndarray, sample: str) -> list[dict[str, Any]]:
    direction = -np.sign(pd.to_numeric(df["residual"], errors="coerce").to_numpy(dtype=float))
    cells: list[dict[str, Any]] = []
    all_trades: dict[str, pd.DataFrame] = {}
    for z_entry in Z_ENTRIES:
        variants = build_variants(df, z_entry)
        for vname, mask in variants.items():
            for horizon in HORIZONS:
                for stop in STOPS:
                    for target in TARGETS:
                        trades = simulate_cell(df, fwd, entry_price, mask, direction, horizon, stop, target)
                        label = {
                            "sample": sample,
                            "variant": vname,
                            "z_entry": z_entry,
                            "horizon": horizon,
                            "stop_ticks": stop if stop is not None else "none",
                            "target_ticks": target if target is not None else "none",
                        }
                        cell = score_trades(trades, label)
                        cell["stability_ok"] = stability_by_half(trades)
                        key = f"{vname}|z{z_entry}|h{horizon}|s{stop}|t{target}"
                        all_trades[key] = trades
                        cell["key"] = key
                        cells.append(cell)
    _ = all_trades
    return cells


def finalize(cells: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(cells)
    eligible = frame["n"] >= MIN_N
    pvals = np.where(eligible, frame["p_value"], np.nan)
    bh = benjamini_hochberg(pvals, q=BH_Q)
    frame["bh_adjusted_p"] = bh["adjusted_p"]
    frame["bh_significant"] = bh["reject"]
    frame["verdict"] = [
        classify(row) if row.get("n", 0) >= MIN_N else "FAIL"
        for row in frame.to_dict("records")
    ]
    return frame


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = add_causal_context(load_panel())
    fwd = build_forward_matrix(df)
    entry_price = df["price"].shift(-1).to_numpy(dtype=float)

    dates = sorted(df["et_date"].unique().tolist())
    split = max(1, int(len(dates) * TRAIN_FRAC))
    explore_dates, holdout_dates = dates[:split], dates[split:]
    print(f"panel rows={len(df)} dates={len(dates)} explore={len(explore_dates)} holdout={len(holdout_dates)}", flush=True)

    all_cells: list[dict[str, Any]] = []
    for sample, sample_dates in (("explore", explore_dates), ("holdout", holdout_dates)):
        mask = df["et_date"].isin(sample_dates).to_numpy()
        sub = df[mask].reset_index(drop=True)
        sub_fwd = fwd[mask]
        sub_entry = entry_price[mask]
        print(f"running {sample} ({len(sub)} rows)...", flush=True)
        all_cells.extend(run_grid(sub, sub_fwd, sub_entry, sample))

    results = finalize(all_cells)
    results.sort_values(["sample", "mean_after_cost_ticks"], ascending=[True, False], inplace=True)
    results.to_csv(OUT_DIR / "trade_spec_results.csv", index=False)

    print("\n=== headline: variant comparison (explore, h=30, stop=3, target=2) ===", flush=True)
    sel = results[
        (results["sample"] == "explore")
        & (results["horizon"] == 30)
        & (results["stop_ticks"] == 3.0)
        & (results["target_ticks"] == 2.0)
    ]
    cols = ["variant", "z_entry", "n", "mean_gross_ticks", "hit_rate", "mean_after_cost_ticks", "verdict"]
    print(sel[cols].to_string(index=False), flush=True)

    for sample in ("explore", "holdout"):
        s = results[(results["sample"] == sample) & (results["n"] >= MIN_N)]
        print(f"\n=== {sample}: top 10 cells by after-cost ticks (n>={MIN_N}) ===", flush=True)
        print(s.head(10)[["variant", "z_entry", "horizon", "stop_ticks", "target_ticks", "n",
                          "mean_gross_ticks", "mean_after_cost_ticks", "one_sided_lower",
                          "bh_significant", "verdict"]].to_string(index=False), flush=True)
        print(f"{sample} verdicts: {dict(s['verdict'].value_counts())}", flush=True)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "panel_rows": int(len(df)),
        "dates": len(dates),
        "explore_dates": len(explore_dates),
        "holdout_dates": len(holdout_dates),
        "headline_cost_ticks": HEADLINE_COST,
        "cells": int(len(results)),
        "verdict_counts": {
            s: dict(results[(results["sample"] == s) & (results["n"] >= MIN_N)]["verdict"].value_counts())
            for s in ("explore", "holdout")
        },
    }
    (OUT_DIR / "trade_spec_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
