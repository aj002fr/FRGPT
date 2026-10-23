#!/usr/bin/env python3
"""Research-only aggress/passive dominance parameter sweep (staged).

Expands lookbacks, dominance ratios, QUIET floors, forward horizons, and
RANGE/TREND cutoff packs under the locked role/absorption definition.

Writes only under workspace/analysis/aggression_v1_aggress_passive_dominance_param_sweep/.
Does not overwrite the baseline dominance artifacts or retune V1.2.
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
from scripts.aggression_v1_aggress_passive_dominance_research import (  # noqa: E402
    DEFAULT_REGIME_CUTOFFS_30,
    FROZEN_CUTOFFS_PATH,
    GATE_CONFIGS,
    _diff_sign,
    _json_default,
    _same_sign,
    _write_json,
    load_frozen_cutoffs_30,
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
    chronological_splits,
    fit_causal_v1_residual,
    load_features,
)

OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v1_aggress_passive_dominance_param_sweep"

WINDOWS = (1, 3, 5, 10, 15, 30)
DOMINANCE_RATIOS = (1.1, 1.25, 1.5, 2.0, 2.5, 3.0)
QUIET_FLOORS = (0.25, 0.5, 0.75)
HORIZONS = (5, 10, 15, 30, 60, 90)
CUTOFF_PACKS = ("frozen", "strict", "loose")
STAGE1_HORIZON = 30
STAGE1_PACK = "frozen"

ATR_BPS_GRID = (4, 6, 8)
ATR_K_GRID = (0.5, 1.0, 1.5)
ATR_W = (5, 15)
ATR_D = (1.5, 2.0)
ATR_Q = 0.5

HEADLINE = {"w": 15, "d": 1.5, "q": 0.5, "h": 30, "pack": "frozen"}
HEADLINE_STATES = ("AGGRESS_DOM", "PASSIVE_DOM", "NEITHER")
ALL_STATES = HEADLINE_STATES + ("QUIET",)
Z_MIN_PERIODS = 20
N_BOOT = 200
EPS = 1e-12

PACK_QUANTILES = {
    # trend: path_eff, disp, abs_net ; range: path_eff, disp, abs_net
    "strict": {"trend": (0.80, 0.80, 0.70), "range": (0.20, 0.20, 0.30)},
    "loose": {"trend": (0.60, 0.60, 0.50), "range": (0.40, 0.40, 0.50)},
}


def _fmt(x: float | int) -> str:
    return f"{float(x):g}"


def state_col(w: int, d: float, q: float) -> str:
    return f"dom_state_w{w}_d{_fmt(d)}_q{_fmt(q)}"


def add_forward_path_outcomes(df: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    parts = []
    for _, day in df.groupby("et_date", sort=False):
        d = day.sort_index().copy()
        price = d["price"].astype(float)
        abs_diff = price.diff().abs()
        for h in horizons:
            future = price.shift(-h)
            net = (future - price) / TICK_SIZE
            fwd_path = abs_diff.shift(-1).rolling(h, min_periods=h).sum().shift(-(h - 1)) / TICK_SIZE
            fwd_high = price.iloc[::-1].rolling(h + 1, min_periods=h + 1).max().iloc[::-1]
            fwd_low = price.iloc[::-1].rolling(h + 1, min_periods=h + 1).min().iloc[::-1]
            fwd_range = (fwd_high - fwd_low) / TICK_SIZE
            d[f"fwd_net_ticks_{h}m"] = net
            d[f"fwd_abs_net_ticks_{h}m"] = net.abs()
            d[f"fwd_path_ticks_{h}m"] = fwd_path
            d[f"fwd_range_ticks_{h}m"] = fwd_range
            d[f"fwd_path_eff_{h}m"] = d[f"fwd_abs_net_ticks_{h}m"] / d[f"fwd_path_ticks_{h}m"].clip(lower=EPS)
            d[f"fwd_disp_to_range_{h}m"] = d[f"fwd_abs_net_ticks_{h}m"] / d[f"fwd_range_ticks_{h}m"].clip(lower=EPS)
            if len(d) >= h:
                cols = [
                    f"fwd_net_ticks_{h}m",
                    f"fwd_abs_net_ticks_{h}m",
                    f"fwd_path_ticks_{h}m",
                    f"fwd_range_ticks_{h}m",
                    f"fwd_path_eff_{h}m",
                    f"fwd_disp_to_range_{h}m",
                ]
                d.loc[d.index[-h]:, cols] = np.nan
        parts.append(d)
    return pd.concat(parts).sort_index()


def add_aggress_passive_contributions(df: pd.DataFrame, windows: tuple[int, ...] = WINDOWS) -> pd.DataFrame:
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

    for w in windows:
        c_a = out["contrib_aggress_1m"].groupby(out["et_date"], sort=False).transform(
            lambda s, ww=w: s.rolling(ww, min_periods=ww).sum()
        )
        c_p = out["contrib_passive_1m"].groupby(out["et_date"], sort=False).transform(
            lambda s, ww=w: s.rolling(ww, min_periods=ww).sum()
        )
        d_p = out["delta_price_1m"].groupby(out["et_date"], sort=False).transform(
            lambda s, ww=w: s.rolling(ww, min_periods=ww).sum()
        )
        out[f"contrib_aggress_{w}m"] = c_a
        out[f"contrib_passive_{w}m"] = c_p
        out[f"delta_price_{w}m"] = d_p
        z_a_parts, z_p_parts = [], []
        for _, day in out.groupby("et_date", sort=False):
            z_a_parts.append(_session_expanding_z(day[f"contrib_aggress_{w}m"], min_periods=Z_MIN_PERIODS))
            z_p_parts.append(_session_expanding_z(day[f"contrib_passive_{w}m"], min_periods=Z_MIN_PERIODS))
        out[f"z_aggress_{w}m"] = pd.concat(z_a_parts).sort_index()
        out[f"z_passive_{w}m"] = pd.concat(z_p_parts).sort_index()
    return out


def label_dominance_states(
    df: pd.DataFrame,
    window: int,
    dominance_ratio: float,
    quiet_floor: float,
) -> pd.Series:
    z_a = pd.to_numeric(df[f"z_aggress_{window}m"], errors="coerce")
    z_p = pd.to_numeric(df[f"z_passive_{window}m"], errors="coerce")
    d_p = pd.to_numeric(df[f"delta_price_{window}m"], errors="coerce")
    abs_a = z_a.abs()
    abs_p = z_p.abs()

    quiet = (abs_a.fillna(0.0) < quiet_floor) & (abs_p.fillna(0.0) < quiet_floor)
    activity = (~quiet) & z_a.notna() & z_p.notna() & d_p.notna()

    aggress_dom = activity & _same_sign(z_a, d_p) & (abs_a >= dominance_ratio * abs_p)
    passive_mag = activity & (abs_p >= dominance_ratio * abs_a)
    fight = _diff_sign(z_p, z_a)
    residual_leads = _same_sign(z_p, d_p)
    passive_dom = passive_mag & (fight | residual_leads) & (~aggress_dom)

    label = pd.Series("NEITHER", index=df.index, dtype=object)
    label = label.mask(quiet, "QUIET")
    label = label.mask(aggress_dom, "AGGRESS_DOM")
    label = label.mask(passive_dom, "PASSIVE_DOM")
    label = label.mask(activity & (~aggress_dom) & (~passive_dom), "NEITHER")
    label = label.mask(~activity & ~quiet, "QUIET")
    return label


def learn_cutoff_pack(
    df: pd.DataFrame,
    discovery_dates: list[str],
    horizon: int,
    pack: str,
) -> dict[str, float]:
    if pack == "frozen" and horizon == 30:
        cut = load_frozen_cutoffs_30()
        cut["horizon"] = 30.0
        cut["pack"] = "frozen"
        return cut

    disc = df[df["et_date"].isin(set(discovery_dates))].dropna(
        subset=[f"fwd_path_eff_{horizon}m", f"fwd_disp_to_range_{horizon}m", f"fwd_abs_net_ticks_{horizon}m"]
    )
    eff = disc[f"fwd_path_eff_{horizon}m"]
    disp = disc[f"fwd_disp_to_range_{horizon}m"]
    abs_net = disc[f"fwd_abs_net_ticks_{horizon}m"]

    if pack == "frozen":
        # Same quantile recipe as learn_regime_cutoffs (0.70/0.30/0.70/0.30/0.60/0.40).
        t_e, t_d, t_n = 0.70, 0.70, 0.60
        r_e, r_d, r_n = 0.30, 0.30, 0.40
    else:
        t_e, t_d, t_n = PACK_QUANTILES[pack]["trend"]
        r_e, r_d, r_n = PACK_QUANTILES[pack]["range"]

    return {
        "horizon": float(horizon),
        "pack": pack,
        "trend_eff_q": float(eff.quantile(t_e)),
        "range_eff_q": float(eff.quantile(r_e)),
        "trend_disp_q": float(disp.quantile(t_d)),
        "range_disp_q": float(disp.quantile(r_d)),
        "trend_abs_net_q": float(abs_net.quantile(t_n)),
        "range_abs_net_q": float(abs_net.quantile(r_n)),
    }


def apply_pack_labels(
    df: pd.DataFrame,
    cutoffs_by_pack_h: dict[tuple[str, int], dict[str, float]],
) -> pd.DataFrame:
    out = df.copy()
    for (pack, h), cutoffs in cutoffs_by_pack_h.items():
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
        prefix = f"{pack}_{h}m"
        out[f"future_regime_{prefix}"] = label
        out[f"future_is_range_{prefix}"] = label == "RANGE"
        out[f"future_is_trend_{prefix}"] = label.isin(["TREND_UP", "TREND_DOWN"])
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
    quiet_floor: float,
    horizon: int,
    pack: str,
    state: str,
    outcome: str,
    family: str,
    stage: str,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "cell_id": cell_id,
        "stage": stage,
        "family": family,
        "split": split_name,
        "window_m": window,
        "dominance_ratio": dominance_ratio,
        "quiet_floor": quiet_floor,
        "horizon_m": horizon,
        "cutoff_pack": pack,
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
    if n < 8 or split_name != "validation":
        return base
    groups = episodes["et_date"].astype(str).tolist()
    lift_values = (y - baseline_rate).to_numpy()
    lift_ci = bootstrap_ci(
        lift_values,
        n_boot=N_BOOT,
        seed=(abs(hash(cell_id)) + 29) % (2**31 - 1),
        groups=groups,
    )
    base["ci_low"] = float(lift_ci["ci_low"]) if lift_ci["n"] else np.nan
    base["ci_high"] = float(lift_ci["ci_high"]) if lift_ci["n"] else np.nan
    base["one_sided_lower"] = float(lift_ci["one_sided_lower"]) if lift_ci["n"] else np.nan
    base["p_one_sided"] = float(one_sided_p(lift_values))
    return base


def score_path_grid(
    panel: pd.DataFrame,
    dates: list[str],
    split_name: str,
    *,
    windows: tuple[int, ...],
    ratios: tuple[float, ...],
    quiet_floors: tuple[float, ...],
    horizons: tuple[int, ...],
    packs: tuple[str, ...],
    stage: str,
) -> list[dict[str, Any]]:
    nonevent = panel[panel["et_date"].isin(set(dates)) & ~panel["event_near"].fillna(False)]
    journal: list[dict[str, Any]] = []
    total = len(windows) * len(ratios) * len(quiet_floors) * len(horizons) * len(packs)
    done = 0
    for w in windows:
        for d in ratios:
            for q in quiet_floors:
                scol = state_col(w, d, q)
                if scol not in nonevent.columns:
                    done += len(horizons) * len(packs)
                    continue
                for h in horizons:
                    for pack in packs:
                        done += 1
                        prefix = f"{pack}_{h}m"
                        regime_col = f"future_regime_{prefix}"
                        range_col = f"future_is_range_{prefix}"
                        trend_col = f"future_is_trend_{prefix}"
                        if regime_col not in nonevent.columns:
                            continue
                        labeled = nonevent.dropna(subset=[regime_col])
                        if labeled.empty:
                            continue
                        base_range = float(labeled[range_col].astype(float).mean())
                        base_trend = float(labeled[trend_col].astype(float).mean())
                        for state in ALL_STATES:
                            mask = labeled[scol] == state
                            eps = fast_decluster(labeled, mask, stride_min=EPISODE_STRIDE_MIN)
                            family = "primary_3way" if state in HEADLINE_STATES else "quiet_diagnostic"
                            for outcome, col, base_rate in (
                                ("RANGE", range_col, base_range),
                                ("TREND", trend_col, base_trend),
                            ):
                                journal.append(
                                    evaluate_cell(
                                        eps,
                                        cell_id=f"{stage}_w{w}_d{_fmt(d)}_q{_fmt(q)}_h{h}_{pack}_{state}_{outcome}",
                                        split_name=split_name,
                                        outcome_col=col,
                                        baseline_rate=base_rate,
                                        window=w,
                                        dominance_ratio=float(d),
                                        quiet_floor=float(q),
                                        horizon=h,
                                        pack=pack,
                                        state=state,
                                        outcome=outcome,
                                        family=family,
                                        stage=stage,
                                    )
                                )
                        if done % 50 == 0 or done == total:
                            print(
                                f"  {stage}/{split_name} geometry-horizon-pack {done}/{total}; journal={len(journal)}",
                                flush=True,
                            )
    return journal


def state_mix_rows(
    panel: pd.DataFrame,
    dates: list[str],
    split_name: str,
) -> list[dict[str, Any]]:
    sub = panel[panel["et_date"].isin(set(dates)) & ~panel["event_near"].fillna(False)]
    rows: list[dict[str, Any]] = []
    n = int(len(sub))
    for w in WINDOWS:
        for d in DOMINANCE_RATIOS:
            for q in QUIET_FLOORS:
                col = state_col(w, d, q)
                if col not in sub.columns:
                    continue
                counts = sub[col].value_counts(dropna=False).to_dict()
                for state in ALL_STATES:
                    c = int(counts.get(state, 0))
                    rows.append(
                        {
                            "split": split_name,
                            "window_m": w,
                            "dominance_ratio": float(d),
                            "quiet_floor": float(q),
                            "state": state,
                            "n_rows": c,
                            "n_dates": int(sub.loc[sub[col] == state, "et_date"].nunique()) if c else 0,
                            "share": float(c / n) if n else np.nan,
                            "universe_n": n,
                        }
                    )
    return rows


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


def score_atr_diagnostic(
    panel: pd.DataFrame,
    dates: list[str],
    split_name: str,
    geometries: list[tuple[int, float, float]],
) -> list[dict[str, Any]]:
    nonevent = panel[panel["et_date"].isin(set(dates)) & ~panel["event_near"].fillna(False)].copy()
    journal: list[dict[str, Any]] = []
    atr_horizons = (15, 30, 60)
    for w, d, q in geometries:
        scol = state_col(w, d, q)
        if scol not in nonevent.columns:
            continue
        for h in atr_horizons:
            for B in ATR_BPS_GRID:
                for k in ATR_K_GRID:
                    labeled = label_atr_config(nonevent, h, float(B), float(k))
                    base_bo = float(labeled["future_is_breakout"].mean())
                    base_cr = float(labeled["future_is_continued_range"].mean())
                    for state in ALL_STATES:
                        mask = labeled[scol] == state
                        eps = fast_decluster(labeled, mask, stride_min=EPISODE_STRIDE_MIN)
                        for outcome, col, base_rate in (
                            ("BREAKOUT", "future_is_breakout", base_bo),
                            ("CONTINUED_RANGE", "future_is_continued_range", base_cr),
                        ):
                            row = evaluate_cell(
                                eps,
                                cell_id=(
                                    f"atr_w{w}_d{_fmt(d)}_q{_fmt(q)}_h{h}_B{B}_k{k}_{state}_{outcome}"
                                ),
                                split_name=split_name,
                                outcome_col=col,
                                baseline_rate=base_rate,
                                window=w,
                                dominance_ratio=float(d),
                                quiet_floor=float(q),
                                horizon=h,
                                pack="atr",
                                state=state,
                                outcome=outcome,
                                family="atr_diagnostic",
                                stage="stage3",
                            )
                            row["breakout_bps"] = float(B)
                            row["k"] = float(k)
                            journal.append(row)
    return journal


def pick_stage3_geometries(stage1_val: pd.DataFrame) -> list[tuple[int, float, float]]:
    """Top 10 Stage-1 primary geometries by validation lift under very_loose, else headline."""
    cfg = next(c for c in GATE_CONFIGS if c["name"] == "very_loose")
    primary = stage1_val[stage1_val["family"] == "primary_3way"].copy()
    if primary.empty:
        return [(HEADLINE["w"], HEADLINE["d"], HEADLINE["q"])]
    primary["gate"] = primary.apply(lambda r, c=cfg: apply_gate(r, c), axis=1)
    ranked = (
        primary.sort_values("lift_pp", ascending=False)
        .drop_duplicates(subset=["window_m", "dominance_ratio", "quiet_floor"])
        .head(10)
    )
    geos = [
        (int(r.window_m), float(r.dominance_ratio), float(r.quiet_floor))
        for r in ranked.itertuples(index=False)
    ]
    headline = (HEADLINE["w"], HEADLINE["d"], HEADLINE["q"])
    if headline not in geos:
        geos = [headline] + geos
    # Also include plan-reduced ATR slice defaults.
    for w in ATR_W:
        for d in ATR_D:
            g = (w, float(d), float(ATR_Q))
            if g not in geos:
                geos.append(g)
    return geos[:12]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading features...", flush=True)
    features, meta = load_features(FEATURE_MATRIX)
    features["event_near"] = _near_event(features.index, _event_times(EVENTS_DB))
    splits = chronological_splits(meta["clean_dates"])

    print("Fitting causal V1 residual...", flush=True)
    panel = fit_causal_v1_residual(features)
    print("Building contributions for expanded windows...", flush=True)
    panel = add_aggress_passive_contributions(panel, WINDOWS)

    print("Labeling dominance states (w x d x q)...", flush=True)
    for w in WINDOWS:
        for d in DOMINANCE_RATIOS:
            for q in QUIET_FLOORS:
                panel[state_col(w, d, q)] = label_dominance_states(panel, w, float(d), float(q))

    print("Adding forward path outcomes...", flush=True)
    panel = add_forward_path_outcomes(panel, HORIZONS)

    print("Learning cutoff packs on discovery...", flush=True)
    cutoffs: dict[tuple[str, int], dict[str, float]] = {}
    for pack in CUTOFF_PACKS:
        for h in HORIZONS:
            cutoffs[(pack, h)] = learn_cutoff_pack(panel, splits["discovery"], h, pack)
    _write_json(
        OUTPUT_DIR / "regime_cutoffs_by_pack_horizon.json",
        {f"{pack}_h{h}": v for (pack, h), v in cutoffs.items()},
    )
    panel = apply_pack_labels(panel, cutoffs)

    mix_rows: list[dict[str, Any]] = []
    for split_name in ("discovery", "validation"):
        mix_rows.extend(state_mix_rows(panel, splits[split_name], split_name))
    pd.DataFrame(mix_rows).to_csv(OUTPUT_DIR / "state_mix_grid.csv", index=False)

    # ---- Stage 1: geometry @ h=30 frozen ----
    stage1: list[dict[str, Any]] = []
    for split_name in ("discovery", "validation"):
        print(f"Stage 1 scoring {split_name}...", flush=True)
        stage1.extend(
            score_path_grid(
                panel,
                splits[split_name],
                split_name,
                windows=WINDOWS,
                ratios=DOMINANCE_RATIOS,
                quiet_floors=QUIET_FLOORS,
                horizons=(STAGE1_HORIZON,),
                packs=(STAGE1_PACK,),
                stage="stage1",
            )
        )
    stage1_df = pd.DataFrame(stage1)
    stage1_df.to_csv(OUTPUT_DIR / "stage1_geometry_journal.csv", index=False)

    # ---- Stage 2: full horizons x packs ----
    stage2: list[dict[str, Any]] = []
    for split_name in ("discovery", "validation"):
        print(f"Stage 2 scoring {split_name}...", flush=True)
        stage2.extend(
            score_path_grid(
                panel,
                splits[split_name],
                split_name,
                windows=WINDOWS,
                ratios=DOMINANCE_RATIOS,
                quiet_floors=QUIET_FLOORS,
                horizons=HORIZONS,
                packs=CUTOFF_PACKS,
                stage="stage2",
            )
        )
    stage2_df = pd.DataFrame(stage2)
    stage2_df.to_csv(OUTPUT_DIR / "stage2_path_journal.csv", index=False)

    # Validation FDR + gates on Stage 2 primary
    val2 = stage2_df[stage2_df["split"] == "validation"].copy()
    val2_primary = val2[val2["family"] == "primary_3way"].copy()
    val2_quiet = val2[val2["family"] == "quiet_diagnostic"].copy()
    val2_primary = add_fdr(val2_primary, ["horizon_m", "outcome", "cutoff_pack"])
    val2_quiet = add_fdr(val2_quiet, ["horizon_m", "outcome", "cutoff_pack"])
    val2 = pd.concat([val2_primary, val2_quiet], ignore_index=True)
    val2.sort_values("lift_pp", ascending=False).to_csv(OUTPUT_DIR / "validation_leaderboard.csv", index=False)

    gate_path = gate_sweep(val2_primary, OUTPUT_DIR, "stage2_path")

    cfg_rest = next(c for c in GATE_CONFIGS if c["name"] == "restrictive")
    g2 = val2_primary.copy()
    g2["gate"] = g2.apply(lambda r, c=cfg_rest: apply_gate(r, c), axis=1)
    restrictive_pass = g2[g2["gate"] == "PASS"]

    # Stage 1 gates (geometry browsing)
    val1 = stage1_df[stage1_df["split"] == "validation"].copy()
    val1_primary = add_fdr(val1[val1["family"] == "primary_3way"].copy(), ["outcome"])
    gate_s1 = gate_sweep(val1_primary, OUTPUT_DIR, "stage1_geometry")

    # ---- Stage 3 ATR diagnostic ----
    print("Adding ATR forward metrics...", flush=True)
    panel = add_atr_forward_metrics(panel)
    geos = pick_stage3_geometries(val1_primary)
    _write_json(OUTPUT_DIR / "stage3_geometries.json", [{"w": w, "d": d, "q": q} for w, d, q in geos])
    stage3: list[dict[str, Any]] = []
    for split_name in ("discovery", "validation"):
        print(f"Stage 3 ATR scoring {split_name} on {len(geos)} geometries...", flush=True)
        stage3.extend(score_atr_diagnostic(panel, splits[split_name], split_name, geos))
    stage3_df = pd.DataFrame(stage3)
    stage3_df.to_csv(OUTPUT_DIR / "stage3_atr_journal.csv", index=False)
    atr_val = stage3_df[stage3_df["split"] == "validation"].copy()
    atr_val = add_fdr(atr_val, ["horizon_m", "breakout_bps", "k", "outcome"])
    atr_val.sort_values("lift_pp", ascending=False).to_csv(
        OUTPUT_DIR / "atr_diagnostic_leaderboard.csv", index=False
    )
    gate_atr = gate_sweep(atr_val, OUTPUT_DIR, "stage3_atr")
    gate_summary = pd.concat([gate_s1, gate_path, gate_atr], ignore_index=True)
    gate_summary.to_csv(OUTPUT_DIR / "gate_threshold_sweep" / "gate_config_summary.csv", index=False)

    final_rows: list[dict[str, Any]] = []
    if not restrictive_pass.empty and splits.get("final"):
        print("Stage-2 restrictive PASS found — scoring FINAL...", flush=True)
        final_rows = score_path_grid(
            panel,
            splits["final"],
            "final",
            windows=WINDOWS,
            ratios=DOMINANCE_RATIOS,
            quiet_floors=QUIET_FLOORS,
            horizons=HORIZONS,
            packs=CUTOFF_PACKS,
            stage="final",
        )
        pd.DataFrame(final_rows).to_csv(OUTPUT_DIR / "final_journal.csv", index=False)
    else:
        print("No Stage-2 restrictive PASS — FINAL locked.", flush=True)

    # Headlines
    headline = val2_primary[
        (val2_primary["window_m"] == HEADLINE["w"])
        & (val2_primary["dominance_ratio"] == HEADLINE["d"])
        & (val2_primary["quiet_floor"] == HEADLINE["q"])
        & (val2_primary["horizon_m"] == HEADLINE["h"])
        & (val2_primary["cutoff_pack"] == HEADLINE["pack"])
    ].sort_values("lift_pp", ascending=False)

    cfg_mod = next(c for c in GATE_CONFIGS if c["name"] == "moderate")
    g1 = val1_primary.copy()
    g1["gate"] = g1.apply(lambda r, c=cfg_mod: apply_gate(r, c), axis=1)
    best_geom = g1.sort_values("lift_pp", ascending=False).head(5)

    summary = {
        "run_date": "2026-07-17",
        "definition": "role_absorption_B",
        "quiet": "separate_4th_bucket_swept",
        "axes": {
            "windows": list(WINDOWS),
            "dominance_ratios": list(DOMINANCE_RATIOS),
            "quiet_floors": list(QUIET_FLOORS),
            "horizons": list(HORIZONS),
            "cutoff_packs": list(CUTOFF_PACKS),
        },
        "headline": HEADLINE,
        "splits": {k: list(v) for k, v in splits.items()},
        "n_clean_dates": int(meta["dates_clean"]),
        "n_clean_rows": int(meta["rows_clean"]),
        "stage1_rows": int(len(stage1_df)),
        "stage2_rows": int(len(stage2_df)),
        "stage3_rows": int(len(stage3_df)),
        "stage2_validation_primary_cells": int(len(val2_primary)),
        "restrictive_pass_n": int(len(restrictive_pass)),
        "restrictive_pass_cells": restrictive_pass[
            ["cell_id", "window_m", "dominance_ratio", "quiet_floor", "horizon_m", "cutoff_pack", "state", "outcome", "lift_pp", "n"]
        ].to_dict(orient="records")
        if not restrictive_pass.empty
        else [],
        "headline_validation": headline[
            ["cell_id", "state", "outcome", "n", "lift_pp", "one_sided_lower", "bh_significant"]
        ].to_dict(orient="records")
        if not headline.empty
        else [],
        "stage1_best_moderate_browse": best_geom[
            ["cell_id", "window_m", "dominance_ratio", "quiet_floor", "state", "outcome", "lift_pp", "gate", "n"]
        ].to_dict(orient="records")
        if not best_geom.empty
        else [],
        "stage3_geometries": [{"w": w, "d": d, "q": q} for w, d, q in geos],
        "gate_summary": gate_summary.to_dict(orient="records"),
        "final_opened": bool(final_rows),
        "guardrail": "offline_only_no_v12_retune_without_restrictive_pass_and_approval",
    }
    _write_json(OUTPUT_DIR / "summary.json", summary)

    print("\n=== Headline replication (w15 d1.5 q0.5 h30 frozen) ===", flush=True)
    if headline.empty:
        print("(empty)", flush=True)
    else:
        print(headline[["state", "outcome", "n", "lift_pp", "bh_significant"]].to_string(index=False), flush=True)
    print(f"\nStage-2 restrictive PASS: {len(restrictive_pass)}", flush=True)
    print(f"Wrote {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
