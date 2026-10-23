#!/usr/bin/env python3
"""Research-only: ATR range vs 8bps non-event breakout after V1 residual caution.

Reuses the causal V1 residual construction from the dispersion study, but replaces
the old path-efficiency RANGE/TREND labels with:

  BREAKOUT         = |forward net| >= 8 yield-bps AND not event-near
  CONTINUED_RANGE  = forward high-low <= k * prior-day RTH ATR AND not BREAKOUT
  OTHER            = everything else

Outputs only under workspace/analysis/aggression_v1_dispersion_atr_breakout/.
Does not touch live V1/V2/V3 or V1.2.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_gate_lib import bootstrap_ci, one_sided_p  # noqa: E402
from scripts.aggression_v1_flow_price_dispersion_research import (  # noqa: E402
    EPISODE_STRIDE_MIN,
    EVENTS_DB,
    FEATURE_MATRIX,
    HORIZONS,
    MIN_N,
    N_BOOT,
    TICK_SIZE,
    WINDOWS,
    _near_event,
    _event_times,
    add_dispersion_dynamics,
    chronological_splits,
    decluster_episodes,
    fit_causal_v1_residual,
    load_features,
)

OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v1_dispersion_atr_breakout"
# Local TY convention used elsewhere in this repo (jolts dashboards): ~2 ticks / yield bp.
TY_TICKS_PER_BP = 2.0
BREAKOUT_BPS = 8.0
BREAKOUT_TICKS = BREAKOUT_BPS * TY_TICKS_PER_BP  # 16 ticks
ATR_RANGE_MULT = 1.0
PRIMARY_HORIZON = 30
EASTERN = "America/New_York"
RTH_START = 8 * 60
RTH_END = 15 * 60


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def _is_rth_minutes(ts: pd.Timestamp) -> bool:
    et = ts.tz_convert(EASTERN)
    mins = et.hour * 60 + et.minute
    return RTH_START <= mins < RTH_END


def prior_day_rth_atr_ticks(df: pd.DataFrame) -> pd.Series:
    """Causal prior-day RTH ATR proxy = prior et_date RTH high-low in ticks."""
    rth = df[df.index.map(_is_rth_minutes)].copy()
    if rth.empty:
        return pd.Series(np.nan, index=df.index)
    daily = rth.groupby("et_date")["price"].agg(["min", "max"])
    daily["atr_ticks"] = (daily["max"] - daily["min"]) / TICK_SIZE
    # Map each row to prior calendar date's ATR (shift by ordered dates).
    dates = sorted(daily.index.tolist())
    prior = {dates[i]: float(daily.loc[dates[i - 1], "atr_ticks"]) for i in range(1, len(dates))}
    return df["et_date"].map(prior).astype(float)


def add_atr_breakout_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for _, day in df.groupby("et_date", sort=False):
        d = day.sort_index().copy()
        price = d["price"].astype(float)
        for h in HORIZONS:
            future = price.shift(-h)
            net = (future - price) / TICK_SIZE
            fwd_high = price.iloc[::-1].rolling(h + 1, min_periods=h + 1).max().iloc[::-1]
            fwd_low = price.iloc[::-1].rolling(h + 1, min_periods=h + 1).min().iloc[::-1]
            fwd_range = (fwd_high - fwd_low) / TICK_SIZE
            d[f"fwd_net_ticks_{h}m"] = net
            d[f"fwd_abs_net_ticks_{h}m"] = net.abs()
            d[f"fwd_range_ticks_{h}m"] = fwd_range
            d[f"fwd_abs_net_bps_{h}m"] = net.abs() / TY_TICKS_PER_BP
            if len(d) >= h:
                cols = [
                    f"fwd_net_ticks_{h}m",
                    f"fwd_abs_net_ticks_{h}m",
                    f"fwd_range_ticks_{h}m",
                    f"fwd_abs_net_bps_{h}m",
                ]
                d.loc[d.index[-h]:, cols] = np.nan
        parts.append(d)
    out = pd.concat(parts).sort_index()
    out["prior_atr_ticks"] = prior_day_rth_atr_ticks(out)
    h = PRIMARY_HORIZON
    abs_net = out[f"fwd_abs_net_ticks_{h}m"]
    fwd_range = out[f"fwd_range_ticks_{h}m"]
    atr = out["prior_atr_ticks"]
    event = out.get("event_near", pd.Series(False, index=out.index)).fillna(False)
    breakout = (abs_net >= BREAKOUT_TICKS) & (~event) & abs_net.notna()
    continued_range = (
        (fwd_range <= (ATR_RANGE_MULT * atr))
        & (~breakout)
        & fwd_range.notna()
        & atr.notna()
        & (atr > 0)
    )
    label = pd.Series("OTHER", index=out.index, dtype=object)
    label = label.mask(continued_range, "CONTINUED_RANGE")
    label = label.mask(breakout, "BREAKOUT")
    label = label.mask(abs_net.isna() | fwd_range.isna(), np.nan)
    out["future_regime_atr"] = label
    out["future_is_breakout"] = out["future_regime_atr"] == "BREAKOUT"
    out["future_is_continued_range"] = out["future_regime_atr"] == "CONTINUED_RANGE"
    out["future_is_extreme_labeled"] = out["future_regime_atr"].isin(["BREAKOUT", "CONTINUED_RANGE"])
    return out


def annotate_caution(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    residual = pd.to_numeric(out["v1_residual"], errors="coerce")
    out["abs_v1_residual_z"] = pd.to_numeric(out["v1_residual_z"], errors="coerce").abs()
    out["residual_widening_15m"] = residual.abs() > residual.shift(15).abs()
    out["large_widening"] = out["abs_v1_residual_z"].ge(1.0) & out["residual_widening_15m"].fillna(False)
    return out


def evaluate_rate(
    episodes: pd.DataFrame,
    *,
    cell_id: str,
    family: str,
    split_name: str,
    outcome_col: str,
    baseline_rate: float,
) -> dict[str, Any]:
    if episodes.empty or outcome_col not in episodes:
        return {
            "cell_id": cell_id,
            "family": family,
            "split": split_name,
            "n": 0,
            "n_dates": 0,
            "rate": np.nan,
            "baseline_rate": baseline_rate,
            "lift_pp": np.nan,
            "ci_low": np.nan,
            "ci_high": np.nan,
            "one_sided_lower": np.nan,
            "p_one_sided": np.nan,
        }
    y = episodes[outcome_col].astype(float)
    groups = episodes["et_date"].astype(str).tolist()
    ci = bootstrap_ci(y.to_numpy(), n_boot=N_BOOT, seed=abs(hash(cell_id)) % (2**31 - 1), groups=groups)
    rate = float(ci["mean"]) if ci["n"] else np.nan
    lift = rate - baseline_rate if np.isfinite(rate) else np.nan
    lift_values = (y - baseline_rate).to_numpy()
    lift_ci = bootstrap_ci(lift_values, n_boot=N_BOOT, seed=(abs(hash(cell_id)) + 11) % (2**31 - 1), groups=groups)
    return {
        "cell_id": cell_id,
        "family": family,
        "split": split_name,
        "n": int(len(episodes)),
        "n_dates": int(episodes["et_date"].nunique()),
        "rate": rate,
        "baseline_rate": baseline_rate,
        "lift_pp": float(lift * 100.0) if np.isfinite(lift) else np.nan,
        "mean": float(lift_ci["mean"]) if lift_ci["n"] else np.nan,
        "ci_low": float(lift_ci["ci_low"]) if lift_ci["n"] else np.nan,
        "ci_high": float(lift_ci["ci_high"]) if lift_ci["n"] else np.nan,
        "one_sided_lower": float(lift_ci["one_sided_lower"]) if lift_ci["n"] else np.nan,
        "p_one_sided": float(one_sided_p(lift_values)),
    }


def run_split(df: pd.DataFrame, dates: list[str], split_name: str) -> list[dict[str, Any]]:
    subset = df[df["et_date"].isin(set(dates))].copy()
    # Primary filter: non-event bars with an extreme label OR all non-event for baselines.
    nonevent = subset[~subset["event_near"].fillna(False)].copy()
    labeled = nonevent[nonevent["future_regime_atr"].isin(["BREAKOUT", "CONTINUED_RANGE"])].copy()
    if labeled.empty:
        return []
    base_break = float(labeled["future_is_breakout"].mean())
    base_range = float(labeled["future_is_continued_range"].mean())
    rows = []
    # Unconditional baselines on filtered labeled set.
    rows.append(
        {
            "cell_id": "baseline_filtered_breakout",
            "family": "baseline",
            "split": split_name,
            "n": int(len(labeled)),
            "n_dates": int(labeled["et_date"].nunique()),
            "rate": base_break,
            "baseline_rate": base_break,
            "lift_pp": 0.0,
            "mean": 0.0,
            "ci_low": np.nan,
            "ci_high": np.nan,
            "one_sided_lower": np.nan,
            "p_one_sided": np.nan,
        }
    )
    rows.append(
        {
            "cell_id": "baseline_filtered_continued_range",
            "family": "baseline",
            "split": split_name,
            "n": int(len(labeled)),
            "n_dates": int(labeled["et_date"].nunique()),
            "rate": base_range,
            "baseline_rate": base_range,
            "lift_pp": 0.0,
            "mean": 0.0,
            "ci_low": np.nan,
            "ci_high": np.nan,
            "one_sided_lower": np.nan,
            "p_one_sided": np.nan,
        }
    )

    masks = {
        "large_widening": labeled["large_widening"].fillna(False),
        "large_only": labeled["abs_v1_residual_z"].ge(1.0),
        "widening_only": labeled["residual_widening_15m"].fillna(False),
        "small_residual": labeled["abs_v1_residual_z"].lt(0.5),
    }
    for name, mask in masks.items():
        episodes = decluster_episodes(labeled, mask, stride_min=EPISODE_STRIDE_MIN)
        rows.append(
            evaluate_rate(
                episodes,
                cell_id=f"{name}_breakout",
                family="caution_breakout",
                split_name=split_name,
                outcome_col="future_is_breakout",
                baseline_rate=base_break,
            )
        )
        rows.append(
            evaluate_rate(
                episodes,
                cell_id=f"{name}_continued_range",
                family="caution_range",
                split_name=split_name,
                outcome_col="future_is_continued_range",
                baseline_rate=base_range,
            )
        )

    # Also report among ALL non-event minutes (including OTHER) — breakout rarity.
    nonevent_scored = nonevent.dropna(subset=[f"fwd_abs_net_ticks_{PRIMARY_HORIZON}m"])
    if not nonevent_scored.empty:
        base_bo_all = float(nonevent_scored["future_is_breakout"].mean())
        base_cr_all = float(nonevent_scored["future_is_continued_range"].mean())
        for name, mask_src in {
            "large_widening": nonevent_scored["large_widening"].fillna(False),
            "large_only": nonevent_scored["abs_v1_residual_z"].ge(1.0),
        }.items():
            episodes = decluster_episodes(nonevent_scored, mask_src, stride_min=EPISODE_STRIDE_MIN)
            rows.append(
                evaluate_rate(
                    episodes,
                    cell_id=f"all_nonevent_{name}_breakout",
                    family="nonevent_all_breakout",
                    split_name=split_name,
                    outcome_col="future_is_breakout",
                    baseline_rate=base_bo_all,
                )
            )
            rows.append(
                evaluate_rate(
                    episodes,
                    cell_id=f"all_nonevent_{name}_continued_range",
                    family="nonevent_all_range",
                    split_name=split_name,
                    outcome_col="future_is_continued_range",
                    baseline_rate=base_cr_all,
                )
            )
            rows.append(
                {
                    "cell_id": f"all_nonevent_baseline_{name}",
                    "family": "nonevent_all_meta",
                    "split": split_name,
                    "n": int(len(nonevent_scored)),
                    "n_dates": int(nonevent_scored["et_date"].nunique()),
                    "rate": base_bo_all,
                    "baseline_rate": base_bo_all,
                    "lift_pp": 0.0,
                    "note": f"unconditional nonevent breakout rate; range_rate={base_cr_all:.4f}",
                }
            )
    return rows


def regime_mix(df: pd.DataFrame, dates: list[str], split_name: str) -> dict[str, Any]:
    subset = df[df["et_date"].isin(set(dates))]
    nonevent = subset[~subset["event_near"].fillna(False)]
    labels = nonevent["future_regime_atr"].value_counts(dropna=False).to_dict()
    return {
        "split": split_name,
        "n_rows": int(len(nonevent)),
        "n_dates": int(nonevent["et_date"].nunique()),
        "label_counts": {str(k): int(v) for k, v in labels.items()},
        "breakout_share_all_nonevent": float(nonevent["future_is_breakout"].mean()) if len(nonevent) else np.nan,
        "continued_range_share_all_nonevent": float(nonevent["future_is_continued_range"].mean()) if len(nonevent) else np.nan,
        "median_prior_atr_ticks": float(nonevent["prior_atr_ticks"].median()) if len(nonevent) else np.nan,
        "breakout_threshold_ticks": BREAKOUT_TICKS,
        "breakout_threshold_bps": BREAKOUT_BPS,
        "atr_range_mult": ATR_RANGE_MULT,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    features, meta = load_features(FEATURE_MATRIX)
    features["event_near"] = _near_event(features.index, _event_times(EVENTS_DB))
    panel = fit_causal_v1_residual(features)
    panel = add_dispersion_dynamics(panel)
    panel = annotate_caution(panel)
    panel = add_atr_breakout_outcomes(panel)
    splits = chronological_splits(meta["clean_dates"])

    journal: list[dict[str, Any]] = []
    mixes = []
    for split_name in ("discovery", "validation"):
        mixes.append(regime_mix(panel, splits[split_name], split_name))
        journal.extend(run_split(panel, splits[split_name], split_name))

    journal_df = pd.DataFrame(journal)
    val = journal_df[journal_df["split"] == "validation"].copy()
    disc = journal_df[journal_df["split"] == "discovery"].copy()

    panel_out = panel[
        [
            "et_date",
            "price",
            "v1_residual",
            "v1_residual_z",
            "abs_v1_residual_z",
            "residual_widening_15m",
            "large_widening",
            "event_near",
            "prior_atr_ticks",
            f"fwd_abs_net_ticks_{PRIMARY_HORIZON}m",
            f"fwd_abs_net_bps_{PRIMARY_HORIZON}m",
            f"fwd_range_ticks_{PRIMARY_HORIZON}m",
            "future_regime_atr",
            "future_is_breakout",
            "future_is_continued_range",
        ]
    ].copy()
    panel_out.to_csv(OUTPUT_DIR / "atr_breakout_panel.csv")
    journal_df.to_csv(OUTPUT_DIR / "candidate_journal.csv", index=False)
    val.sort_values("lift_pp", ascending=False).to_csv(OUTPUT_DIR / "validation_leaderboard.csv", index=False)
    _write_json(OUTPUT_DIR / "split_manifest.json", splits)
    _write_json(
        OUTPUT_DIR / "definition.json",
        {
            "breakout_bps": BREAKOUT_BPS,
            "ty_ticks_per_bp": TY_TICKS_PER_BP,
            "breakout_ticks": BREAKOUT_TICKS,
            "atr": "prior_day_RTH_high_low_ticks",
            "continued_range": f"fwd_range_ticks_{PRIMARY_HORIZON}m <= {ATR_RANGE_MULT} * prior_atr",
            "event_filter": "event_near within +/-30m excluded from BREAKOUT; primary cells use non-event bars",
            "horizon_min": PRIMARY_HORIZON,
            "caution": "abs(v1_residual_z)>=1 and residual_widening_15m",
        },
    )

    # Headline: large_widening on filtered extreme labels.
    def _pick(frame: pd.DataFrame, cell: str) -> dict[str, Any]:
        hit = frame[frame["cell_id"] == cell]
        return hit.iloc[0].to_dict() if len(hit) else {}

    summary = {
        "ok": True,
        "definitions": {
            "BREAKOUT": f"|net| >= {BREAKOUT_BPS} bps ({BREAKOUT_TICKS} ticks) AND not event_near",
            "CONTINUED_RANGE": f"fwd range <= {ATR_RANGE_MULT}x prior-day RTH ATR AND not BREAKOUT",
            "filtered_dataset": "non-event bars with BREAKOUT or CONTINUED_RANGE label",
        },
        "regime_mix": mixes,
        "discovery": {
            "large_widening_breakout": _pick(disc, "large_widening_breakout"),
            "large_widening_continued_range": _pick(disc, "large_widening_continued_range"),
            "all_nonevent_large_widening_breakout": _pick(disc, "all_nonevent_large_widening_breakout"),
            "all_nonevent_large_widening_continued_range": _pick(disc, "all_nonevent_large_widening_continued_range"),
        },
        "validation": {
            "large_widening_breakout": _pick(val, "large_widening_breakout"),
            "large_widening_continued_range": _pick(val, "large_widening_continued_range"),
            "all_nonevent_large_widening_breakout": _pick(val, "all_nonevent_large_widening_breakout"),
            "all_nonevent_large_widening_continued_range": _pick(val, "all_nonevent_large_widening_continued_range"),
        },
        "top_validation": val.sort_values("lift_pp", ascending=False).head(15).to_dict(orient="records"),
        "artifacts": {
            "panel": str(OUTPUT_DIR / "atr_breakout_panel.csv"),
            "journal": str(OUTPUT_DIR / "candidate_journal.csv"),
            "leaderboard": str(OUTPUT_DIR / "validation_leaderboard.csv"),
        },
        "notes": [
            "FINAL not inspected (exploratory redefinition).",
            "Does not authorize V1.2 definition change without review.",
        ],
    }
    _write_json(OUTPUT_DIR / "summary.json", summary)
    print(json.dumps(summary, indent=2, default=_json_default))


if __name__ == "__main__":
    main()
