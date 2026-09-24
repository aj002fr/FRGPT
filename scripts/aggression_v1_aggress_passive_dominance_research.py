#!/usr/bin/env python3
"""Research-only aggress vs implied-passive dominance → RANGE/TREND study.

Role/absorption definition (plan B): AGGRESS_DOM when aggress same-sign-drives
price and dominates; PASSIVE_DOM when residual is large against/without
aggress; QUIET as a separate 4th bucket. Primary gate is path-efficiency
RANGE vs TREND; ATR/bps is diagnostic only.

Writes only under workspace/analysis/aggression_v1_aggress_passive_dominance/.
Does not touch live V1/V2/V3 or retune V1.2.
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

from scripts.aggression_gate_lib import (  # noqa: E402
    benjamini_hochberg,
    bootstrap_ci,
    one_sided_p,
)
from scripts.aggression_v1_dispersion_foundations_research import (  # noqa: E402
    add_forward_metrics as add_atr_forward_metrics,
    apply_gate,
    fast_decluster,
    label_config as label_atr_config,
)
from scripts.aggression_v1_flow_price_dispersion_research import (  # noqa: E402
    EPISODE_STRIDE_MIN,
    EVENTS_DB,
    FEATURE_MATRIX,
    TICK_SIZE,
    _event_times,
    _near_event,
    _session_expanding_z,
    add_forward_regime_outcomes,
    chronological_splits,
    fit_causal_v1_residual,
    learn_regime_cutoffs,
    load_features,
)

OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v1_aggress_passive_dominance"
FROZEN_CUTOFFS_PATH = (
    PROJECT_ROOT / "workspace" / "analysis" / "aggression_v1_flow_price_dispersion" / "regime_cutoffs.json"
)
TY_TICKS_PER_BP = 2.0
WINDOWS = (1, 5, 15)
DOMINANCE_RATIOS = (1.25, 1.5, 2.0)
HORIZONS = (15, 30, 60)
ATR_BPS_GRID = (4, 6, 8)
ATR_K_GRID = (0.5, 1.0, 1.5)
PRIMARY = {"w": 15, "d": 1.5, "h": 30}
HEADLINE_STATES = ("AGGRESS_DOM", "PASSIVE_DOM", "NEITHER")
ALL_STATES = HEADLINE_STATES + ("QUIET",)
QUIET_FLOOR = 0.5
Z_MIN_PERIODS = 20
N_BOOT = 200
EASTERN = "America/New_York"

# Frozen discovery cutoffs from aggression_v1_flow_price_dispersion (h=30).
DEFAULT_REGIME_CUTOFFS_30: dict[str, float] = {
    "horizon": 30.0,
    "trend_eff_q": 0.16666666666666666,
    "range_eff_q": 0.06666666666666667,
    "trend_disp_q": 0.6666666666666666,
    "range_disp_q": 0.3333333333333333,
    "trend_abs_net_q": 2.0,
    "range_abs_net_q": 1.0,
}

GATE_CONFIGS = [
    {"name": "very_loose", "min_n": 20, "min_dates": 3, "min_lift_pp": 2.0, "require_ci": False, "require_bh": False, "require_dates": True},
    {"name": "loose", "min_n": 30, "min_dates": 5, "min_lift_pp": 3.0, "require_ci": True, "require_bh": False, "require_dates": True},
    {"name": "moderate", "min_n": 40, "min_dates": 6, "min_lift_pp": 5.0, "require_ci": True, "require_bh": False, "require_dates": True},
    {"name": "standard", "min_n": 50, "min_dates": 8, "min_lift_pp": 5.0, "require_ci": True, "require_bh": False, "require_dates": True},
    {"name": "strict", "min_n": 50, "min_dates": 8, "min_lift_pp": 8.0, "require_ci": True, "require_bh": False, "require_dates": True},
    {"name": "restrictive", "min_n": 50, "min_dates": 8, "min_lift_pp": 8.0, "require_ci": True, "require_bh": True, "require_dates": True},
]


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


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def load_frozen_cutoffs_30() -> dict[str, float]:
    if FROZEN_CUTOFFS_PATH.exists():
        raw = json.loads(FROZEN_CUTOFFS_PATH.read_text(encoding="utf-8"))
        out = {k: float(v) for k, v in raw.items()}
        out["horizon"] = 30.0
        return out
    return dict(DEFAULT_REGIME_CUTOFFS_30)


def add_aggress_passive_contributions(df: pd.DataFrame) -> pd.DataFrame:
    """Minute aggress (βΔA) and implied-passive (ΔR) contributions + windowed z."""
    out = df.copy()
    beta = pd.to_numeric(out["v1_beta"], errors="coerce")
    aggress = pd.to_numeric(out["total_aggress"], errors="coerce")
    residual = pd.to_numeric(out["v1_residual"], errors="coerce")
    price = pd.to_numeric(out["price"], errors="coerce")

    delta_a = aggress.groupby(out["et_date"], sort=False).diff().fillna(0.0)
    delta_r = residual.groupby(out["et_date"], sort=False).diff()
    delta_p = price.groupby(out["et_date"], sort=False).diff()

    out["contrib_aggress_1m"] = beta * delta_a
    out["contrib_passive_1m"] = delta_r
    out["delta_price_1m"] = delta_p

    for w in WINDOWS:
        c_a = (
            out["contrib_aggress_1m"]
            .groupby(out["et_date"], sort=False)
            .transform(lambda s, ww=w: s.rolling(ww, min_periods=ww).sum())
        )
        c_p = (
            out["contrib_passive_1m"]
            .groupby(out["et_date"], sort=False)
            .transform(lambda s, ww=w: s.rolling(ww, min_periods=ww).sum())
        )
        d_p = (
            out["delta_price_1m"]
            .groupby(out["et_date"], sort=False)
            .transform(lambda s, ww=w: s.rolling(ww, min_periods=ww).sum())
        )
        out[f"contrib_aggress_{w}m"] = c_a
        out[f"contrib_passive_{w}m"] = c_p
        out[f"delta_price_{w}m"] = d_p

        z_a_parts = []
        z_p_parts = []
        for _, day in out.groupby("et_date", sort=False):
            z_a_parts.append(_session_expanding_z(day[f"contrib_aggress_{w}m"], min_periods=Z_MIN_PERIODS))
            z_p_parts.append(_session_expanding_z(day[f"contrib_passive_{w}m"], min_periods=Z_MIN_PERIODS))
        out[f"z_aggress_{w}m"] = pd.concat(z_a_parts).sort_index()
        out[f"z_passive_{w}m"] = pd.concat(z_p_parts).sort_index()
    return out


def _same_sign(a: pd.Series, b: pd.Series) -> pd.Series:
    sa = np.sign(a.fillna(0.0))
    sb = np.sign(b.fillna(0.0))
    return (sa != 0) & (sb != 0) & (sa == sb)


def _diff_sign(a: pd.Series, b: pd.Series) -> pd.Series:
    sa = np.sign(a.fillna(0.0))
    sb = np.sign(b.fillna(0.0))
    return (sa != 0) & (sb != 0) & (sa != sb)


def label_dominance_states(df: pd.DataFrame, window: int, dominance_ratio: float) -> pd.Series:
    """Role/absorption dominance with QUIET as a separate 4th bucket."""
    z_a = pd.to_numeric(df[f"z_aggress_{window}m"], errors="coerce")
    z_p = pd.to_numeric(df[f"z_passive_{window}m"], errors="coerce")
    d_p = pd.to_numeric(df[f"delta_price_{window}m"], errors="coerce")
    abs_a = z_a.abs()
    abs_p = z_p.abs()

    quiet = (abs_a.fillna(0.0) < QUIET_FLOOR) & (abs_p.fillna(0.0) < QUIET_FLOOR)
    activity = (~quiet) & z_a.notna() & z_p.notna() & d_p.notna()

    aggress_dom = activity & _same_sign(z_a, d_p) & (abs_a >= dominance_ratio * abs_p)
    # Passive large vs aggress: fight, or residual leads price without aggress drive.
    passive_mag = activity & (abs_p >= dominance_ratio * abs_a)
    fight = _diff_sign(z_p, z_a)
    residual_leads = _same_sign(z_p, d_p)
    passive_dom = passive_mag & (fight | residual_leads) & (~aggress_dom)

    label = pd.Series("NEITHER", index=df.index, dtype=object)
    label = label.mask(quiet | z_a.isna() | z_p.isna(), "QUIET")
    # Re-apply QUIET only when activity floor fails; NaN z → QUIET-like exclude.
    label = label.mask(quiet, "QUIET")
    label = label.mask(aggress_dom, "AGGRESS_DOM")
    label = label.mask(passive_dom, "PASSIVE_DOM")
    # Activity-met but neither role rule → NEITHER (already default for those rows)
    label = label.mask(activity & (~aggress_dom) & (~passive_dom), "NEITHER")
    label = label.mask(~activity & ~quiet, "QUIET")
    return label


def apply_path_regime_labels(df: pd.DataFrame, cutoffs_by_h: dict[int, dict[str, float]]) -> pd.DataFrame:
    out = df.copy()
    for h, cutoffs in cutoffs_by_h.items():
        eff = out[f"fwd_path_eff_{h}m"]
        disp = out[f"fwd_disp_to_range_{h}m"]
        abs_net = out[f"fwd_abs_net_ticks_{h}m"]
        net = out[f"fwd_net_ticks_{h}m"]
        trend = (
            (eff >= cutoffs["trend_eff_q"])
            & (disp >= cutoffs["trend_disp_q"])
            & (abs_net >= cutoffs["trend_abs_net_q"])
        )
        rng = (
            (eff <= cutoffs["range_eff_q"])
            & (disp <= cutoffs["range_disp_q"])
            & (abs_net <= cutoffs["range_abs_net_q"])
        )
        label = pd.Series("AMBIGUOUS", index=out.index, dtype=object)
        label = label.mask(rng, "RANGE")
        label = label.mask(trend & (net > 0), "TREND_UP")
        label = label.mask(trend & (net < 0), "TREND_DOWN")
        label = label.mask(eff.isna() | disp.isna() | abs_net.isna(), np.nan)
        out[f"future_regime_{h}m"] = label
        out[f"future_is_range_{h}m"] = label == "RANGE"
        out[f"future_is_trend_{h}m"] = label.isin(["TREND_UP", "TREND_DOWN"])
    return out


def evaluate_cell(
    episodes: pd.DataFrame,
    *,
    cell_id: str,
    split_name: str,
    outcome_col: str,
    baseline_rate: float,
    window: int,
    dominance_ratio: float,
    horizon: int,
    state: str,
    outcome: str,
    family: str,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "cell_id": cell_id,
        "family": family,
        "split": split_name,
        "window_m": window,
        "dominance_ratio": dominance_ratio,
        "horizon_m": horizon,
        "state": state,
        "outcome": outcome,
        "n": 0,
        "n_dates": 0,
        "rate": np.nan,
        "baseline_rate": baseline_rate,
        "lift_pp": np.nan,
        "ci_low": np.nan,
        "ci_high": np.nan,
        "one_sided_lower": np.nan,
        "p_one_sided": np.nan,
        "trend_up_share": np.nan,
        "mean_abs_net_ticks": np.nan,
        "mean_range_ticks": np.nan,
    }
    if episodes.empty or outcome_col not in episodes:
        return base
    y = episodes[outcome_col].astype(float)
    n = int(len(episodes))
    base["n"] = n
    base["n_dates"] = int(episodes["et_date"].nunique())
    rate = float(y.mean()) if n else np.nan
    base["rate"] = rate
    lift = rate - baseline_rate if np.isfinite(rate) and np.isfinite(baseline_rate) else np.nan
    base["lift_pp"] = float(lift * 100.0) if np.isfinite(lift) else np.nan
    net_col = f"fwd_abs_net_ticks_{horizon}m"
    rng_col = f"fwd_range_ticks_{horizon}m"
    if net_col in episodes:
        base["mean_abs_net_ticks"] = float(pd.to_numeric(episodes[net_col], errors="coerce").mean())
    if rng_col in episodes:
        base["mean_range_ticks"] = float(pd.to_numeric(episodes[rng_col], errors="coerce").mean())
    regime_col = f"future_regime_{horizon}m"
    if outcome == "TREND" and regime_col in episodes.columns:
        trend_eps = episodes[episodes[outcome_col].astype(bool)]
        if len(trend_eps) >= 5:
            base["trend_up_share"] = float((trend_eps[regime_col] == "TREND_UP").mean())
    if n < 8 or split_name != "validation":
        return base
    groups = episodes["et_date"].astype(str).tolist()
    lift_values = (y - baseline_rate).to_numpy()
    lift_ci = bootstrap_ci(
        lift_values,
        n_boot=N_BOOT,
        seed=(abs(hash(cell_id)) + 17) % (2**31 - 1),
        groups=groups,
    )
    base["ci_low"] = float(lift_ci["ci_low"]) if lift_ci["n"] else np.nan
    base["ci_high"] = float(lift_ci["ci_high"]) if lift_ci["n"] else np.nan
    base["one_sided_lower"] = float(lift_ci["one_sided_lower"]) if lift_ci["n"] else np.nan
    base["p_one_sided"] = float(one_sided_p(lift_values))
    return base


def state_mix_rows(
    panel: pd.DataFrame,
    dates: list[str],
    split_name: str,
    window: int,
    dominance_ratio: float,
) -> list[dict[str, Any]]:
    col = f"dom_state_w{window}_d{dominance_ratio}"
    sub = panel[panel["et_date"].isin(set(dates)) & ~panel["event_near"].fillna(False)]
    if col not in sub.columns or sub.empty:
        return []
    counts = sub[col].value_counts(dropna=False).to_dict()
    n = int(len(sub))
    rows = []
    for state in ALL_STATES:
        c = int(counts.get(state, 0))
        rows.append(
            {
                "split": split_name,
                "window_m": window,
                "dominance_ratio": dominance_ratio,
                "state": state,
                "n_rows": c,
                "n_dates": int(sub.loc[sub[col] == state, "et_date"].nunique()) if c else 0,
                "share": float(c / n) if n else np.nan,
                "universe_n": n,
            }
        )
    return rows


def score_path_split(
    panel: pd.DataFrame,
    dates: list[str],
    split_name: str,
) -> list[dict[str, Any]]:
    nonevent = panel[panel["et_date"].isin(set(dates)) & ~panel["event_near"].fillna(False)].copy()
    journal: list[dict[str, Any]] = []
    for w in WINDOWS:
        for d in DOMINANCE_RATIOS:
            state_col = f"dom_state_w{w}_d{d}"
            for h in HORIZONS:
                range_col = f"future_is_range_{h}m"
                trend_col = f"future_is_trend_{h}m"
                if range_col not in nonevent.columns:
                    continue
                labeled = nonevent.dropna(subset=[f"future_regime_{h}m"]).copy()
                if labeled.empty:
                    continue
                base_range = float(labeled[range_col].astype(float).mean())
                base_trend = float(labeled[trend_col].astype(float).mean())
                for state in ALL_STATES:
                    mask = labeled[state_col] == state
                    eps = fast_decluster(labeled, mask, stride_min=EPISODE_STRIDE_MIN)
                    family = "primary_3way" if state in HEADLINE_STATES else "quiet_diagnostic"
                    for outcome, col, base_rate in (
                        ("RANGE", range_col, base_range),
                        ("TREND", trend_col, base_trend),
                    ):
                        journal.append(
                            evaluate_cell(
                                eps,
                                cell_id=f"w{w}_d{d}_h{h}_{state}_{outcome}",
                                split_name=split_name,
                                outcome_col=col,
                                baseline_rate=base_rate,
                                window=w,
                                dominance_ratio=d,
                                horizon=h,
                                state=state,
                                outcome=outcome,
                                family=family,
                            )
                        )
    return journal


def score_atr_diagnostic(
    panel: pd.DataFrame,
    dates: list[str],
    split_name: str,
) -> list[dict[str, Any]]:
    """Secondary ATR CONTINUED_RANGE vs BREAKOUT — diagnostic only."""
    nonevent = panel[panel["et_date"].isin(set(dates)) & ~panel["event_near"].fillna(False)].copy()
    journal: list[dict[str, Any]] = []
    # Headline dominance config only for ATR diagnostic to limit cell count.
    w = PRIMARY["w"]
    d = PRIMARY["d"]
    state_col = f"dom_state_w{w}_d{d}"
    for h in HORIZONS:
        for B in ATR_BPS_GRID:
            for k in ATR_K_GRID:
                labeled = label_atr_config(nonevent, h, float(B), float(k))
                base_bo = float(labeled["future_is_breakout"].mean())
                base_cr = float(labeled["future_is_continued_range"].mean())
                for state in ALL_STATES:
                    mask = labeled[state_col] == state
                    eps = fast_decluster(labeled, mask, stride_min=EPISODE_STRIDE_MIN)
                    family = "atr_diagnostic"
                    for outcome, col, base_rate in (
                        ("BREAKOUT", "future_is_breakout", base_bo),
                        ("CONTINUED_RANGE", "future_is_continued_range", base_cr),
                    ):
                        row = evaluate_cell(
                            eps,
                            cell_id=f"atr_w{w}_d{d}_h{h}_B{B}_k{k}_{state}_{outcome}",
                            split_name=split_name,
                            outcome_col=col,
                            baseline_rate=base_rate,
                            window=w,
                            dominance_ratio=d,
                            horizon=h,
                            state=state,
                            outcome=outcome,
                            family=family,
                        )
                        row["breakout_bps"] = float(B)
                        row["k"] = float(k)
                        journal.append(row)
    return journal


def add_fdr(val: pd.DataFrame, group_keys: list[str]) -> pd.DataFrame:
    out = val.copy()
    out["bh_adjusted_p"] = np.nan
    out["bh_significant"] = False
    if out.empty:
        return out
    pieces = []
    for _, part in out.groupby(group_keys, sort=False):
        p = part.copy()
        mask = p["p_one_sided"].notna()
        if mask.any():
            bh = benjamini_hochberg(p.loc[mask, "p_one_sided"].to_numpy(dtype=float), q=0.10)
            p.loc[mask, "bh_adjusted_p"] = bh["adjusted_p"]
            p.loc[mask, "bh_significant"] = bh["reject"]
        pieces.append(p)
    return pd.concat(pieces, ignore_index=True)


def gate_sweep(val: pd.DataFrame, out_dir: Path, prefix: str) -> pd.DataFrame:
    gate_dir = out_dir / "gate_threshold_sweep"
    gate_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for cfg in GATE_CONFIGS:
        g = val.copy()
        g["gate"] = g.apply(lambda r, c=cfg: apply_gate(r, c), axis=1)
        counts = g["gate"].value_counts().to_dict()
        summary.append(
            {
                "board": prefix,
                **cfg,
                "n_PASS": int(counts.get("PASS", 0)),
                "n_PROVISIONAL": int(counts.get("PROVISIONAL", 0)),
                "n_FAIL": int(counts.get("FAIL", 0)),
            }
        )
        g.sort_values("lift_pp", ascending=False).to_csv(
            gate_dir / f"{prefix}_validation_{cfg['name']}.csv", index=False
        )
    return pd.DataFrame(summary)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading features...", flush=True)
    features, meta = load_features(FEATURE_MATRIX)
    features["event_near"] = _near_event(features.index, _event_times(EVENTS_DB))
    splits = chronological_splits(meta["clean_dates"])

    print("Fitting causal V1 residual...", flush=True)
    panel = fit_causal_v1_residual(features)
    print("Building aggress/passive contributions + dominance states...", flush=True)
    panel = add_aggress_passive_contributions(panel)
    for w in WINDOWS:
        for d in DOMINANCE_RATIOS:
            panel[f"dom_state_w{w}_d{d}"] = label_dominance_states(panel, w, float(d))

    print("Adding path-efficiency forward outcomes...", flush=True)
    panel = add_forward_regime_outcomes(panel)

    cutoffs_30 = load_frozen_cutoffs_30()
    cutoffs_by_h: dict[int, dict[str, float]] = {30: cutoffs_30}
    for h in HORIZONS:
        if h == 30:
            continue
        learned = learn_regime_cutoffs(panel, splits["discovery"], horizon=h)
        cutoffs_by_h[h] = learned
    _write_json(OUTPUT_DIR / "regime_cutoffs_by_horizon.json", cutoffs_by_h)
    panel = apply_path_regime_labels(panel, cutoffs_by_h)

    print("Adding ATR forward metrics (diagnostic)...", flush=True)
    panel = add_atr_forward_metrics(panel)

    mix_rows: list[dict[str, Any]] = []
    for split_name in ("discovery", "validation"):
        for w in WINDOWS:
            for d in DOMINANCE_RATIOS:
                mix_rows.extend(state_mix_rows(panel, splits[split_name], split_name, w, float(d)))
    mix_df = pd.DataFrame(mix_rows)
    mix_df.to_csv(OUTPUT_DIR / "state_mix_grid.csv", index=False)

    journal: list[dict[str, Any]] = []
    atr_journal: list[dict[str, Any]] = []
    for split_name in ("discovery", "validation"):
        print(f"Scoring path RANGE/TREND on {split_name}...", flush=True)
        journal.extend(score_path_split(panel, splits[split_name], split_name))
        print(f"Scoring ATR diagnostic on {split_name}...", flush=True)
        atr_journal.extend(score_atr_diagnostic(panel, splits[split_name], split_name))

    journal_df = pd.DataFrame(journal)
    atr_df = pd.DataFrame(atr_journal)
    journal_df.to_csv(OUTPUT_DIR / "candidate_journal.csv", index=False)
    atr_df.to_csv(OUTPUT_DIR / "atr_diagnostic_journal.csv", index=False)

    val = journal_df[journal_df["split"] == "validation"].copy()
    # BH within (horizon, outcome) for primary 3-way; QUIET scored separately in family.
    val_primary = val[val["family"] == "primary_3way"].copy()
    val_quiet = val[val["family"] == "quiet_diagnostic"].copy()
    val_primary = add_fdr(val_primary, ["horizon_m", "outcome"])
    val_quiet = add_fdr(val_quiet, ["horizon_m", "outcome"])
    val = pd.concat([val_primary, val_quiet], ignore_index=True)
    val.sort_values("lift_pp", ascending=False).to_csv(OUTPUT_DIR / "validation_leaderboard.csv", index=False)

    atr_val = atr_df[atr_df["split"] == "validation"].copy()
    atr_val = add_fdr(atr_val, ["horizon_m", "breakout_bps", "k", "outcome"])
    atr_val.sort_values("lift_pp", ascending=False).to_csv(
        OUTPUT_DIR / "atr_diagnostic_leaderboard.csv", index=False
    )

    gate_path = gate_sweep(val_primary, OUTPUT_DIR, "path")
    gate_atr = gate_sweep(atr_val, OUTPUT_DIR, "atr")
    gate_summary = pd.concat([gate_path, gate_atr], ignore_index=True)
    gate_summary.to_csv(OUTPUT_DIR / "gate_threshold_sweep" / "gate_config_summary.csv", index=False)

    cfg = next(c for c in GATE_CONFIGS if c["name"] == "restrictive")
    g = val_primary.copy()
    g["gate"] = g.apply(lambda r, c=cfg: apply_gate(r, c), axis=1)
    restrictive_pass = g[g["gate"] == "PASS"]

    primary_cells = val_primary[
        (val_primary["window_m"] == PRIMARY["w"])
        & (val_primary["dominance_ratio"] == PRIMARY["d"])
        & (val_primary["horizon_m"] == PRIMARY["h"])
    ].sort_values("lift_pp", ascending=False)

    # Optional FINAL peek only if restrictive PASS exists.
    final_rows: list[dict[str, Any]] = []
    if not restrictive_pass.empty and splits.get("final"):
        print("Primary restrictive PASS found — scoring FINAL split...", flush=True)
        final_rows = score_path_split(panel, splits["final"], "final")
        pd.DataFrame(final_rows).to_csv(OUTPUT_DIR / "final_journal.csv", index=False)
    else:
        print("No primary restrictive PASS — FINAL locked.", flush=True)

    disc = journal_df[journal_df["split"] == "discovery"]
    summary = {
        "run_date": "2026-07-17",
        "definition": "role_absorption_B",
        "quiet": "separate_4th_bucket",
        "primary": PRIMARY,
        "windows": list(WINDOWS),
        "dominance_ratios": list(DOMINANCE_RATIOS),
        "horizons": list(HORIZONS),
        "splits": {k: list(v) for k, v in splits.items()},
        "n_clean_dates": int(meta["dates_clean"]),
        "n_clean_rows": int(meta["rows_clean"]),
        "regime_cutoffs_by_horizon": cutoffs_by_h,
        "path_journal_rows": int(len(journal_df)),
        "atr_journal_rows": int(len(atr_df)),
        "validation_primary_cells": int(len(val_primary)),
        "restrictive_pass_n": int(len(restrictive_pass)),
        "restrictive_pass_cells": restrictive_pass[["cell_id", "state", "outcome", "lift_pp", "n", "n_dates"]].to_dict(
            orient="records"
        )
        if not restrictive_pass.empty
        else [],
        "primary_headline_validation": primary_cells[
            ["cell_id", "state", "outcome", "n", "n_dates", "rate", "baseline_rate", "lift_pp", "one_sided_lower", "bh_significant"]
        ].to_dict(orient="records")
        if not primary_cells.empty
        else [],
        "gate_summary": gate_summary.to_dict(orient="records"),
        "final_opened": bool(final_rows),
        "discovery_top5": disc.sort_values("lift_pp", ascending=False)
        .head(5)[["cell_id", "state", "outcome", "lift_pp", "n"]]
        .to_dict(orient="records")
        if not disc.empty
        else [],
        "guardrail": "offline_only_no_v12_retune_without_primary_pass_and_approval",
    }
    _write_json(OUTPUT_DIR / "summary.json", summary)

    print("\n=== Primary headline (w=15, d=1.5, h=30) validation ===", flush=True)
    if primary_cells.empty:
        print("(empty)", flush=True)
    else:
        print(primary_cells[["state", "outcome", "n", "lift_pp", "bh_significant"]].to_string(index=False), flush=True)
    print(f"\nRestrictive PASS count (primary 3-way): {len(restrictive_pass)}", flush=True)
    if not restrictive_pass.empty:
        print(restrictive_pass[["cell_id", "lift_pp", "n"]].head(20).to_string(index=False), flush=True)
    print(f"Wrote artifacts under {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
