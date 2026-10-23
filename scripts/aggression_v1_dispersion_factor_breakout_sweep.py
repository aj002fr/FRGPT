#!/usr/bin/env python3
"""Research-only sweep: dispersion factors → ATR CONTINUED_RANGE vs bps BREAKOUT.

Reports ALL cells (discovery + validation) regardless of gate. Overlays a
flexible→restrictive validation gate ladder. Writes only under
workspace/analysis/aggression_v1_dispersion_factor_breakout/. Does not touch
live V1/V2/V3 or V1.2.
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
from scripts.aggression_v1_flow_price_dispersion_research import (  # noqa: E402
    EPISODE_STRIDE_MIN,
    EVENTS_DB,
    FEATURE_MATRIX,
    TICK_SIZE,
    _event_times,
    _near_event,
    add_dispersion_dynamics,
    chronological_splits,
    fit_causal_v1_residual,
    load_features,
)

OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v1_dispersion_factor_breakout"
TY_TICKS_PER_BP = 2.0
HORIZONS = (15, 30, 60)
BREAKOUT_BPS_GRID = (4, 6, 8, 12, 16)
K_GRID = (0.5, 1.0, 1.5, 2.0)
RTH_MINUTES = 420.0
PRIMARY = {"h": 30, "B": 8.0, "k": 1.0}
N_BOOT = 200  # validation inference only; all raw rates always stored
EASTERN = "America/New_York"
RTH_START = 8 * 60
RTH_END = 15 * 60


def fast_decluster(df: pd.DataFrame, mask: pd.Series, stride_min: int = EPISODE_STRIDE_MIN) -> pd.DataFrame:
    """Faster de-cluster: avoid iterrows."""
    m = mask.reindex(df.index).fillna(False).to_numpy()
    if not m.any():
        return df.iloc[0:0].copy()
    sub = df.loc[m]
    if sub.empty:
        return sub
    sub = sub.sort_index()
    dates = sub["et_date"].to_numpy()
    minutes = sub.index.asi8 // (60 * 10**9)
    keep = np.zeros(len(sub), dtype=bool)
    last_t = None
    last_d = None
    for i in range(len(sub)):
        if last_t is None or dates[i] != last_d or (minutes[i] - last_t) >= stride_min:
            keep[i] = True
            last_t = int(minutes[i])
            last_d = dates[i]
    return sub.iloc[keep]

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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def _is_rth(ts: pd.Timestamp) -> bool:
    et = ts.tz_convert(EASTERN)
    return RTH_START <= (et.hour * 60 + et.minute) < RTH_END


def prior_day_rth_atr_ticks(df: pd.DataFrame) -> pd.Series:
    rth = df[df.index.map(_is_rth)]
    if rth.empty:
        return pd.Series(np.nan, index=df.index)
    daily = rth.groupby("et_date")["price"].agg(["min", "max"])
    daily["atr_ticks"] = (daily["max"] - daily["min"]) / TICK_SIZE
    dates = sorted(daily.index.tolist())
    prior = {dates[i]: float(daily.loc[dates[i - 1], "atr_ticks"]) for i in range(1, len(dates))}
    return df["et_date"].map(prior).astype(float)


def add_forward_metrics(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for _, day in df.groupby("et_date", sort=False):
        d = day.sort_index().copy()
        price = d["price"].astype(float)
        for h in HORIZONS:
            future = price.shift(-h)
            net = (future - price) / TICK_SIZE
            fwd_high = price.iloc[::-1].rolling(h + 1, min_periods=h + 1).max().iloc[::-1]
            fwd_low = price.iloc[::-1].rolling(h + 1, min_periods=h + 1).min().iloc[::-1]
            d[f"fwd_net_ticks_{h}m"] = net
            d[f"fwd_abs_net_ticks_{h}m"] = net.abs()
            d[f"fwd_range_ticks_{h}m"] = (fwd_high - fwd_low) / TICK_SIZE
            d[f"fwd_abs_net_bps_{h}m"] = net.abs() / TY_TICKS_PER_BP
            if len(d) >= h:
                cols = [f"fwd_net_ticks_{h}m", f"fwd_abs_net_ticks_{h}m", f"fwd_range_ticks_{h}m", f"fwd_abs_net_bps_{h}m"]
                d.loc[d.index[-h]:, cols] = np.nan
        parts.append(d)
    out = pd.concat(parts).sort_index()
    out["prior_atr_ticks"] = prior_day_rth_atr_ticks(out)
    for h in HORIZONS:
        out[f"atr_unit_{h}m"] = out["prior_atr_ticks"] * float(np.sqrt(h / RTH_MINUTES))
    return out


def same_sign_streak(residual: pd.Series, et_date: pd.Series) -> pd.Series:
    signs = np.sign(residual.fillna(0.0).to_numpy())
    dates = et_date.to_numpy()
    out = np.zeros(len(signs), dtype=float)
    run = 0
    prev_s = 0.0
    prev_d = None
    for i, (s, d) in enumerate(zip(signs, dates)):
        if d != prev_d:
            run = 0
            prev_s = 0.0
            prev_d = d
        if s == 0 or s != prev_s:
            run = 1 if s != 0 else 0
        else:
            run += 1
        prev_s = s
        out[i] = float(run)
    return pd.Series(out, index=residual.index)


def build_factor_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    abs_z = pd.to_numeric(df["v1_residual_z"], errors="coerce").abs()
    residual = pd.to_numeric(df["v1_residual"], errors="coerce")
    age = pd.to_numeric(df.get("residual_large_age_min"), errors="coerce").fillna(0.0)
    chg5 = pd.to_numeric(df.get("abs_residual_chg_5m"), errors="coerce")
    chg15 = pd.to_numeric(df.get("abs_residual_chg_15m"), errors="coerce")
    # Causal session percentiles for speed.
    fast5 = chg5.groupby(df["et_date"]).transform(lambda s: s.shift(1).expanding(20).quantile(0.70))
    slow5 = chg5.groupby(df["et_date"]).transform(lambda s: s.shift(1).expanding(20).quantile(0.30))
    fast15 = chg15.groupby(df["et_date"]).transform(lambda s: s.shift(1).expanding(20).quantile(0.70))
    widening = df.get("residual_widening_15m", pd.Series(False, index=df.index)).fillna(False)
    narrowing = (~widening) & abs_z.notna()
    large = abs_z >= 1.0
    positive = residual > 0
    negative = residual < 0
    flow_leads = df.get("flow_leads_15m", pd.Series(False, index=df.index)).fillna(False)
    price_leads = df.get("price_leads_15m", pd.Series(False, index=df.index)).fillna(False)
    agree = df.get("flow_price_agree_15m", pd.Series(False, index=df.index)).fillna(False)
    disagree = df.get("flow_price_disagree_15m", pd.Series(False, index=df.index)).fillna(False)
    accel = pd.to_numeric(df.get("residual_accel_5m"), errors="coerce").fillna(0.0)
    is_fast5 = chg5 >= fast5
    is_slow5 = chg5 <= slow5
    is_fast15 = chg15 >= fast15
    streak = same_sign_streak(residual, df["et_date"])
    streak_hi = streak.groupby(df["et_date"]).transform(lambda s: s.shift(1).expanding(20).quantile(0.67))

    masks: dict[str, pd.Series] = {
        "z_ge_0_5": abs_z >= 0.5,
        "z_ge_1": large,
        "z_ge_1_5": abs_z >= 1.5,
        "z_ge_2": abs_z >= 2.0,
        "z_small": abs_z < 0.5,
        "residual_pos": positive,
        "residual_neg": negative,
        "large_pos": large & positive,
        "large_neg": large & negative,
        "flow_leads": flow_leads,
        "price_leads": price_leads,
        "agree": agree,
        "disagree": disagree,
        "large_disagree": large & disagree,
        "fast_5m": is_fast5.fillna(False),
        "slow_5m": is_slow5.fillna(False),
        "fast_15m": is_fast15.fillna(False),
        "large_fast_5m": large & is_fast5.fillna(False),
        "accel_up_large": large & (accel > 0),
        "accel_down_large": large & (accel < 0),
        "age_0_5": (age >= 0) & (age < 5) & large,
        "age_5_15": (age >= 5) & (age < 15),
        "age_15_30": (age >= 15) & (age < 30),
        "age_ge_30": age >= 30,
        "large_persist_10": large & (age >= 10),
        "large_persist_20": large & (age >= 20),
        "widening": widening,
        "narrowing": narrowing,
        "large_widening": large & widening,
        "large_narrowing": large & narrowing,
        "large_widen_fast": large & widening & is_fast5.fillna(False),
        "large_widen_persist": large & widening & (age >= 10),
        "large_disagree_fast": large & disagree & is_fast5.fillna(False),
        "same_sign_streak_hi": (streak >= streak_hi.fillna(np.inf)) & (streak >= 5),
    }
    # |z| quintiles within day using causal expanding rank proxy via prior expanding.
    z_rank = abs_z.groupby(df["et_date"]).transform(
        lambda s: s.rank(pct=True)  # contemporaneous within-day rank for monotonicity screen only
    )
    for q_lo, q_hi, name in ((0.0, 0.2, "zq0_20"), (0.2, 0.4, "zq20_40"), (0.4, 0.6, "zq40_60"), (0.6, 0.8, "zq60_80"), (0.8, 1.01, "zq80_100")):
        masks[name] = (z_rank > q_lo) & (z_rank <= q_hi)
    return masks


def evaluate_cell(
    episodes: pd.DataFrame,
    *,
    cell_id: str,
    family: str,
    split_name: str,
    outcome_col: str,
    baseline_rate: float,
    h: int,
    B: float,
    k: float,
    factor: str,
    outcome: str,
) -> dict[str, Any]:
    base = {
        "cell_id": cell_id,
        "family": family,
        "split": split_name,
        "horizon_m": h,
        "breakout_bps": B,
        "k": k,
        "factor": factor,
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
        seed=(abs(hash(cell_id)) + 13) % (2**31 - 1),
        groups=groups,
    )
    base["ci_low"] = float(lift_ci["ci_low"]) if lift_ci["n"] else np.nan
    base["ci_high"] = float(lift_ci["ci_high"]) if lift_ci["n"] else np.nan
    base["one_sided_lower"] = float(lift_ci["one_sided_lower"]) if lift_ci["n"] else np.nan
    base["p_one_sided"] = float(one_sided_p(lift_values))
    return base


def apply_gate(row: pd.Series, cfg: dict[str, Any]) -> str:
    n_ok = int(row.get("n", 0) or 0) >= cfg["min_n"]
    dates_ok = (not cfg["require_dates"]) or (int(row.get("n_dates", 0) or 0) >= cfg["min_dates"])
    lift = row.get("lift_pp")
    lift_ok = np.isfinite(lift) and float(lift) >= cfg["min_lift_pp"]
    ci = row.get("one_sided_lower")
    ci_ok = (not cfg["require_ci"]) or (np.isfinite(ci) and float(ci) > 0)
    bh_ok = (not cfg["require_bh"]) or bool(row.get("bh_significant", False))
    if n_ok and dates_ok and lift_ok and ci_ok and bh_ok:
        return "PASS"
    if n_ok and lift_ok and (ci_ok or bool(row.get("bh_significant", False))):
        return "PROVISIONAL"
    return "FAIL"


def label_config(df: pd.DataFrame, h: int, B: float, k: float) -> pd.DataFrame:
    abs_net = df[f"fwd_abs_net_ticks_{h}m"]
    fwd_range = df[f"fwd_range_ticks_{h}m"]
    atr_unit = df[f"atr_unit_{h}m"]
    event = df["event_near"].fillna(False)
    breakout_ticks = B * TY_TICKS_PER_BP
    breakout = (abs_net >= breakout_ticks) & (~event) & abs_net.notna()
    continued = (fwd_range <= (k * atr_unit)) & (~breakout) & fwd_range.notna() & atr_unit.notna() & (atr_unit > 0)
    label = pd.Series("OTHER", index=df.index, dtype=object)
    label = label.mask(continued, "CONTINUED_RANGE")
    label = label.mask(breakout, "BREAKOUT")
    label = label.mask(abs_net.isna() | fwd_range.isna(), np.nan)
    out = df.copy()
    out["future_regime_atr"] = label
    out["future_is_breakout"] = label == "BREAKOUT"
    out["future_is_continued_range"] = label == "CONTINUED_RANGE"
    return out


def regime_mix_row(df: pd.DataFrame, dates: list[str], h: int, B: float, k: float, split: str) -> dict[str, Any]:
    sub = df[df["et_date"].isin(set(dates)) & ~df["event_near"].fillna(False)]
    counts = sub["future_regime_atr"].value_counts(dropna=False).to_dict()
    return {
        "split": split,
        "horizon_m": h,
        "breakout_bps": B,
        "k": k,
        "n_rows": int(len(sub)),
        "n_dates": int(sub["et_date"].nunique()),
        "n_breakout": int(counts.get("BREAKOUT", 0)),
        "n_continued_range": int(counts.get("CONTINUED_RANGE", 0)),
        "n_other": int(counts.get("OTHER", 0)),
        "n_nan": int(sum(v for key, v in counts.items() if pd.isna(key))),
        "breakout_share": float(sub["future_is_breakout"].mean()) if len(sub) else np.nan,
        "range_share": float(sub["future_is_continued_range"].mean()) if len(sub) else np.nan,
    }


def run_split_all_configs(
    panel: pd.DataFrame,
    masks: dict[str, pd.Series],
    dates: list[str],
    split_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Evaluate all (h,B,k) for one split; attach masks once."""
    nonevent = panel[panel["et_date"].isin(set(dates)) & ~panel["event_near"].fillna(False)].copy()
    for name, mask in masks.items():
        nonevent[f"mask_{name}"] = mask.reindex(nonevent.index).fillna(False)

    journal: list[dict[str, Any]] = []
    mixes: list[dict[str, Any]] = []
    configs = [(h, float(B), float(k)) for h in HORIZONS for B in BREAKOUT_BPS_GRID for k in K_GRID]
    for i, (h, B, k) in enumerate(configs, start=1):
        labeled = label_config(nonevent, h, B, k)
        mixes.append(regime_mix_row(labeled, dates, h, B, k, split_name))
        extreme = labeled[labeled["future_regime_atr"].isin(["BREAKOUT", "CONTINUED_RANGE"])].copy()
        if extreme.empty:
            if i % 10 == 0:
                print(f"  {split_name} {i}/{len(configs)} (empty extreme)", flush=True)
            continue
        base_bo_ext = float(extreme["future_is_breakout"].mean())
        base_cr_ext = float(extreme["future_is_continued_range"].mean())
        base_bo_all = float(labeled["future_is_breakout"].mean())
        base_cr_all = float(labeled["future_is_continued_range"].mean())
        journal.append(
            {
                "cell_id": f"h{h}_B{B}_k{k}_baseline_extreme_breakout",
                "family": "baseline",
                "split": split_name,
                "horizon_m": h,
                "breakout_bps": B,
                "k": k,
                "factor": "baseline_extreme",
                "outcome": "BREAKOUT",
                "n": int(len(extreme)),
                "n_dates": int(extreme["et_date"].nunique()),
                "rate": base_bo_ext,
                "baseline_rate": base_bo_ext,
                "lift_pp": 0.0,
                "ci_low": np.nan,
                "ci_high": np.nan,
                "one_sided_lower": np.nan,
                "p_one_sided": np.nan,
            }
        )
        for factor in masks:
            mask_col = f"mask_{factor}"
            ep_ext = fast_decluster(extreme, extreme[mask_col])
            journal.append(
                evaluate_cell(
                    ep_ext,
                    cell_id=f"h{h}_B{B}_k{k}_{factor}_breakout_ext",
                    family="factor_breakout_extreme",
                    split_name=split_name,
                    outcome_col="future_is_breakout",
                    baseline_rate=base_bo_ext,
                    h=h,
                    B=B,
                    k=k,
                    factor=factor,
                    outcome="BREAKOUT",
                )
            )
            journal.append(
                evaluate_cell(
                    ep_ext,
                    cell_id=f"h{h}_B{B}_k{k}_{factor}_range_ext",
                    family="factor_range_extreme",
                    split_name=split_name,
                    outcome_col="future_is_continued_range",
                    baseline_rate=base_cr_ext,
                    h=h,
                    B=B,
                    k=k,
                    factor=factor,
                    outcome="CONTINUED_RANGE",
                )
            )
            ep_all = fast_decluster(labeled, labeled[mask_col])
            journal.append(
                evaluate_cell(
                    ep_all,
                    cell_id=f"h{h}_B{B}_k{k}_{factor}_breakout_all",
                    family="factor_breakout_all",
                    split_name=split_name,
                    outcome_col="future_is_breakout",
                    baseline_rate=base_bo_all,
                    h=h,
                    B=B,
                    k=k,
                    factor=factor,
                    outcome="BREAKOUT",
                )
            )
            journal.append(
                evaluate_cell(
                    ep_all,
                    cell_id=f"h{h}_B{B}_k{k}_{factor}_range_all",
                    family="factor_range_all",
                    split_name=split_name,
                    outcome_col="future_is_continued_range",
                    baseline_rate=base_cr_all,
                    h=h,
                    B=B,
                    k=k,
                    factor=factor,
                    outcome="CONTINUED_RANGE",
                )
            )
        if i % 5 == 0 or i == len(configs):
            print(f"  {split_name} configs {i}/{len(configs)}; journal+= {len(journal)}", flush=True)
    return journal, mixes


