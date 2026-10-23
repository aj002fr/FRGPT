#!/usr/bin/env python3
"""Offline L1–L4 LIFO-zone follow-ups plus Track P/F diagnostics.

Primary target remains Branch-0 lifo_zone_15m. Track P (same-bar ΔP ~ agg)
and Track F (flow-state persistence) are diagnostics, not letter gates.

Guardrail: does not import, write to, or restart V1/V1.2/V2/V2.1/V2.2/V2.3/V2.6/V2.7/V3.
Reads the published TBBO matrix and events DB only.
Writes only under workspace/analysis/aggression_v2_lifo_literature/.
Does not overwrite aggression_v2_lifo_zone/ or tbbo_feature_matrix.csv.
Does not retune H5. ship_gate is always false.
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
from scripts.aggression_gate_lib import bootstrap_ci  # noqa: E402
from scripts.aggression_v2_lifo_zone_research import (  # noqa: E402
    ADVERSE_TICKS,
    MEAT_TICKS,
    MIN_DATES,
    MIN_LIFT_PP,
    MIN_N,
    N_BOOT,
    PRIMARY_H,
    _expanding_q,
    _gate,
    _json_default,
    _score_binary,
    add_edge_clock,
    add_flow_flags,
    add_lifo_labels,
)

OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_lifo_literature"
WINDOW_NU = 15
V_STAR = 2000
RUN_CUT = 10
FLIPPY_MAX = 2
SHIP_GATE = False


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def trailing_nu(agg: pd.Series, window: int = WINDOW_NU) -> pd.Series:
    """|sum agg| / sum |agg| over a trailing window. 0 = two-sided, 1 = one-sided."""
    signed = agg.rolling(window, min_periods=window).sum()
    abs_sum = agg.abs().rolling(window, min_periods=window).sum()
    return signed.abs() / abs_sum.replace(0.0, np.nan)


def sign_run_length(agg: pd.Series) -> pd.Series:
    """Causal same-sign run of agg. Resets on 0 or a flip."""
    signs = np.sign(pd.to_numeric(agg, errors="coerce").fillna(0.0).to_numpy(dtype=float))
    out = np.zeros(len(signs), dtype=float)
    for i, sign in enumerate(signs):
        if sign == 0.0:
            out[i] = 0.0
        elif i == 0 or sign != signs[i - 1]:
            out[i] = 1.0
        else:
            out[i] = out[i - 1] + 1.0
    return pd.Series(out, index=agg.index)


def volume_bucket_nu(agg: pd.Series, vol: pd.Series, v_star: float = V_STAR) -> pd.Series:
    """Nu of the last completed volume bucket (causal). NaN until the first fill."""
    a = pd.to_numeric(agg, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    v = pd.to_numeric(vol, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    out = np.full(len(a), np.nan)
    last_nu = np.nan
    bucket_sum = 0.0
    bucket_abs = 0.0
    cum = 0.0
    for i in range(len(a)):
        bucket_sum += a[i]
        bucket_abs += abs(a[i])
        cum += max(float(v[i]), 0.0)
        if cum >= v_star:
            last_nu = abs(bucket_sum) / bucket_abs if bucket_abs > 0 else np.nan
            bucket_sum = 0.0
            bucket_abs = 0.0
            cum = 0.0
        out[i] = last_nu
    return pd.Series(out, index=agg.index)


def ols_r2(y: pd.Series, x: pd.Series) -> float:
    """R² of y on x with intercept. NaN if variance is degenerate."""
    pair = pd.DataFrame({"y": pd.to_numeric(y, errors="coerce"), "x": pd.to_numeric(x, errors="coerce")})
    pair = pair.replace([np.inf, -np.inf], np.nan).dropna()
    if len(pair) < 8:
        return float("nan")
    yy = pair["y"].to_numpy(dtype=float)
    xx = pair["x"].to_numpy(dtype=float)
    y_dev = yy - yy.mean()
    x_dev = xx - xx.mean()
    ss_tot = float(np.dot(y_dev, y_dev))
    ss_xx = float(np.dot(x_dev, x_dev))
    if ss_tot <= 0 or ss_xx <= 0:
        return float("nan")
    beta = float(np.dot(x_dev, y_dev) / ss_xx)
    resid = y_dev - beta * x_dev
    return float(1.0 - np.dot(resid, resid) / ss_tot)


def forward_window_nu(agg: pd.Series, dates: pd.Series, horizon: int = WINDOW_NU) -> pd.Series:
    """Nu of the next `horizon` completed bars (t+1 .. t+horizon)."""
    out = pd.Series(np.nan, index=agg.index)
    numeric = pd.to_numeric(agg, errors="coerce")
    for _, idx in dates.groupby(dates, sort=False).groups.items():
        loc = numeric.loc[idx]
        values = loc.to_numpy(dtype=float)
        n = len(values)
        result = np.full(n, np.nan)
        last = n - horizon
        for i in range(max(last, 0)):
            window = values[i + 1 : i + 1 + horizon]
            if len(window) < horizon:
                continue
            denom = float(np.nansum(np.abs(window)))
            if denom <= 0:
                continue
            result[i] = abs(float(np.nansum(window))) / denom
        out.loc[loc.index] = result
    return out


def add_path_stats(df: pd.DataFrame, horizon: int = PRIMARY_H) -> pd.DataFrame:
    """Forward 15m endpoint, max |path|, and retrace share for L4."""
    out = df.copy()
    end = pd.Series(np.nan, index=out.index)
    maxabs = pd.Series(np.nan, index=out.index)
    for _, day in out.groupby("et_date", sort=False):
        price = day["price"].to_numpy(dtype=float)
        n = len(day)
        e = np.full(n, np.nan)
        m = np.full(n, np.nan)
        last = n - horizon
        for i in range(max(last, 0)):
            path = (price[i : i + horizon + 1] - price[i]) / TICK_SIZE
            if not np.isfinite(path).all():
                continue
            e[i] = float(path[-1])
            m[i] = float(np.max(np.abs(path)))
        end.loc[day.index] = e
        maxabs.loc[day.index] = m
    out["fwd_end_ticks_15m"] = end
    out["fwd_maxabs_ticks_15m"] = maxabs
    out["retrace_share_15m"] = end.abs() / maxabs.replace(0.0, np.nan)
    return out


def add_literature_flags(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    dates = out["et_date"]
    agg = pd.to_numeric(out["agg_1m"], errors="coerce")
    intensity = pd.to_numeric(out["trade_intensity"], errors="coerce")
    vol = pd.to_numeric(out.get("vol_per_min"), errors="coerce")
    mom_ticks = pd.to_numeric(out.get("mom_5m"), errors="coerce") / TICK_SIZE
    d_price = out.groupby(dates, sort=False)["price"].diff() / TICK_SIZE

    q_i33 = _expanding_q(intensity, dates, 0.33)
    q_i67 = _expanding_q(intensity, dates, 0.67)
    out["int_mid"] = (intensity > q_i33) & (intensity <= q_i67) & intensity.notna()
    out["int_not_quiet"] = (intensity > q_i33) & intensity.notna()

    out["nu_15"] = agg.groupby(dates, sort=False).transform(lambda s: trailing_nu(s, 15))
    out["nu_5"] = agg.groupby(dates, sort=False).transform(lambda s: trailing_nu(s, 5))
    q_nu33 = _expanding_q(out["nu_15"], dates, 0.33)
    q_nu25 = _expanding_q(out["nu_15"], dates, 0.25)
    q_nu5_33 = _expanding_q(out["nu_5"], dates, 0.33)
    out["twosided"] = (out["nu_15"] <= q_nu33) & out["nu_15"].notna()
    out["twosided_p25"] = (out["nu_15"] <= q_nu25) & out["nu_15"].notna()
    out["twosided_w5"] = (out["nu_5"] <= q_nu5_33) & out["nu_5"].notna()
    out["mod_twosided"] = out["int_mid"] & out["twosided"]
    out["mod_twosided_p25"] = out["int_mid"] & out["twosided_p25"]
    out["notquiet_twosided"] = out["int_not_quiet"] & out["twosided"]
    out["mod_twosided_w5"] = out["int_mid"] & out["twosided_w5"]

    bucket = []
    for _, day in out.groupby(dates, sort=False):
        bucket.append(volume_bucket_nu(day["agg_1m"], day.get("vol_per_min", vol.reindex(day.index))))
    out["nu_vol"] = pd.concat(bucket).sort_index()
    q_vol33 = _expanding_q(out["nu_vol"], dates, 0.33)
    out["twosided_vol"] = (out["nu_vol"] <= q_vol33) & out["nu_vol"].notna()
    out["mod_twosided_vol"] = out["int_mid"] & out["twosided_vol"]

    q_abs67 = _expanding_q(agg.abs(), dates, 0.67)
    out["delta_p_ticks"] = d_price
    out["absorb_bar"] = (agg.abs() >= q_abs67) & (d_price.abs() <= 1.0) & agg.notna() & d_price.notna()
    last_push = mom_ticks.abs() >= MEAT_TICKS
    out["absorb_extreme"] = (
        out["absorb_bar"]
        & out.get("trail_meat_15m", pd.Series(False, index=out.index)).fillna(False)
        & last_push.fillna(False)
    )
    if "fade_last_push_sign" not in out.columns:
        out["fade_last_push_sign"] = np.where(last_push.fillna(False), -np.sign(mom_ticks), 0.0)
    depth = pd.to_numeric(out.get("displayed_depth_mean"), errors="coerce")
    q_d50 = _expanding_q(depth, dates, 0.50)
    out["absorb_extreme_depth"] = out["absorb_extreme"] & (depth >= q_d50) & depth.notna()

    run = []
    for _, day in out.groupby(dates, sort=False):
        run.append(sign_run_length(day["agg_1m"]))
    out["sign_run"] = pd.concat(run).sort_index()
    out["metaorder_run"] = out["sign_run"] >= RUN_CUT
    out["flippy_active"] = (out["sign_run"] >= 1) & (out["sign_run"] <= FLIPPY_MAX) & out["int_not_quiet"]
    out["q_nu33"] = q_nu33
    return out


def add_track_f_labels(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    dates = out["et_date"]
    out["fwd_nu_15"] = forward_window_nu(out["agg_1m"], dates, WINDOW_NU)
    out["fwd_twosided"] = (out["fwd_nu_15"] <= out["q_nu33"]) & out["fwd_nu_15"].notna() & out["q_nu33"].notna()
    shifted = []
    for _, day in out.groupby(dates, sort=False):
        shifted.append(day["sign_run"].shift(-WINDOW_NU))
    out["fwd_sign_run_15"] = pd.concat(shifted).sort_index()
    out["fwd_still_run"] = out["fwd_sign_run_15"] >= RUN_CUT
    return out


ZONE_FLAGS = [
    ("mod_twosided", "L1", "zone"),
    ("twosided", "L1", "zone"),
    ("int_mid", "L1", "zone"),
    ("mod_twosided_p25", "L1d", "zone"),
    ("notquiet_twosided", "L1d", "zone"),
    ("mod_twosided_w5", "L1d", "zone"),
    ("mod_twosided_vol", "L1d", "zone"),
    ("absorb_extreme", "L2", "zone"),
    ("absorb_bar", "L2", "zone"),
    ("absorb_extreme_depth", "L2d", "zone"),
    ("flippy_active", "L3", "zone"),
    ("h5", "H5", "zone"),
]

VETO_FLAGS = [
    ("metaorder_run", "L3"),
]


def score_andersen(panel: pd.DataFrame, splits: dict[str, list[str]], outcome: str) -> pd.DataFrame:
    """Lift of twosided inside int_mid only."""
    rows = []
    mid = panel["int_mid"].fillna(False)
    restricted = panel.loc[mid].copy()
    if restricted.empty:
        return pd.DataFrame()
    mask = restricted["twosided"].fillna(False)
    for split, dates in splits.items():
        if split == "all":
            continue
        stats = _score_binary(restricted, dates, mask, outcome, decluster=True)
        stats.update({
            "flag": "twosided_given_int_mid",
            "branch": "L1a",
            "split": split,
            "outcome": outcome,
            "role": "andersen",
        })
        stats["gate"] = _gate(stats)
        rows.append(stats)
    return pd.DataFrame(rows)


def score_zone_flags(panel: pd.DataFrame, splits: dict[str, list[str]], outcome: str) -> pd.DataFrame:
    rows = []
    for name, branch, _kind in ZONE_FLAGS:
        if name not in panel.columns:
            continue
        mask = panel[name].fillna(False)
        for split, dates in splits.items():
            if split == "all":
                continue
            stats = _score_binary(panel, dates, mask, outcome, decluster=True)
            extras = _piece_rates(panel, dates, mask, horizon=PRIMARY_H)
            stats.update(extras)
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
            extras = _piece_rates(panel, dates, mask, horizon=PRIMARY_H)
            veto = dict(stats)
            raw_lift = stats.get("lift_pp", np.nan)
            veto.update(extras)
            veto["zone_lift_pp"] = raw_lift
            veto["lift_pp"] = -float(raw_lift) if np.isfinite(raw_lift) else np.nan
            veto["ci_clears"] = bool(
                np.isfinite(raw_lift) and raw_lift < 0 and stats["n"] >= MIN_N
            )
            if np.isfinite(veto["lift_pp"]) and veto["lift_pp"] >= MIN_LIFT_PP and veto["ci_clears"]:
                veto["gate"] = "PASS" if stats["n"] >= MIN_N and stats["n_dates"] >= MIN_DATES else "FAIL"
            elif np.isfinite(veto["lift_pp"]) and veto["lift_pp"] >= MIN_LIFT_PP:
                veto["gate"] = "PROVISIONAL"
            else:
                veto["gate"] = "FAIL"
            veto.update({"flag": name, "branch": branch, "split": split, "outcome": outcome, "role": "veto"})
            rows.append(veto)
    return pd.DataFrame(rows)


def _piece_rates(panel: pd.DataFrame, dates: list[str], mask: pd.Series, horizon: int) -> dict[str, float]:
    sub = panel.loc[panel["et_date"].isin(set(dates)) & ~panel["event_near"].fillna(False)]
    fired = sub.loc[mask.reindex(sub.index).fillna(False)]
    if fired.empty:
        return {"meat_rate": np.nan, "twoway_rate": np.nan, "path_rate": np.nan}
    fired = fast_decluster(fired, pd.Series(True, index=fired.index), stride_min=EPISODE_STRIDE_MIN)
    return {
        "meat_rate": float(pd.to_numeric(fired[f"lifo_meat_{horizon}m"], errors="coerce").mean()),
        "twoway_rate": float(pd.to_numeric(fired[f"lifo_twoway_{horizon}m"], errors="coerce").mean()),
        "path_rate": float(pd.to_numeric(fired[f"lifo_path_{horizon}m"], errors="coerce").mean()),
    }


def score_edge(panel: pd.DataFrame, splits: dict[str, list[str]]) -> pd.DataFrame:
    rows = []
    for flag in ("absorb_extreme", "absorb_extreme_depth"):
        if flag not in panel.columns:
            continue
        col = f"edge_{flag}"
        panel[col] = add_edge_clock(panel, flag)
        for split, dates in splits.items():
            if split == "all":
                continue
            sub = panel.loc[panel["et_date"].isin(set(dates)) & ~panel["event_near"].fillna(False)]
            fired = sub.loc[sub[col].notna()]
            if not fired.empty:
                fired = fast_decluster(fired, pd.Series(True, index=fired.index), stride_min=EPISODE_STRIDE_MIN)
            y = pd.to_numeric(fired[col], errors="coerce").dropna() if not fired.empty else pd.Series(dtype=float)
            n = int(len(y))
            hit = float(y.mean()) if n else np.nan
            ticks = y.map(lambda v: MEAT_TICKS if v >= 0.5 else -ADVERSE_TICKS) if n else pd.Series(dtype=float)
            if n < MIN_N:
                gate = "FAIL_N"
            elif np.isfinite(hit) and hit >= 0.60:
                groups = fired.loc[y.index, "et_date"].astype(str).tolist()
                ci = bootstrap_ci(
                    (y - 0.50).to_numpy(), n_boot=N_BOOT, seed=7, groups=groups or None
                )
                gate = "PASS" if ci.get("one_sided_lower", -1) > 0 else "PROVISIONAL"
            else:
                gate = "FAIL"
            rows.append({
                "flag": flag,
                "branch": "L2e",
                "split": split,
                "n": n,
                "n_dates": int(fired["et_date"].nunique()) if n else 0,
                "hit": hit,
                "avg_ticks": float(ticks.mean()) if n else np.nan,
                "after_cost_0_5": float(ticks.mean() - 0.5) if n else np.nan,
                "gate": gate,
            })
    return pd.DataFrame(rows)


def score_track_p(panel: pd.DataFrame, splits: dict[str, list[str]]) -> pd.DataFrame:
    rows = []
    nonevent = panel.loc[~panel["event_near"].fillna(False)]
    dp = pd.to_numeric(nonevent.get("delta_p_ticks"), errors="coerce")
    agg = pd.to_numeric(nonevent["agg_1m"], errors="coerce")
    agg_lag = agg.groupby(nonevent["et_date"], sort=False).shift(1)
    for split, dates in splits.items():
        if split == "all":
            continue
        idx = nonevent["et_date"].isin(set(dates))
        rows.append({
            "track": "P",
            "split": split,
            "same_bar_r2": ols_r2(dp.loc[idx], agg.loc[idx]),
            "lead1_r2": ols_r2(dp.loc[idx], agg_lag.loc[idx]),
            "n": int((idx & dp.notna() & agg.notna()).sum()),
        })
    return pd.DataFrame(rows)


def score_track_f(panel: pd.DataFrame, splits: dict[str, list[str]]) -> pd.DataFrame:
    rows = []
    for flag, future, branch in (
        ("twosided", "fwd_twosided", "F_nu"),
        ("metaorder_run", "fwd_still_run", "F_run"),
    ):
        mask = panel[flag].fillna(False)
        for split, dates in splits.items():
            if split == "all":
                continue
            stats = _score_binary(panel, dates, mask, future, decluster=True)
            stats.update({"flag": flag, "branch": branch, "split": split, "outcome": future, "role": "persist"})
            stats["gate"] = "DIAG"
            rows.append(stats)
    return pd.DataFrame(rows)


def score_hasbrouck(panel: pd.DataFrame, splits: dict[str, list[str]]) -> pd.DataFrame:
    rows = []
    abs_agg = pd.to_numeric(panel["agg_1m"], errors="coerce").abs()
    q33 = _expanding_q(abs_agg, panel["et_date"], 0.33)
    q67 = _expanding_q(abs_agg, panel["et_date"], 0.67)
    tercile = pd.Series("mid", index=panel.index)
    tercile.loc[abs_agg <= q33] = "lo"
    tercile.loc[abs_agg > q67] = "hi"
    tercile.loc[abs_agg.isna() | q33.isna()] = np.nan
    nonevent = ~panel["event_near"].fillna(False)
    for split, dates in splits.items():
        if split == "all":
            continue
        sub = panel.loc[nonevent & panel["et_date"].isin(set(dates)) & panel["lifo_zone_15m"].notna()]
        for name in ("lo", "mid", "hi"):
            cell = sub.loc[tercile.reindex(sub.index) == name]
            if cell.empty:
                rows.append({"split": split, "tercile": name, "n": 0})
                continue
            rows.append({
                "split": split,
                "tercile": name,
                "n": int(len(cell)),
                "n_dates": int(cell["et_date"].nunique()),
                "zone_rate": float(cell["lifo_zone_15m"].mean()),
                "twoway_rate": float(cell["lifo_twoway_15m"].mean()),
                "meat_rate": float(cell["lifo_meat_15m"].mean()),
                "median_abs_end": float(cell["fwd_end_ticks_15m"].abs().median()),
                "median_retrace_share": float(cell["retrace_share_15m"].median()),
            })
    return pd.DataFrame(rows)


def _fmt(value: Any, spec: str = ".3f") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(number):
        return "n/a"
    return format(number, spec)


def _validation_letter(journal: pd.DataFrame) -> bool:
    primary = journal[
        (journal["split"] == "validation")
        & (journal["outcome"] == f"lifo_zone_{PRIMARY_H}m")
        & (journal["flag"].isin(("mod_twosided", "flippy_active", "absorb_extreme")))
        & (journal["role"] == "zone")
        & (journal["gate"] == "PASS")
    ]
    veto = journal[
        (journal["split"] == "validation")
        & (journal["flag"] == "metaorder_run")
        & (journal["role"] == "veto")
        & (journal["gate"] == "PASS")
    ]
    return (not primary.empty) or (not veto.empty)


def write_report(
    *,
    label_base: dict[str, Any],
    journal: pd.DataFrame,
    andersen: pd.DataFrame,
    edge: pd.DataFrame,
    track_p: pd.DataFrame,
    track_f: pd.DataFrame,
    hasbrouck: pd.DataFrame,
    splits: dict[str, list[str]],
    show_final: bool,
    path: Path,
) -> None:
    lines = [
        "# Aggression V2 LIFO literature experiments",
        "",
        "Research-only. Live V1 / V2 / V2.2 / V2.7 were not modified, restarted, or written.",
        f"Output: `{OUTPUT_DIR.as_posix()}`",
        f"Run: {datetime.now(timezone.utc).isoformat()}",
        f"`ship_gate`: **{str(SHIP_GATE).lower()}**",
        "",
        "## Which model",
        "",
        "Primary target is still `lifo_zone_15m` (next 15 minutes is a two-way 3-tick box).",
        "Track P is Cont same-bar ΔP ~ agg. Track F is whether today's flow state persists.",
        "Neither Track P nor Track F is a letter gate or a live banner.",
        "",
        "## Label parity (nonevent)",
        "",
    ]
    for key, value in label_base.items():
        lines.append(f"- `{key}`: {value}")
    lines += [
        "",
        "## Track P — same-bar flow–price vs lead-1",
        "",
        "| Split | same-bar R² | lead-1 R² | n |",
        "| --- | ---: | ---: | ---: |",
    ]
    for _, row in track_p.iterrows():
        lines.append(
            f"| {row['split']} | {_fmt(row['same_bar_r2'], '.4f')} | "
            f"{_fmt(row['lead1_r2'], '.4f')} | {row['n']} |"
        )
    lines += [
        "",
        "## Track F — does the flow state persist 15 minutes?",
        "",
        "| Flag | split | n | persist rate | baseline | lift pp |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for _, row in track_f.iterrows():
        if not show_final and row["split"] == "final":
            continue
        lines.append(
            f"| `{row['flag']}` | {row['split']} | {row['n']} | "
            f"{_fmt(row['rate'])} | {_fmt(row['baseline'])} | {_fmt(row['lift_pp'], '.2f')} |"
        )
    show_splits = ["discovery", "validation"] + (["final"] if show_final else [])
    primary = journal[
        (journal["outcome"] == f"lifo_zone_{PRIMARY_H}m")
        & (journal["split"].isin(show_splits))
        & (journal["flag"].isin((
            "mod_twosided", "twosided", "int_mid", "absorb_extreme", "absorb_bar",
            "flippy_active", "h5", "metaorder_run",
        )))
    ]
    lines += [
        "",
        "## Validation / discovery zone lift (primary 15m, declustered)",
        "",
        "FINAL is folded until a starred validation letter exists." if not show_final else "FINAL is shown because a validation letter exists (still not a ship).",
        "",
        "| Flag | Branch | split | n | rate | baseline | lift pp | meat | two-way | gate |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for _, row in primary.sort_values(["split", "branch", "flag"]).iterrows():
        lines.append(
            f"| `{row['flag']}` | {row['branch']} | {row['split']} | {row['n']} | "
            f"{_fmt(row['rate'])} | {_fmt(row['baseline'])} | {_fmt(row['lift_pp'], '.2f')} | "
            f"{_fmt(row.get('meat_rate'))} | {_fmt(row.get('twoway_rate'))} | {row['gate']} |"
        )
    lines += ["", "## L1 Andersen control (twosided inside int_mid)", ""]
    if andersen.empty:
        lines.append("No Andersen rows.")
    else:
        lines.append("| split | n | rate | baseline | lift pp | gate |")
        lines.append("| --- | ---: | ---: | ---: | ---: | --- |")
        for _, row in andersen.iterrows():
            if not show_final and row["split"] == "final":
                continue
            lines.append(
                f"| {row['split']} | {row['n']} | {_fmt(row['rate'])} | "
                f"{_fmt(row['baseline'])} | {_fmt(row['lift_pp'], '.2f')} | {row['gate']} |"
            )
    lines += ["", "## L2 edge clock (fade last 3-tick push, 10m)", ""]
    if edge.empty:
        lines.append("No edge rows.")
    else:
        lines.append("| Flag | split | n | hit | avg ticks | gate |")
        lines.append("| --- | --- | ---: | ---: | ---: | --- |")
        for _, row in edge.iterrows():
            if not show_final and row["split"] == "final":
                continue
            lines.append(
                f"| `{row['flag']}` | {row['split']} | {row['n']} | "
                f"{_fmt(row.get('hit'))} | {_fmt(row.get('avg_ticks'), '.2f')} | {row['gate']} |"
            )
    lines += ["", "## L4 Hasbrouck terciles of |agg_1m| (no letter)", ""]
    hb = hasbrouck[hasbrouck["split"].isin(show_splits)]
    if hb.empty:
        lines.append("No tercile rows.")
    else:
        lines.append("| split | tercile | n | zone | two-way | meat | med |end| | retrace share |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for _, row in hb.iterrows():
            lines.append(
                f"| {row['split']} | {row['tercile']} | {row.get('n', 0)} | "
                f"{_fmt(row.get('zone_rate'))} | {_fmt(row.get('twoway_rate'))} | "
                f"{_fmt(row.get('meat_rate'))} | {_fmt(row.get('median_abs_end'), '.2f')} | "
                f"{_fmt(row.get('median_retrace_share'))} |"
            )
    lines += [
        "",
        "## Splits",
        "",
        f"- Discovery: {splits['discovery'][0]} … {splits['discovery'][-1]} ({len(splits['discovery'])} dates)" if splits["discovery"] else "- Discovery: empty",
        f"- Validation: {splits['validation'][0]} … {splits['validation'][-1]} ({len(splits['validation'])} dates)" if splits["validation"] else "- Validation: empty",
        f"- FINAL: {splits['final'][0]} … {splits['final'][-1]} ({len(splits['final'])} dates)" if splits["final"] else "- FINAL: empty",
        "",
        "`ship_gate` is false. This run does not authorize a live banner, clock, or H5 change.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading features...", flush=True)
    features, meta = load_features(FEATURE_MATRIX)
    features["event_near"] = _near_event(features.index, _event_times(EVENTS_DB))
    print("Causal V1 residual (H5 control only)...", flush=True)
    panel = fit_causal_v1_residual(features)
    panel = add_dispersion_dynamics(panel)
    print("Branch 0 labels...", flush=True)
    panel = add_lifo_labels(panel)
    panel = add_path_stats(panel)
    print("H5 + literature flags...", flush=True)
    panel = add_flow_flags(panel)
    panel = add_literature_flags(panel)
    panel = add_track_f_labels(panel)
    splits = chronological_splits(meta["clean_dates"])

    nonevent = panel.loc[~panel["event_near"].fillna(False)]
    label_base: dict[str, Any] = {
        "rows_clean": meta["rows_clean"],
        "dates_clean": meta["dates_clean"],
    }
    scored = nonevent["lifo_zone_15m"].dropna()
    label_base["zone_15m_rate"] = float(scored.mean()) if len(scored) else np.nan
    label_base["zone_15m_n"] = int(len(scored))
    label_base["twoway_15m_rate"] = float(nonevent["lifo_twoway_15m"].dropna().mean())
    label_base["meat_15m_rate"] = float(nonevent["lifo_meat_15m"].dropna().mean())

    print("Scoring...", flush=True)
    journal = pd.concat(
        [
            score_zone_flags(panel, splits, f"lifo_zone_{PRIMARY_H}m"),
            score_andersen(panel, splits, f"lifo_zone_{PRIMARY_H}m"),
        ],
        ignore_index=True,
    )
    andersen = journal[journal["role"] == "andersen"].copy()
    journal_flags = journal[journal["role"] != "andersen"].copy()
    edge = score_edge(panel, splits)
    track_p = score_track_p(panel, splits)
    track_f = score_track_f(panel, splits)
    hasbrouck = score_hasbrouck(panel, splits)
    show_final = _validation_letter(journal_flags)

    journal.to_csv(OUTPUT_DIR / "flag_journal.csv", index=False)
    edge.to_csv(OUTPUT_DIR / "edge_clock.csv", index=False)
    track_pf = pd.concat(
        [
            track_p.assign(kind="P"),
            track_f.assign(kind="F"),
        ],
        ignore_index=True,
        sort=False,
    )
    track_pf.to_csv(OUTPUT_DIR / "track_pf.csv", index=False)
    hasbrouck.to_csv(OUTPUT_DIR / "hasbrouck_terciles.csv", index=False)
    _write_json(
        OUTPUT_DIR / "summary.json",
        {
            "label": label_base,
            "splits": {k: v for k, v in splits.items() if k != "all"},
            "ship_gate": SHIP_GATE,
            "show_final": show_final,
            "track_p": track_p.to_dict(orient="records"),
        },
    )
    write_report(
        label_base=label_base,
        journal=journal_flags,
        andersen=andersen,
        edge=edge,
        track_p=track_p,
        track_f=track_f,
        hasbrouck=hasbrouck,
        splits=splits,
        show_final=show_final,
        path=OUTPUT_DIR / "readout.md",
    )
    print(f"Wrote {OUTPUT_DIR}", flush=True)
    print(json.dumps({"zone_15m": label_base.get("zone_15m_rate"), "show_final": show_final}, default=_json_default), flush=True)


if __name__ == "__main__":
    main()
