#!/usr/bin/env python3
"""Research-only V1 fair-value dispersion source attribution.

Decomposes residual changes into PRICE_LED / FLOW_UNFOLLOWED / MIXED_MISMATCH /
QUIET_BUILD, with a BETA_STALE overlay, and reports mix + co-occurrence when
|z| is large. Forward RANGE/TREND by source is diagnostic only.

Writes only under workspace/analysis/aggression_v1_dispersion_source_attribution/.
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

from scripts.aggression_v1_aggress_passive_dominance_research import (  # noqa: E402
    DEFAULT_REGIME_CUTOFFS_30,
    apply_path_regime_labels,
    load_frozen_cutoffs_30,
)
from scripts.aggression_v1_dispersion_foundations_research import (  # noqa: E402
    fast_decluster,
    fit_live_style_residual,
)
from scripts.aggression_v1_flow_price_dispersion_research import (  # noqa: E402
    EPISODE_STRIDE_MIN,
    EVENTS_DB,
    FEATURE_MATRIX,
    MIN_POINTS_OLS,
    _event_times,
    _near_event,
    _session_expanding_z,
    add_dispersion_dynamics,
    add_forward_regime_outcomes,
    chronological_splits,
    fit_causal_v1_residual,
    load_features,
)

OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v1_dispersion_source_attribution"
WINDOWS = (1, 5, 15)
HEADLINE_W = 15
DOM_RATIO = 1.5
ACTIVITY_FLOOR = 0.5
LARGE_Z = (1.0, 1.5)
SOURCES = ("PRICE_LED", "FLOW_UNFOLLOWED", "MIXED_MISMATCH", "QUIET_BUILD")
CTX_COLS = (
    "trade_intensity",
    "agg_5m",
    "agg_rate",
    "displayed_depth_mean",
    "spread_mean",
    "size_imbalance_mean",
    "trade_size_vs_depth",
    "residual_widening_15m",
    "abs_residual_chg_15m",
)
Z_MIN_PERIODS = 20
EPS = 1e-12


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


def _same_sign(a: pd.Series, b: pd.Series) -> pd.Series:
    sa = np.sign(a.fillna(0.0))
    sb = np.sign(b.fillna(0.0))
    return (sa != 0) & (sb != 0) & (sa == sb)


def _diff_sign(a: pd.Series, b: pd.Series) -> pd.Series:
    sa = np.sign(a.fillna(0.0))
    sb = np.sign(b.fillna(0.0))
    return (sa != 0) & (sb != 0) & (sa != sb)


def _expanding_abs_scale(series: pd.Series, dates: pd.Series, min_periods: int = Z_MIN_PERIODS) -> pd.Series:
    """Causal session expanding median of |series| (shift-1)."""
    abs_s = series.abs()
    return abs_s.groupby(dates, sort=False).transform(
        lambda s: s.shift(1).expanding(min_periods=min_periods).median()
    ).clip(lower=EPS)


def add_decomp_windows(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    beta = pd.to_numeric(out["v1_beta"], errors="coerce")
    aggress = pd.to_numeric(out["total_aggress"], errors="coerce")
    residual = pd.to_numeric(out["v1_residual"], errors="coerce")
    price = pd.to_numeric(out["price"], errors="coerce")

    delta_a = aggress.groupby(out["et_date"], sort=False).diff().fillna(0.0)
    delta_r = residual.groupby(out["et_date"], sort=False).diff()
    delta_p = price.groupby(out["et_date"], sort=False).diff()
    out["contrib_aggress_1m"] = beta * delta_a
    out["delta_resid_1m"] = delta_r
    out["delta_price_1m"] = delta_p
    # Identity residual of decomp (should be ~0 aside from α/β jumps).
    out["decomp_gap_1m"] = out["delta_resid_1m"] - (out["delta_price_1m"] - out["contrib_aggress_1m"])

    for w in WINDOWS:
        out[f"contrib_aggress_{w}m"] = (
            out["contrib_aggress_1m"]
            .groupby(out["et_date"], sort=False)
            .transform(lambda s, ww=w: s.rolling(ww, min_periods=ww).sum())
        )
        out[f"delta_resid_{w}m"] = (
            out["delta_resid_1m"]
            .groupby(out["et_date"], sort=False)
            .transform(lambda s, ww=w: s.rolling(ww, min_periods=ww).sum())
        )
        out[f"delta_price_{w}m"] = (
            out["delta_price_1m"]
            .groupby(out["et_date"], sort=False)
            .transform(lambda s, ww=w: s.rolling(ww, min_periods=ww).sum())
        )
        scale_p = _expanding_abs_scale(out[f"delta_price_{w}m"], out["et_date"])
        scale_a = _expanding_abs_scale(out[f"contrib_aggress_{w}m"], out["et_date"])
        out[f"scaled_abs_price_{w}m"] = out[f"delta_price_{w}m"].abs() / scale_p
        out[f"scaled_abs_aggress_{w}m"] = out[f"contrib_aggress_{w}m"].abs() / scale_a
    return out


def label_sources(df: pd.DataFrame, window: int, dominance_ratio: float = DOM_RATIO) -> pd.Series:
    """Label residual-change source over lookback window."""
    sp = pd.to_numeric(df[f"scaled_abs_price_{window}m"], errors="coerce")
    sa = pd.to_numeric(df[f"scaled_abs_aggress_{window}m"], errors="coerce")
    d_p = pd.to_numeric(df[f"delta_price_{window}m"], errors="coerce")
    c_a = pd.to_numeric(df[f"contrib_aggress_{window}m"], errors="coerce")
    d_r = pd.to_numeric(df[f"delta_resid_{window}m"], errors="coerce")

    quiet = (sp.fillna(0.0) < ACTIVITY_FLOOR) & (sa.fillna(0.0) < ACTIVITY_FLOOR)
    activity = (~quiet) & sp.notna() & sa.notna() & d_r.notna()

    price_dom = activity & (sp >= dominance_ratio * sa)
    flow_dom = activity & (sa >= dominance_ratio * sp)
    # PRICE_LED: price dominates and residual moves with price (gap built by price not explained by aggress).
    price_led = price_dom & (_same_sign(d_p, d_r) | (~_same_sign(c_a, d_r)))
    # FLOW_UNFOLLOWED: aggress dominates window (price not matching flow magnitude).
    flow_unfollowed = flow_dom & (~price_led)
    both_material = activity & (sp >= ACTIVITY_FLOOR) & (sa >= ACTIVITY_FLOOR)
    fight = _diff_sign(d_p, c_a)
    mixed = activity & (~price_led) & (~flow_unfollowed) & (both_material | fight)

    label = pd.Series("QUIET_BUILD", index=df.index, dtype=object)
    label = label.mask(quiet | sp.isna() | sa.isna(), "QUIET_BUILD")
    label = label.mask(price_led, "PRICE_LED")
    label = label.mask(flow_unfollowed, "FLOW_UNFOLLOWED")
    label = label.mask(mixed, "MIXED_MISMATCH")
    label = label.mask(activity & (~price_led) & (~flow_unfollowed) & (~mixed), "MIXED_MISMATCH")
    return label


def add_beta_stale_flag(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    z_c = pd.to_numeric(out["v1_residual_z"], errors="coerce")
    z_l = pd.to_numeric(out["live_residual_z"], errors="coerce")
    gap = (z_c - z_l).abs()
    gap_hi = gap.groupby(out["et_date"], sort=False).transform(
        lambda s: s.shift(1).expanding(min_periods=Z_MIN_PERIODS).quantile(0.70)
    )
    out["fv_z_gap"] = gap
    out["beta_stale"] = (z_c.abs() >= 1.0) & (gap >= gap_hi) & gap.notna() & z_l.notna()
    return out


def universe_mask(df: pd.DataFrame, z_thr: float) -> pd.Series:
    z = pd.to_numeric(df["v1_residual_z"], errors="coerce").abs()
    event = df["event_near"].fillna(False)
    return (z >= z_thr) & (~event) & z.notna()


def source_mix_table(df: pd.DataFrame, source_col: str, z_thr: float, window: int) -> list[dict[str, Any]]:
    mask = universe_mask(df, z_thr)
    sub = df.loc[mask]
    n = int(len(sub))
    rows = []
    if n == 0:
        return rows
    counts = sub[source_col].value_counts(dropna=False).to_dict()
    stale_rate = float(sub["beta_stale"].fillna(False).mean())
    for src in SOURCES:
        c = int(counts.get(src, 0))
        rows.append(
            {
                "window_m": window,
                "z_threshold": z_thr,
                "source": src,
                "n_rows": c,
                "share": float(c / n) if n else np.nan,
                "universe_n": n,
                "beta_stale_rate_in_universe": stale_rate,
                "beta_stale_rate_in_source": float(sub.loc[sub[source_col] == src, "beta_stale"].fillna(False).mean())
                if c
                else np.nan,
            }
        )
    return rows


def contribution_shares(df: pd.DataFrame, source_col: str, z_thr: float, window: int) -> list[dict[str, Any]]:
    """Share of sum(|ΔR_w|) by source among large-|z| minutes."""
    mask = universe_mask(df, z_thr)
    sub = df.loc[mask].copy()
    abs_dr = pd.to_numeric(sub[f"delta_resid_{window}m"], errors="coerce").abs()
    total = float(abs_dr.sum())
    rows = []
    for src in SOURCES:
        part = abs_dr[sub[source_col] == src]
        s = float(part.sum()) if len(part) else 0.0
        rows.append(
            {
                "window_m": window,
                "z_threshold": z_thr,
                "source": src,
                "abs_delta_resid_sum": s,
                "share_of_abs_delta_resid": float(s / total) if total > 0 else np.nan,
                "mean_abs_delta_resid": float(part.mean()) if len(part) else np.nan,
                "n": int(len(part)),
            }
        )
    rows.append(
        {
            "window_m": window,
            "z_threshold": z_thr,
            "source": "ALL",
            "abs_delta_resid_sum": total,
            "share_of_abs_delta_resid": 1.0 if total > 0 else np.nan,
            "mean_abs_delta_resid": float(abs_dr.mean()) if len(abs_dr) else np.nan,
            "n": int(len(sub)),
        }
    )
    return rows


def cooccurrence_table(df: pd.DataFrame, source_col: str, z_thr: float, window: int) -> list[dict[str, Any]]:
    mask = universe_mask(df, z_thr)
    base = df.loc[~df["event_near"].fillna(False)]
    large = df.loc[mask]
    rows: list[dict[str, Any]] = []

    def _med(series: pd.Series) -> float:
        s = pd.to_numeric(series, errors="coerce")
        if s.dropna().empty:
            return float("nan")
        return float(s.median())

    def _mean_bool(series: pd.Series) -> float:
        s = series.fillna(False).astype(bool)
        return float(s.mean()) if len(s) else float("nan")

    groups = [("ALL_NONEVENT", base), ("LARGE", large)]
    for src in SOURCES:
        groups.append((src, large.loc[large[source_col] == src]))

    for name, part in groups:
        if part.empty:
            continue
        row: dict[str, Any] = {
            "window_m": window,
            "z_threshold": z_thr,
            "group": name,
            "n": int(len(part)),
            "median_abs_z": _med(pd.to_numeric(part["v1_residual_z"], errors="coerce").abs()),
            "beta_stale_rate": _mean_bool(part["beta_stale"]),
        }
        for col in CTX_COLS:
            if col not in part.columns:
                continue
            if col.startswith("residual_widening"):
                row[f"{col}_rate"] = _mean_bool(part[col])
            else:
                row[f"{col}_median"] = _med(part[col])
        rows.append(row)
    return rows


def forward_by_source_diagnostic(
    df: pd.DataFrame,
    source_col: str,
    z_thr: float,
    window: int,
    dates: list[str],
    split_name: str,
) -> list[dict[str, Any]]:
    """Descriptive P(RANGE)/P(TREND) by source — not a shipping gate."""
    sub = df[df["et_date"].isin(set(dates)) & universe_mask(df, z_thr)].copy()
    if sub.empty or "future_is_range_30m" not in sub.columns:
        return []
    labeled = sub.dropna(subset=["future_regime_30m"])
    if labeled.empty:
        return []
    base_range = float(labeled["future_is_range_30m"].astype(float).mean())
    base_trend = float(labeled["future_is_trend_30m"].astype(float).mean())
    rows = []
    for src in list(SOURCES) + ["ALL"]:
        if src == "ALL":
            eps = fast_decluster(labeled, pd.Series(True, index=labeled.index), stride_min=EPISODE_STRIDE_MIN)
        else:
            eps = fast_decluster(labeled, labeled[source_col] == src, stride_min=EPISODE_STRIDE_MIN)
        if eps.empty:
            continue
        n = int(len(eps))
        r_rate = float(eps["future_is_range_30m"].astype(float).mean())
        t_rate = float(eps["future_is_trend_30m"].astype(float).mean())
        rows.append(
            {
                "split": split_name,
                "window_m": window,
                "z_threshold": z_thr,
                "source": src,
                "n": n,
                "n_dates": int(eps["et_date"].nunique()),
                "range_rate": r_rate,
                "trend_rate": t_rate,
                "range_lift_pp": (r_rate - base_range) * 100.0,
                "trend_lift_pp": (t_rate - base_trend) * 100.0,
                "baseline_range": base_range,
                "baseline_trend": base_trend,
            }
        )
    return rows


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading features...", flush=True)
    features, meta = load_features(FEATURE_MATRIX)
    features["event_near"] = _near_event(features.index, _event_times(EVENTS_DB))
    splits = chronological_splits(meta["clean_dates"])

    print("Fitting causal residual + dynamics...", flush=True)
    panel = fit_causal_v1_residual(features)
    panel = add_dispersion_dynamics(panel)
    print("Fitting live-style residual (BETA_STALE overlay)...", flush=True)
    panel = fit_live_style_residual(panel)
    print("Building decomp windows + source labels...", flush=True)
    panel = add_decomp_windows(panel)
    panel = add_beta_stale_flag(panel)
    for w in WINDOWS:
        panel[f"source_{w}m"] = label_sources(panel, w, DOM_RATIO)

    print("Adding forward path regimes (diagnostic)...", flush=True)
    panel = add_forward_regime_outcomes(panel)
    # add_forward_regime_outcomes uses HORIZONS including 30 from dispersion module
    cutoffs = load_frozen_cutoffs_30()
    # Map to columns expected by apply_path_regime_labels helper from dominance script
    # That helper writes future_regime_{h}m from cutoffs_by_h; reuse for h=30 only.
    panel = apply_path_regime_labels(panel, {30: cutoffs})

    mix_rows: list[dict[str, Any]] = []
    share_rows: list[dict[str, Any]] = []
    ctx_rows: list[dict[str, Any]] = []
    fwd_rows: list[dict[str, Any]] = []

    for w in WINDOWS:
        scol = f"source_{w}m"
        for z_thr in LARGE_Z:
            mix_rows.extend(source_mix_table(panel, scol, z_thr, w))
            share_rows.extend(contribution_shares(panel, scol, z_thr, w))
            ctx_rows.extend(cooccurrence_table(panel, scol, z_thr, w))
            for split_name in ("discovery", "validation"):
                fwd_rows.extend(
                    forward_by_source_diagnostic(panel, scol, z_thr, w, splits[split_name], split_name)
                )

    mix_df = pd.DataFrame(mix_rows)
    share_df = pd.DataFrame(share_rows)
    ctx_df = pd.DataFrame(ctx_rows)
    fwd_df = pd.DataFrame(fwd_rows)
    mix_df.to_csv(OUTPUT_DIR / "source_mix.csv", index=False)
    share_df.to_csv(OUTPUT_DIR / "contribution_shares.csv", index=False)
    ctx_df.to_csv(OUTPUT_DIR / "cooccurrence_table.csv", index=False)
    fwd_df.to_csv(OUTPUT_DIR / "forward_by_source_diagnostic.csv", index=False)

    # Headline snapshot: w=15, |z|>=1.5
    def _headline_block(z_thr: float) -> dict[str, Any]:
        m = mix_df[(mix_df["window_m"] == HEADLINE_W) & (mix_df["z_threshold"] == z_thr)]
        s = share_df[(share_df["window_m"] == HEADLINE_W) & (share_df["z_threshold"] == z_thr)]
        c = ctx_df[(ctx_df["window_m"] == HEADLINE_W) & (ctx_df["z_threshold"] == z_thr)]
        f = fwd_df[
            (fwd_df["window_m"] == HEADLINE_W)
            & (fwd_df["z_threshold"] == z_thr)
            & (fwd_df["split"] == "validation")
        ]
        return {
            "source_mix": m.to_dict(orient="records"),
            "contribution_shares": s.to_dict(orient="records"),
            "cooccurrence": c.to_dict(orient="records"),
            "forward_validation": f.to_dict(orient="records"),
        }

    # Decomp identity check
    gap = pd.to_numeric(panel["decomp_gap_1m"], errors="coerce").abs()
    identity = {
        "median_abs_decomp_gap": float(gap.median()) if gap.notna().any() else None,
        "p99_abs_decomp_gap": float(gap.quantile(0.99)) if gap.notna().any() else None,
        "note": "ΔR - (ΔP - βΔA); non-zero from α/β day changes and numerical noise",
    }

    summary = {
        "run_date": "2026-07-20",
        "definition": "deltaR_approx_deltaP_minus_beta_deltaA",
        "dominance_ratio": DOM_RATIO,
        "activity_floor": ACTIVITY_FLOOR,
        "windows": list(WINDOWS),
        "headline_window": HEADLINE_W,
        "large_z_thresholds": list(LARGE_Z),
        "n_clean_dates": int(meta["dates_clean"]),
        "n_clean_rows": int(meta["rows_clean"]),
        "splits": {k: list(v) for k, v in splits.items()},
        "identity_check": identity,
        "headline_z1": _headline_block(1.0),
        "headline_z15": _headline_block(1.5),
        "guardrail": "diagnostic_only_no_v12_badge",
        "shipping_claim": False,
    }
    _write_json(OUTPUT_DIR / "summary.json", summary)

    print("\n=== Headline source mix (w=15, |z|>=1.5, non-event) ===", flush=True)
    h = mix_df[(mix_df.window_m == HEADLINE_W) & (mix_df.z_threshold == 1.5)]
    print(h[["source", "n_rows", "share", "beta_stale_rate_in_source"]].to_string(index=False), flush=True)
    print("\n=== abs(deltaR) contribution shares ===", flush=True)
    hs = share_df[(share_df.window_m == HEADLINE_W) & (share_df.z_threshold == 1.5)]
    print(hs[["source", "share_of_abs_delta_resid", "n"]].to_string(index=False), flush=True)
    print(f"\nWrote {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