def add_fdr_by_family(val: pd.DataFrame) -> pd.DataFrame:
    out = val.copy()
    out["bh_adjusted_p"] = np.nan
    out["bh_significant"] = False
    if out.empty:
        return out
    pieces = []
    for (_, _, _), part in out.groupby(["horizon_m", "breakout_bps", "k"], sort=False):
        p = part.copy()
        mask = p["family"].astype(str).str.startswith("factor_") & p["p_one_sided"].notna()
        if mask.any():
            bh = benjamini_hochberg(p.loc[mask, "p_one_sided"].to_numpy(dtype=float), q=0.10)
            p.loc[mask, "bh_adjusted_p"] = bh["adjusted_p"]
            p.loc[mask, "bh_significant"] = bh["reject"]
        pieces.append(p)
    return pd.concat(pieces, ignore_index=True)


def gate_sweep(val: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    gate_dir = out_dir / "gate_threshold_sweep"
    gate_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for cfg in GATE_CONFIGS:
        g = val.copy()
        g["gate"] = g.apply(lambda r, c=cfg: apply_gate(r, c), axis=1)
        counts = g["gate"].value_counts().to_dict()
        summary.append(
            {
                **cfg,
                "n_PASS": int(counts.get("PASS", 0)),
                "n_PROVISIONAL": int(counts.get("PROVISIONAL", 0)),
                "n_FAIL": int(counts.get("FAIL", 0)),
            }
        )
        g.sort_values("lift_pp", ascending=False).to_csv(gate_dir / f"validation_{cfg['name']}.csv", index=False)
    summary_df = pd.DataFrame(summary)
    summary_df.to_csv(gate_dir / "gate_config_summary.csv", index=False)
    return summary_df


def factor_stability(val: pd.DataFrame) -> pd.DataFrame:
    rows = []
    factor_cells = val[val["family"].astype(str).str.startswith("factor_")].copy()
    if factor_cells.empty:
        return pd.DataFrame()
    for (factor, outcome, family), part in factor_cells.groupby(["factor", "outcome", "family"]):
        pos = part[part["lift_pp"] > 0]
        rows.append(
            {
                "factor": factor,
                "outcome": outcome,
                "family": family,
                "n_configs": int(part[["horizon_m", "breakout_bps", "k"]].drop_duplicates().shape[0]),
                "n_positive_lift": int(pos[["horizon_m", "breakout_bps", "k"]].drop_duplicates().shape[0]),
                "median_lift_pp": float(part["lift_pp"].median()) if part["lift_pp"].notna().any() else np.nan,
                "max_lift_pp": float(part["lift_pp"].max()) if part["lift_pp"].notna().any() else np.nan,
                "best_cell_id": str(part.sort_values("lift_pp", ascending=False).iloc[0]["cell_id"]) if len(part) else "",
            }
        )
    return pd.DataFrame(rows).sort_values(["n_positive_lift", "median_lift_pp"], ascending=[False, False])


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading features...", flush=True)
    features, meta = load_features(FEATURE_MATRIX)
    features["event_near"] = _near_event(features.index, _event_times(EVENTS_DB))
    print("Fitting residual + dynamics...", flush=True)
    panel = fit_causal_v1_residual(features)
    panel = add_dispersion_dynamics(panel)
    panel = add_forward_metrics(panel)
    splits = chronological_splits(meta["clean_dates"])
    print("Building factor masks...", flush=True)
    masks = build_factor_masks(panel)
    n_configs = len(HORIZONS) * len(BREAKOUT_BPS_GRID) * len(K_GRID)
    print(f"Factors={len(masks)}; configs={n_configs}", flush=True)

    journal: list[dict[str, Any]] = []
    mix_rows: list[dict[str, Any]] = []
    for split_name in ("discovery", "validation"):
        print(f"Running {split_name}...", flush=True)
        j, m = run_split_all_configs(panel, masks, splits[split_name], split_name)
        journal.extend(j)
        mix_rows.extend(m)

    journal_df = pd.DataFrame(journal)
    mix_df = pd.DataFrame(mix_rows)
    mix_df.to_csv(OUTPUT_DIR / "regime_mix_grid.csv", index=False)
    journal_df.to_csv(OUTPUT_DIR / "candidate_journal.csv", index=False)

    val = journal_df[journal_df["split"] == "validation"].copy()
    val = add_fdr_by_family(val)
    val.sort_values("lift_pp", ascending=False).to_csv(OUTPUT_DIR / "validation_all_raw.csv", index=False)
    val.sort_values("lift_pp", ascending=False).to_csv(OUTPUT_DIR / "validation_leaderboard.csv", index=False)

    gate_summary = gate_sweep(val, OUTPUT_DIR)
    stability = factor_stability(val)
    stability.to_csv(OUTPUT_DIR / "factor_stability.csv", index=False)

    primary = val[
        (val["horizon_m"] == PRIMARY["h"])
        & (val["breakout_bps"] == PRIMARY["B"])
        & (val["k"] == PRIMARY["k"])
    ].sort_values("lift_pp", ascending=False)

    cfg = next(c for c in GATE_CONFIGS if c["name"] == "restrictive")
    g = val.copy()
    g["gate"] = g.apply(lambda r, c=cfg: apply_gate(r, c), axis=1)
    restrictive_pass = g[g["gate"] == "PASS"].to_dict(orient="records")

    summary = {
        "ok": True,
        "primary_config": PRIMARY,
        "definitions": {
            "BREAKOUT": "|net_h| >= B yield-bps and not event_near",
            "CONTINUED_RANGE": "range_h <= k * prior_day_RTH_ATR * sqrt(h/420) and not BREAKOUT",
            "ty_ticks_per_bp": TY_TICKS_PER_BP,
        },
        "n_journal_rows": int(len(journal_df)),
        "n_validation_rows": int(len(val)),
        "n_factors": int(len(masks)),
        "n_label_configs": int(n_configs),
        "gate_summary": gate_summary.to_dict(orient="records"),
        "restrictive_pass_n": int(len(restrictive_pass)),
        "restrictive_pass": restrictive_pass[:20],
        "primary_top_validation": primary.head(40).to_dict(orient="records"),
        "regime_mix_primary": mix_df[
            (mix_df["horizon_m"] == PRIMARY["h"])
            & (mix_df["breakout_bps"] == PRIMARY["B"])
            & (mix_df["k"] == PRIMARY["k"])
        ].to_dict(orient="records"),
        "stability_top": stability.head(30).to_dict(orient="records") if len(stability) else [],
        "artifacts": {
            "journal": str(OUTPUT_DIR / "candidate_journal.csv"),
            "validation_all_raw": str(OUTPUT_DIR / "validation_all_raw.csv"),
            "regime_mix": str(OUTPUT_DIR / "regime_mix_grid.csv"),
            "gate_summary": str(OUTPUT_DIR / "gate_threshold_sweep" / "gate_config_summary.csv"),
            "stability": str(OUTPUT_DIR / "factor_stability.csv"),
        },
        "notes": [
            "ALL cells retained regardless of gate.",
            "Flexible gates are exploratory only; only restrictive PASS unlocks FINAL.",
            "FINAL not inspected in this run unless restrictive_pass_n > 0.",
        ],
    }
    _write_json(OUTPUT_DIR / "split_manifest.json", splits)
    _write_json(OUTPUT_DIR / "summary.json", summary)
    print(
        json.dumps(
            {
                "ok": True,
                "n_journal": len(journal_df),
                "restrictive_pass_n": len(restrictive_pass),
                "gate_summary": summary["gate_summary"],
            },
            indent=2,
            default=_json_default,
        )
    )


if __name__ == "__main__":
    main()
