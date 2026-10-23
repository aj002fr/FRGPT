#!/usr/bin/env python3
"""Offline LIFO-zone study: two-way boxes with >=3-tick meat, from flow flags.

Branch 0 freezes the LIFO_ZONE label and a 3-before-2 path metric.
Branch A re-scores frozen H5 / trailing-meat / widening / V1-style flags.
Branch B scores two-way flow signatures.
Branch D scores anti-LIFO vetos (want *lower* zone rate).
Branch C times a fade of the last 3-tick push only when a zone flag is on.

Guardrail: does not import, write to, or restart V1/V1.2/V2/V2.1/V2.2/V2.3/V2.6/V2.7/V3.
Reads the published TBBO matrix (and events DB) only.
Writes only under workspace/analysis/aggression_v2_lifo_zone/.
Does not retune H5 on the 2026-07-17 dates. Does not change live LIFO banners.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_gate_lib import bootstrap_ci, one_sided_p  # noqa: E402
from scripts.aggression_v1_dispersion_foundations_research import (  # noqa: E402
    fast_decluster,
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

OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_lifo_zone"
HORIZONS = (15, 30)
PRIMARY_H = 15
MEAT_TICKS = 3.0
MEAT_SENSITIVITY = (3.0, 4.0, 5.0)
CONTAIN_TICKS = 8.0
ADVERSE_TICKS = 2.0
TRAIL_MEAT_MIN = 15
PATH_CLOCK_MIN = 10
LARGE_ABS_Z = 1.0
INTENSITY_Q = 0.33
EXPANDING_MIN = 20
N_BOOT = 400
MIN_N = 50
MIN_DATES = 8
MIN_LIFT_PP = 8.0
RTH_MINUTES = 420.0
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


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def first_reversal(path_ticks: np.ndarray, meat: float = MEAT_TICKS) -> bool:
    """True if a meat-sized push is followed by a meat-sized retrace from its extreme."""
    first_i = None
    first_dir = 0
    for i, x in enumerate(path_ticks):
        if x >= meat:
            first_i, first_dir = i, 1
            break
        if x <= -meat:
            first_i, first_dir = i, -1
            break
    if first_i is None:
        return False
    if first_dir == 1:
        peak = float(path_ticks[first_i])
        for x in path_ticks[first_i:]:
            peak = max(peak, float(x))
            if peak - float(x) >= meat:
                return True
        return False
    trough = float(path_ticks[first_i])
    for x in path_ticks[first_i:]:
        trough = min(trough, float(x))
        if float(x) - trough >= meat:
            return True
    return False


def three_before_two(
    path_ticks: np.ndarray,
    meat: float = MEAT_TICKS,
    adverse: float = ADVERSE_TICKS,
) -> bool:
    """Either direction: a meat swing prints before a 2-tick move against that swing."""
    up_hit = up_block = down_hit = down_block = False
    for x in path_ticks[1:]:
        value = float(x)
        if not up_hit and not up_block:
            if value <= -adverse:
                up_block = True
            elif value >= meat:
                up_hit = True
        if not down_hit and not down_block:
            if value >= adverse:
                down_block = True
            elif value <= -meat:
                down_hit = True
        if up_hit or down_hit:
            return True
    return False


def label_forward_window(prices: np.ndarray, meat: float, contain: float) -> dict[str, float]:
    """Label one complete forward path. prices[0] is now; the rest are future bars."""
    if len(prices) < 2 or not np.isfinite(prices).all():
        return {"meat": np.nan, "contained": np.nan, "two_way": np.nan, "zone": np.nan, "path": np.nan}
    path = (prices - prices[0]) / TICK_SIZE
    meat_ok = float((np.nanmax(prices) - np.nanmin(prices)) / TICK_SIZE >= meat)
    contained = float(abs(float(path[-1])) < contain)
    two_way = float(first_reversal(path, meat=meat))
    zone = float(bool(meat_ok) and bool(contained) and bool(two_way))
    path_ok = float(three_before_two(path, meat=meat))
    return {
        "meat": meat_ok,
        "contained": contained,
        "two_way": two_way,
        "zone": zone,
        "path": path_ok,
    }


def add_lifo_labels(
    df: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = HORIZONS,
    meat: float = MEAT_TICKS,
    contain: float = CONTAIN_TICKS,
) -> pd.DataFrame:
    """Same-day forward labels. Incomplete horizons stay NaN (censored)."""
    parts: list[pd.DataFrame] = []
    for _, day in df.groupby("et_date", sort=False):
        d = day.sort_index().copy()
        price = d["price"].to_numpy(dtype=float)
        n = len(d)
        for h in horizons:
            meat_c = np.full(n, np.nan)
            contained = np.full(n, np.nan)
            two_way = np.full(n, np.nan)
            zone = np.full(n, np.nan)
            path = np.full(n, np.nan)
            last = n - h
            for i in range(max(last, 0)):
                lab = label_forward_window(price[i : i + h + 1], meat, contain)
                meat_c[i] = lab["meat"]
                contained[i] = lab["contained"]
                two_way[i] = lab["two_way"]
                zone[i] = lab["zone"]
                path[i] = lab["path"]
            d[f"lifo_meat_{int(h)}m"] = meat_c
            d[f"lifo_contained_{int(h)}m"] = contained
            d[f"lifo_twoway_{int(h)}m"] = two_way
            d[f"lifo_zone_{int(h)}m"] = zone
            d[f"lifo_path_{int(h)}m"] = path
        trail = d["price"].rolling(TRAIL_MEAT_MIN, min_periods=TRAIL_MEAT_MIN).max() - d[
            "price"
        ].rolling(TRAIL_MEAT_MIN, min_periods=TRAIL_MEAT_MIN).min()
        d["trail_meat_15m"] = (trail / TICK_SIZE >= meat).fillna(False)
        parts.append(d)
    return pd.concat(parts).sort_index()


def _expanding_q(series: pd.Series, dates: pd.Series, q: float, min_periods: int = EXPANDING_MIN) -> pd.Series:
    return series.groupby(dates, sort=False).transform(
        lambda s: s.shift(1).expanding(min_periods=min_periods).quantile(q)
    )


def _is_rth(ts: pd.Timestamp) -> bool:
    et = ts.tz_convert(EASTERN)
    return RTH_START <= (et.hour * 60 + et.minute) < RTH_END


def add_atr_range_flag(df: pd.DataFrame, h: int = 30, k: float = 1.0) -> pd.DataFrame:
    """Diagnostic overlap only: foundations CONTINUED_RANGE at primary-ish 30m/k=1."""
    out = df.copy()
    rth = out[out.index.map(_is_rth)]
    if rth.empty:
        out["continued_range_30m"] = False
        return out
    daily = rth.groupby("et_date")["price"].agg(["min", "max"])
    daily["atr_ticks"] = (daily["max"] - daily["min"]) / TICK_SIZE
    dates = sorted(daily.index.tolist())
    prior = {dates[i]: float(daily.loc[dates[i - 1], "atr_ticks"]) for i in range(1, len(dates))}
    atr = out["et_date"].map(prior).astype(float)
    unit = atr * float(np.sqrt(h / RTH_MINUTES))
    rng = pd.to_numeric(out.get(f"lifo_meat_{h}m"), errors="coerce")
    # meat flag is 0/1; recover range ticks from a dedicated column if present
    if f"fwd_range_ticks_{h}m" in out.columns:
        range_ticks = pd.to_numeric(out[f"fwd_range_ticks_{h}m"], errors="coerce")
    else:
        range_ticks = pd.Series(np.nan, index=out.index)
        for _, day in out.groupby("et_date", sort=False):
            price = day["price"].astype(float)
            fwd_high = price.iloc[::-1].rolling(h + 1, min_periods=h + 1).max().iloc[::-1]
            fwd_low = price.iloc[::-1].rolling(h + 1, min_periods=h + 1).min().iloc[::-1]
            range_ticks.loc[day.index] = (fwd_high - fwd_low) / TICK_SIZE
        range_ticks.loc[out.groupby("et_date").tail(h).index] = np.nan
    out["continued_range_30m"] = (range_ticks <= (k * unit)) & range_ticks.notna() & unit.notna()
    _ = rng  # kept for readability; overlap uses continued_range_30m vs lifo_zone
    return out


def add_flow_flags(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    dates = out["et_date"]
    z = pd.to_numeric(out["v1_residual_z"], errors="coerce")
    intensity = pd.to_numeric(out["trade_intensity"], errors="coerce")
    int_lo = _expanding_q(intensity, dates, INTENSITY_Q)
    int_hi = _expanding_q(intensity, dates, 0.67)
    out["h5"] = (z.abs() >= LARGE_ABS_Z) & (intensity <= int_lo) & intensity.notna() & z.notna()
    out["h5_and_trail_meat"] = out["h5"] & out["trail_meat_15m"].fillna(False)
    out["h5_quiet_dead"] = out["h5"] & ~out["trail_meat_15m"].fillna(False)
    widen = out.get("residual_widening_15m", pd.Series(False, index=out.index)).fillna(False)
    out["h5_and_widen"] = out["h5"] & widen
    out["large_z"] = z.abs() >= LARGE_ABS_Z
    out["intensity_lo"] = (intensity <= int_lo) & intensity.notna()
    out["intensity_hi"] = (intensity >= int_hi) & intensity.notna()

    buysell = pd.to_numeric(out["buysell_ratio"], errors="coerce")
    sign = np.sign(buysell.fillna(0.0))
    flip = (sign != sign.shift(1)) & (sign != 0) & (sign.shift(1) != 0)
    out["buysell_flips_15m"] = flip.groupby(dates, sort=False).transform(
        lambda s: s.rolling(15, min_periods=5).sum()
    )
    flip_hi = _expanding_q(out["buysell_flips_15m"], dates, 0.67)
    out["buysell_two_way"] = (out["buysell_flips_15m"] >= flip_hi) & out["buysell_flips_15m"].notna()

    disagree = out.get("flow_price_disagree_15m", pd.Series(False, index=out.index)).fillna(False)
    agree = out.get("flow_price_agree_15m", pd.Series(False, index=out.index)).fillna(False)
    out["disagree_15m"] = disagree
    out["agree_15m"] = agree
    out["h5_and_disagree"] = out["h5"] & disagree
    out["h5_and_flips"] = out["h5"] & out["buysell_two_way"].fillna(False)

    mom_ticks = pd.to_numeric(out["mom_5m"], errors="coerce") / TICK_SIZE
    wing = pd.to_numeric(out.get("FV_agg_5m"), errors="coerce").abs() + pd.to_numeric(
        out.get("US_agg_5m"), errors="coerce"
    ).abs()
    wing_hi = _expanding_q(wing, dates, 0.67)
    out["wing_vs_ty"] = (mom_ticks.abs() < 2.0) & (wing >= wing_hi) & wing.notna()
    out["anti_lifo_aggress"] = out["intensity_hi"].fillna(False) & agree
    out["anti_lifo_h5_off_hot"] = (~out["h5"].fillna(False)) & out["intensity_hi"].fillna(False)
    large_share = pd.to_numeric(out.get("large_trade_share"), errors="coerce")
    share_lo = _expanding_q(large_share, dates, INTENSITY_Q)
    out["large_share_lo"] = (large_share <= share_lo) & large_share.notna()
    out["last_push_3t"] = mom_ticks.abs() >= MEAT_TICKS
    out["fade_last_push_sign"] = np.where(out["last_push_3t"], -np.sign(mom_ticks), 0.0)
    return out


def fade_path_hit(
    prices: np.ndarray,
    direction: float,
    *,
    meat: float = MEAT_TICKS,
    adverse: float = ADVERSE_TICKS,
) -> float:
    """1 if fade makes meat ticks before adverse, 0 if adverse first, nan if neither."""
    if direction == 0 or len(prices) < 2:
        return np.nan
    path = direction * (prices - prices[0]) / TICK_SIZE
    for x in path[1:]:
        if float(x) >= meat:
            return 1.0
        if float(x) <= -adverse:
            return 0.0
    return np.nan


def add_edge_clock(df: pd.DataFrame, flag_col: str, clock: int = PATH_CLOCK_MIN) -> pd.Series:
    hits = pd.Series(np.nan, index=df.index, dtype=float)
    for _, day in df.groupby("et_date", sort=False):
        price = day["price"].to_numpy(dtype=float)
        sign = day["fade_last_push_sign"].to_numpy(dtype=float)
        on = day[flag_col].fillna(False).to_numpy()
        n = len(day)
        vals = np.full(n, np.nan)
        last = n - clock
        for i in range(max(last, 0)):
            if not on[i] or sign[i] == 0:
                continue
            vals[i] = fade_path_hit(price[i : i + clock + 1], float(sign[i]))
        hits.loc[day.index] = vals
    return hits


ZONE_FLAGS = [
    ("h5", "A1", "zone"),
    ("h5_and_trail_meat", "A2", "zone"),
    ("h5_quiet_dead", "A2", "zone"),
    ("h5_and_widen", "A4", "zone"),
    ("large_z", "A1", "zone"),
    ("intensity_lo", "A1", "zone"),
    ("h5_and_disagree", "B", "zone"),
    ("h5_and_flips", "B", "zone"),
    ("buysell_two_way", "B", "zone"),
    ("disagree_15m", "B", "zone"),
    ("wing_vs_ty", "B", "zone"),
    ("large_share_lo", "B", "zone"),
]

VETO_FLAGS = [
    ("anti_lifo_aggress", "D"),
    ("anti_lifo_h5_off_hot", "D"),
    ("agree_15m", "D"),
    ("intensity_hi", "D"),
]


def _score_binary(
    panel: pd.DataFrame,
    dates: list[str],
    mask: pd.Series,
    outcome: str,
    *,
    decluster: bool,
) -> dict[str, Any]:
    sub = panel.loc[panel["et_date"].isin(set(dates)) & ~panel["event_near"].fillna(False)].copy()
    valid = sub[outcome].notna()
    base = sub.loc[valid, outcome]
    baseline = float(base.mean()) if len(base) else np.nan
    fired = sub.loc[valid & mask.reindex(sub.index).fillna(False)]
    if decluster and not fired.empty:
        fired = fast_decluster(fired, pd.Series(True, index=fired.index), stride_min=EPISODE_STRIDE_MIN)
    y = pd.to_numeric(fired[outcome], errors="coerce").dropna() if not fired.empty else pd.Series(dtype=float)
    rate = float(y.mean()) if len(y) else np.nan
    lift = (rate - baseline) if np.isfinite(rate) and np.isfinite(baseline) else np.nan
    groups = fired.loc[y.index, "et_date"].astype(str).tolist() if len(y) else []
    ci = bootstrap_ci(y.to_numpy() - baseline if len(y) else [], n_boot=N_BOOT, seed=7, groups=groups or None)
    return {
        "n": int(len(y)),
        "n_dates": int(fired["et_date"].nunique()) if len(y) else 0,
        "rate": rate,
        "baseline": baseline,
        "lift_pp": float(lift * 100.0) if np.isfinite(lift) else np.nan,
        "one_sided_p": one_sided_p((y - baseline).to_numpy()) if len(y) >= 2 else np.nan,
        "one_sided_lower_pp": float(ci["one_sided_lower"] * 100.0) if np.isfinite(ci.get("one_sided_lower", np.nan)) else np.nan,
        "ci_clears": bool(np.isfinite(ci.get("one_sided_lower", np.nan)) and ci["one_sided_lower"] > 0),
        "baseline_n": int(len(base)),
    }


def _gate(row: dict[str, Any]) -> str:
    if row["n"] < MIN_N or row["n_dates"] < MIN_DATES:
        return "FAIL"
    if not np.isfinite(row.get("lift_pp", np.nan)):
        return "FAIL"
    if row["lift_pp"] < MIN_LIFT_PP:
        return "FAIL"
    if not row.get("ci_clears"):
        return "PROVISIONAL" if row["lift_pp"] >= MIN_LIFT_PP else "FAIL"
    return "PASS"


def score_splits(panel: pd.DataFrame, splits: dict[str, list[str]], outcome: str) -> pd.DataFrame:
    rows = []
    for name, branch, _kind in ZONE_FLAGS:
        if name not in panel.columns:
            continue
        mask = panel[name].fillna(False)
        for split, dates in splits.items():
            if split == "all":
                continue
            stats = _score_binary(panel, dates, mask, outcome, decluster=True)
            stats.update({"flag": name, "branch": branch, "split": split, "outcome": outcome, "role": "zone"})
            stats["gate"] = _gate(stats)
            rows.append(stats)
    for name, branch in VETO_FLAGS:
        if name not in panel.columns:
            continue
        mask = panel[name].fillna(False)
        for split, dates in splits.items():
            if split == "all":
                continue
            stats = _score_binary(panel, dates, mask, outcome, decluster=True)
            # veto wants negative lift on the zone
            veto = dict(stats)
            raw_lift = stats.get("lift_pp", np.nan)
            veto["zone_lift_pp"] = raw_lift
            veto["lift_pp"] = -float(raw_lift) if np.isfinite(raw_lift) else np.nan
            veto["ci_clears"] = bool(
                np.isfinite(raw_lift) and raw_lift < 0 and stats["n"] >= MIN_N
            )
            veto.update({"flag": name, "branch": branch, "split": split, "outcome": outcome, "role": "veto"})
            veto["gate"] = _gate(veto)
            rows.append(veto)
    return pd.DataFrame(rows)


def score_edge(panel: pd.DataFrame, splits: dict[str, list[str]]) -> pd.DataFrame:
    rows = []
    for flag in ("h5", "h5_and_trail_meat", "h5_and_flips"):
        if flag not in panel.columns:
            continue
        col = f"edge_{flag}"
        panel[col] = add_edge_clock(panel, flag)
        for split, dates in splits.items():
            if split == "all":
                continue
            sub = panel.loc[panel["et_date"].isin(set(dates)) & ~panel["event_near"].fillna(False)]
            y = pd.to_numeric(sub[col], errors="coerce").dropna()
            if y.empty:
                rows.append({
                    "flag": flag,
                    "branch": "C",
                    "split": split,
                    "n": 0,
                    "n_dates": 0,
                    "hit": np.nan,
                    "after_cost_0_5": np.nan,
                    "gate": "FAIL",
                })
                continue
            # decluster on rows that fired the clock
            fired = sub.loc[sub[col].notna()]
            fired = fast_decluster(fired, pd.Series(True, index=fired.index), stride_min=EPISODE_STRIDE_MIN)
            y = pd.to_numeric(fired[col], errors="coerce").dropna()
            hit = float(y.mean()) if len(y) else np.nan
            # last-print meat is 3 ticks; 0.5-tick cost on a binary hit is not ticks — report hit and tick expectancy
            # +3 on hit, -2 on miss, unresolved dropped
            ticks = y.map(lambda v: MEAT_TICKS if v >= 0.5 else -ADVERSE_TICKS)
            rows.append({
                "flag": flag,
                "branch": "C",
                "split": split,
                "n": int(len(y)),
                "n_dates": int(fired["et_date"].nunique()) if len(y) else 0,
                "hit": hit,
                "avg_ticks": float(ticks.mean()) if len(ticks) else np.nan,
                "after_cost_0_5": float(ticks.mean() - 0.5) if len(ticks) else np.nan,
                "gate": "FAIL",
            })
    return pd.DataFrame(rows)


def write_report(
    label_base: dict[str, Any],
    journal: pd.DataFrame,
    edge: pd.DataFrame,
    overlap: dict[str, Any],
    splits: dict[str, list[str]],
    path: Path,
) -> None:
    primary = journal[(journal["outcome"] == f"lifo_zone_{PRIMARY_H}m") & (journal["split"] == "validation")]
    lines = [
        "# Aggression V2 LIFO-Zone Experiments",
        "",
        "Research-only. Live V1 / V2 / V2.2 / V2.7 were not modified, restarted, or written.",
        f"Output: `{OUTPUT_DIR.as_posix()}`",
        f"Run: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Goal",
        "",
        "Identify **LIFO zones**: two-way boxes with at least 3 ticks of meat, from flow.",
        "Not a fade-to-fair-value clock. Not the 2026-07-17 CONTINUED_RANGE label.",
        "",
        "## Branch 0 label (frozen)",
        "",
        f"- Horizons: {list(HORIZONS)} minutes, primary **{PRIMARY_H}m**",
        f"- Meat: forward high–low ≥ {MEAT_TICKS:g} ticks",
        f"- Contained: |net| < {CONTAIN_TICKS:g} ticks",
        f"- Two-way: first {MEAT_TICKS:g}-tick push, then a {MEAT_TICKS:g}-tick retrace from that extreme",
        f"- Path: a {MEAT_TICKS:g}-tick swing before a {ADVERSE_TICKS:g}-tick move against that swing",
        f"- Event-near ±30m censored. Incomplete horizons censored.",
        "",
        "## Base rates (nonevent, scored minutes)",
        "",
    ]
    for key, value in label_base.items():
        lines.append(f"- `{key}`: {value}")
    lines += [
        "",
        "## Overlap vs CONTINUED_RANGE (30m, k=1 ATR)",
        "",
        f"- P(LIFO_ZONE 15m | CONTINUED_RANGE 30m): {overlap.get('p_zone_given_atr')}",
        f"- P(CONTINUED_RANGE 30m | LIFO_ZONE 15m): {overlap.get('p_atr_given_zone')}",
        "",
        "## Validation (primary 15m zone, declustered 15m)",
        "",
        "| Flag | Branch | n | dates | rate | baseline | lift pp | CI>0 | gate |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    show = primary.sort_values(["role", "lift_pp"], ascending=[True, False])
    def _fmt(value: Any, spec: str) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "n/a"
        if not np.isfinite(number):
            return "n/a"
        return format(number, spec)

    for _, row in show.iterrows():
        lines.append(
            f"| `{row['flag']}` | {row['branch']} | {row['n']} | {row['n_dates']} | "
            f"{_fmt(row['rate'], '.3f')} | {_fmt(row['baseline'], '.3f')} | {_fmt(row['lift_pp'], '.2f')} | "
            f"{row['ci_clears']} | {row['gate']} |"
        )
    lines += ["", "## Branch C edge clock (fade last 3-tick / 5m push, 10m, 3 before 2)", ""]
    if edge.empty:
        lines.append("No edge rows.")
    else:
        lines.append("| Flag | split | n | hit | avg ticks | after 0.5 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
        for _, row in edge.iterrows():
            lines.append(
                f"| `{row['flag']}` | {row['split']} | {row['n']} | "
                f"{row.get('hit')} | {row.get('avg_ticks')} | {row.get('after_cost_0_5')} |"
            )
    lines += [
        "",
        "## Splits",
        "",
        f"- Discovery: {splits['discovery'][0]} … {splits['discovery'][-1]} ({len(splits['discovery'])} dates)" if splits["discovery"] else "- Discovery: empty",
        f"- Validation: {splits['validation'][0]} … {splits['validation'][-1]} ({len(splits['validation'])} dates)" if splits["validation"] else "- Validation: empty",
        f"- FINAL: {splits['final'][0]} … {splits['final'][-1]} ({len(splits['final'])} dates)" if splits["final"] else "- FINAL: empty",
        "",
        "`ship_gate` is false unless a zone cell is PASS on validation and FINAL CI clears. This run does not authorize a live banner change.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading features...", flush=True)
    features, meta = load_features(FEATURE_MATRIX)
    features["event_near"] = _near_event(features.index, _event_times(EVENTS_DB))
    print("Causal V1 residual + dynamics...", flush=True)
    panel = fit_causal_v1_residual(features)
    panel = add_dispersion_dynamics(panel)
    print("LIFO labels...", flush=True)
    panel = add_lifo_labels(panel)
    panel = add_atr_range_flag(panel)
    print("Flow flags...", flush=True)
    panel = add_flow_flags(panel)
    splits = chronological_splits(meta["clean_dates"])

    nonevent = panel.loc[~panel["event_near"].fillna(False)]
    label_base: dict[str, Any] = {
        "rows_clean": meta["rows_clean"],
        "dates_clean": meta["dates_clean"],
    }
    for h in HORIZONS:
        col = f"lifo_zone_{h}m"
        scored = nonevent[col].dropna()
        label_base[f"zone_{h}m_rate"] = float(scored.mean()) if len(scored) else np.nan
        label_base[f"zone_{h}m_n"] = int(len(scored))
        label_base[f"meat_{h}m_rate"] = float(nonevent[f"lifo_meat_{h}m"].dropna().mean())
        label_base[f"contained_{h}m_rate"] = float(nonevent[f"lifo_contained_{h}m"].dropna().mean())
        label_base[f"twoway_{h}m_rate"] = float(nonevent[f"lifo_twoway_{h}m"].dropna().mean())
        label_base[f"path_{h}m_rate"] = float(nonevent[f"lifo_path_{h}m"].dropna().mean())
    atr = nonevent["continued_range_30m"].fillna(False)
    zone15 = nonevent["lifo_zone_15m"] == 1.0
    overlap = {
        "p_zone_given_atr": float(nonevent.loc[atr & nonevent["lifo_zone_15m"].notna(), "lifo_zone_15m"].mean())
        if atr.any()
        else np.nan,
        "p_atr_given_zone": float(atr.loc[zone15].mean()) if zone15.any() else np.nan,
        "n_both": int((atr & zone15).sum()),
    }

    print("Scoring flags...", flush=True)
    parts = [
        score_splits(panel, splits, f"lifo_zone_{PRIMARY_H}m"),
        score_splits(panel, splits, f"lifo_path_{PRIMARY_H}m"),
        score_splits(panel, splits, "lifo_zone_30m"),
    ]
    journal = pd.concat(parts, ignore_index=True)
    print("Branch C edge clocks...", flush=True)
    edge = score_edge(panel, splits)

    journal.to_csv(OUTPUT_DIR / "flag_journal.csv", index=False)
    edge.to_csv(OUTPUT_DIR / "edge_clock.csv", index=False)
    _write_json(
        OUTPUT_DIR / "summary.json",
        {
            "label": label_base,
            "overlap": overlap,
            "splits": {k: v for k, v in splits.items() if k != "all"},
            "ship_gate": False,
            "primary_horizon_m": PRIMARY_H,
            "meat_ticks": MEAT_TICKS,
            "contain_ticks": CONTAIN_TICKS,
        },
    )
    write_report(label_base, journal, edge, overlap, splits, OUTPUT_DIR / "readout.md")
    docs = PROJECT_ROOT / "docs" / "aggression_v2_lifo_zone_experiments.md"
    write_report(label_base, journal, edge, overlap, splits, docs)
    print(f"Wrote {OUTPUT_DIR}", flush=True)
    print(json.dumps({"zone_15m": label_base.get("zone_15m_rate"), "overlap": overlap}, default=_json_default), flush=True)


if __name__ == "__main__":
    main()
