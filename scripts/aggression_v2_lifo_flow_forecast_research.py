#!/usr/bin/env python3
"""Offline diagnostic: past flow → future flow, then flow→price vs direct price.

Does not replace the Branch-0 zone exam. Asks two questions on the published
TBBO panel:

1. Does signed aggression over the last W minutes forecast signed aggression
   over the next H minutes? W in {1,5,15,30}, H in {1,5,15}.
2. If we form that flow forecast and apply the contemporaneous Cont map
   (same-bar ΔP ≈ λ × agg), is the out-of-sample price forecast better than
   regressing future ΔP on past flow directly?

A linear flow forecast of a single past sum is an affine transform of that
sum, so in-sample R²(ΔP, F̂) equals R²(ΔP, past). The comparison that can
differ is out-of-sample *forecast* R²: frozen same-bar λ × F̂ versus a
discovery-fit direct map. The oracle R²(future ΔP, future flow) is the
upper bound if stage 1 were perfect.

Guardrail: does not import, write to, or restart V1/V1.2/V2/V2.1/V2.2/V2.3/V2.6/V2.7/V3.
Reads the published TBBO matrix and events DB only.
Writes only under workspace/analysis/aggression_v2_lifo_flow_forecast/.
Does not overwrite aggression_v2_lifo_zone/, aggression_v2_lifo_literature/,
or tbbo_feature_matrix.csv. ship_gate is always false.
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

from scripts.aggression_v1_flow_price_dispersion_research import (  # noqa: E402
    EVENTS_DB,
    FEATURE_MATRIX,
    TICK_SIZE,
    _event_times,
    _near_event,
    chronological_splits,
    load_features,
)
from scripts.aggression_v2_lifo_literature_research import ols_r2  # noqa: E402
from scripts.aggression_v2_lifo_zone_research import _json_default  # noqa: E402

OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_lifo_flow_forecast"
PAST_W = (1, 5, 15, 30)
FUTURE_H = (1, 5, 15)
SHIP_GATE = False


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def forecast_r2(y: pd.Series, yhat: pd.Series) -> float:
    """1 - SSE / SST using the frozen forecast. Can be negative."""
    pair = pd.DataFrame({
        "y": pd.to_numeric(y, errors="coerce"),
        "yhat": pd.to_numeric(yhat, errors="coerce"),
    }).replace([np.inf, -np.inf], np.nan).dropna()
    if len(pair) < 8:
        return float("nan")
    yy = pair["y"].to_numpy(dtype=float)
    resid = yy - pair["yhat"].to_numpy(dtype=float)
    y_dev = yy - yy.mean()
    ss_tot = float(np.dot(y_dev, y_dev))
    if ss_tot <= 0:
        return float("nan")
    return float(1.0 - np.dot(resid, resid) / ss_tot)


def ols_fit(y: pd.Series, x: pd.Series) -> tuple[float, float, float, int]:
    """Intercept, slope, in-sample R², n."""
    pair = pd.DataFrame({
        "y": pd.to_numeric(y, errors="coerce"),
        "x": pd.to_numeric(x, errors="coerce"),
    }).replace([np.inf, -np.inf], np.nan).dropna()
    n = int(len(pair))
    if n < 8:
        return float("nan"), float("nan"), float("nan"), n
    yy = pair["y"].to_numpy(dtype=float)
    xx = pair["x"].to_numpy(dtype=float)
    x_mean = float(xx.mean())
    y_mean = float(yy.mean())
    x_dev = xx - x_mean
    y_dev = yy - y_mean
    ss_xx = float(np.dot(x_dev, x_dev))
    ss_tot = float(np.dot(y_dev, y_dev))
    if ss_xx <= 0 or ss_tot <= 0:
        return float("nan"), float("nan"), float("nan"), n
    slope = float(np.dot(x_dev, y_dev) / ss_xx)
    intercept = y_mean - slope * x_mean
    resid = y_dev - slope * x_dev
    r2 = float(1.0 - np.dot(resid, resid) / ss_tot)
    return intercept, slope, r2, n


def ols_fit_multi(y: pd.Series, frame: pd.DataFrame) -> tuple[np.ndarray, float, int]:
    """Least squares with intercept. Returns coef (intercept first), R², n."""
    work = pd.concat(
        [pd.to_numeric(y, errors="coerce").rename("y"), frame.apply(pd.to_numeric, errors="coerce")],
        axis=1,
    ).replace([np.inf, -np.inf], np.nan).dropna()
    n = int(len(work))
    if n < 8 or work.shape[1] < 2:
        return np.array([np.nan]), float("nan"), n
    yy = work["y"].to_numpy(dtype=float)
    xx = work.drop(columns=["y"]).to_numpy(dtype=float)
    design = np.column_stack([np.ones(n), xx])
    coef, *_ = np.linalg.lstsq(design, yy, rcond=None)
    yhat = design @ coef
    y_dev = yy - yy.mean()
    ss_tot = float(np.dot(y_dev, y_dev))
    if ss_tot <= 0:
        return coef, float("nan"), n
    resid = yy - yhat
    return coef, float(1.0 - np.dot(resid, resid) / ss_tot), n


def apply_multi(frame: pd.DataFrame, coef: np.ndarray) -> pd.Series:
    work = frame.apply(pd.to_numeric, errors="coerce")
    design = np.column_stack([np.ones(len(work)), work.to_numpy(dtype=float)])
    return pd.Series(design @ coef, index=frame.index)


def sign_hit(past: pd.Series, future: pd.Series) -> float:
    a = np.sign(pd.to_numeric(past, errors="coerce"))
    b = np.sign(pd.to_numeric(future, errors="coerce"))
    ok = a.notna() & b.notna() & (a != 0) & (b != 0)
    if int(ok.sum()) < 8:
        return float("nan")
    return float((a.loc[ok] == b.loc[ok]).mean())


def add_flow_price_windows(
    df: pd.DataFrame,
    past_w: tuple[int, ...] = PAST_W,
    future_h: tuple[int, ...] = FUTURE_H,
) -> pd.DataFrame:
    """Causal past sums through t; strictly forward sums / endpoint ticks after t."""
    out = df.copy()
    agg = pd.to_numeric(out["agg_1m"], errors="coerce")
    price = pd.to_numeric(out["price"], errors="coerce")
    dates = out["et_date"]
    for window in past_w:
        out[f"past_flow_{window}"] = np.nan
    for horizon in future_h:
        out[f"fwd_flow_{horizon}"] = np.nan
        out[f"fwd_dp_{horizon}"] = np.nan
    dp_same = pd.Series(np.nan, index=out.index)
    for _, idx in dates.groupby(dates, sort=False).groups.items():
        a = agg.loc[idx].to_numpy(dtype=float)
        p = price.loc[idx].to_numpy(dtype=float)
        n = len(a)
        same = np.full(n, np.nan)
        same[1:] = (p[1:] - p[:-1]) / TICK_SIZE
        dp_same.loc[idx] = same
        for window in past_w:
            past = np.full(n, np.nan)
            for i in range(window - 1, n):
                past[i] = float(np.nansum(a[i - window + 1 : i + 1]))
            out.loc[idx, f"past_flow_{window}"] = past
        for horizon in future_h:
            fwd_f = np.full(n, np.nan)
            fwd_p = np.full(n, np.nan)
            last = n - horizon
            for i in range(max(last, 0)):
                window = a[i + 1 : i + 1 + horizon]
                if len(window) < horizon or not np.isfinite(window).all():
                    continue
                if not np.isfinite(p[i]) or not np.isfinite(p[i + horizon]):
                    continue
                fwd_f[i] = float(np.sum(window))
                fwd_p[i] = float((p[i + horizon] - p[i]) / TICK_SIZE)
            out.loc[idx, f"fwd_flow_{horizon}"] = fwd_f
            out.loc[idx, f"fwd_dp_{horizon}"] = fwd_p
    out["delta_p_ticks"] = dp_same
    return out


def _split_mask(panel: pd.DataFrame, dates: list[str]) -> pd.Series:
    nonevent = ~panel["event_near"].fillna(False)
    return nonevent & panel["et_date"].isin(set(dates))


def score_univariate(panel: pd.DataFrame, splits: dict[str, list[str]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for horizon in FUTURE_H:
        fwd_f = panel[f"fwd_flow_{horizon}"]
        fwd_p = panel[f"fwd_dp_{horizon}"]
        for window in PAST_W:
            past = panel[f"past_flow_{window}"]
            for split, dates in splits.items():
                if split == "all":
                    continue
                idx = _split_mask(panel, dates)
                y_f = fwd_f.loc[idx]
                y_p = fwd_p.loc[idx]
                x = past.loc[idx]
                rows.append({
                    "kind": "univariate",
                    "split": split,
                    "past_w": window,
                    "future_h": horizon,
                    "n": int((y_f.notna() & x.notna()).sum()),
                    "flow_r2": ols_r2(y_f, x),
                    "direct_price_r2": ols_r2(y_p, x),
                    "oracle_price_r2": ols_r2(y_p, y_f),
                    "sign_hit": sign_hit(x, y_f),
                })
    return pd.DataFrame(rows)


def score_oos_maps(panel: pd.DataFrame, splits: dict[str, list[str]]) -> pd.DataFrame:
    """Discovery-fit maps applied to validation / FINAL (and replayed on discovery)."""
    disc = _split_mask(panel, splits["discovery"])
    lam_int, lam_slope, lam_r2, lam_n = ols_fit(
        panel.loc[disc, "delta_p_ticks"],
        panel.loc[disc, "agg_1m"],
    )
    rows: list[dict[str, Any]] = []
    for horizon in FUTURE_H:
        fwd_f = panel[f"fwd_flow_{horizon}"]
        fwd_p = panel[f"fwd_dp_{horizon}"]
        for window in PAST_W:
            past = panel[f"past_flow_{window}"]
            f_int, f_slope, f_r2_is, _ = ols_fit(fwd_f.loc[disc], past.loc[disc])
            d_int, d_slope, d_r2_is, _ = ols_fit(fwd_p.loc[disc], past.loc[disc])
            for split, dates in splits.items():
                if split == "all":
                    continue
                idx = _split_mask(panel, dates)
                x = past.loc[idx]
                y_f = fwd_f.loc[idx]
                y_p = fwd_p.loc[idx]
                f_hat = f_int + f_slope * x
                direct_hat = d_int + d_slope * x
                stage_hat = lam_slope * f_hat
                rows.append({
                    "kind": "oos_univariate",
                    "split": split,
                    "past_w": window,
                    "future_h": horizon,
                    "n": int((y_p.notna() & x.notna()).sum()),
                    "lambda_same_bar": lam_slope,
                    "lambda_same_bar_r2": lam_r2,
                    "lambda_n": lam_n,
                    "flow_is_r2": f_r2_is,
                    "flow_forecast_r2": forecast_r2(y_f, f_hat),
                    "direct_is_r2": d_r2_is,
                    "direct_forecast_r2": forecast_r2(y_p, direct_hat),
                    "twostage_forecast_r2": forecast_r2(y_p, stage_hat),
                    "oracle_r2": ols_r2(y_p, y_f),
                })
    return pd.DataFrame(rows)


def score_multivariate(panel: pd.DataFrame, splits: dict[str, list[str]]) -> pd.DataFrame:
    """All past windows together. Direct span of X vs frozen λ × F̂(X)."""
    cols = [f"past_flow_{w}" for w in PAST_W]
    disc = _split_mask(panel, splits["discovery"])
    _, lam_slope, lam_r2, _ = ols_fit(panel.loc[disc, "delta_p_ticks"], panel.loc[disc, "agg_1m"])
    rows: list[dict[str, Any]] = []
    for horizon in FUTURE_H:
        fwd_f = panel[f"fwd_flow_{horizon}"]
        fwd_p = panel[f"fwd_dp_{horizon}"]
        f_coef, f_r2_is, _ = ols_fit_multi(fwd_f.loc[disc], panel.loc[disc, cols])
        d_coef, d_r2_is, _ = ols_fit_multi(fwd_p.loc[disc], panel.loc[disc, cols])
        for split, dates in splits.items():
            if split == "all":
                continue
            idx = _split_mask(panel, dates)
            x = panel.loc[idx, cols]
            y_f = fwd_f.loc[idx]
            y_p = fwd_p.loc[idx]
            f_hat = apply_multi(x, f_coef)
            direct_hat = apply_multi(x, d_coef)
            stage_hat = lam_slope * f_hat
            rows.append({
                "kind": "oos_multivariate",
                "split": split,
                "past_w": "1+5+15+30",
                "future_h": horizon,
                "n": int((y_p.notna() & x.notna().all(axis=1)).sum()),
                "lambda_same_bar": lam_slope,
                "lambda_same_bar_r2": lam_r2,
                "flow_is_r2": f_r2_is,
                "flow_forecast_r2": forecast_r2(y_f, f_hat),
                "direct_is_r2": d_r2_is,
                "direct_forecast_r2": forecast_r2(y_p, direct_hat),
                "twostage_forecast_r2": forecast_r2(y_p, stage_hat),
                "oracle_r2": ols_r2(y_p, y_f),
            })
    return pd.DataFrame(rows)


def _fmt(value: Any, spec: str = ".4f") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(number):
        return "n/a"
    return format(number, spec)


def write_report(
    uni: pd.DataFrame,
    oos: pd.DataFrame,
    multi: pd.DataFrame,
    splits: dict[str, list[str]],
    path: Path,
) -> None:
    lines = [
        "# Aggression V2 LIFO flow-forecast diagnostic",
        "",
        "Research-only. Live V1 / V2 / V2.2 / V2.7 were not modified, restarted, or written.",
        f"Output: `{path.parent}`",
        f"Run: {datetime.now(timezone.utc).isoformat()}",
        "`ship_gate`: **false**",
        "",
        "## Questions",
        "",
        "1. Does past signed flow forecast future signed flow at several (W, H)?",
        "2. Does applying the same-bar Cont λ to that forecast beat a direct future-ΔP ~ past-flow map?",
        "",
        "Oracle is future ΔP ~ *actual* future flow (not a forecast). Two-stage uses discovery-fit flow forecast times discovery same-bar λ. Direct uses discovery-fit future ΔP ~ past flow. Forecast R² can be negative.",
        "",
        "## In-sample R² by split (univariate)",
        "",
        "| Split | W | H | n | flow→flow R² | direct ΔP~past R² | oracle ΔP~future-flow R² | sign hit |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    view = uni.sort_values(["split", "future_h", "past_w"])
    for row in view.itertuples(index=False):
        lines.append(
            f"| {row.split} | {row.past_w} | {row.future_h} | {int(row.n)} | "
            f"{_fmt(row.flow_r2)} | {_fmt(row.direct_price_r2)} | {_fmt(row.oracle_price_r2)} | {_fmt(row.sign_hit, '.3f')} |"
        )
    lines.extend([
        "",
        "## Out-of-sample maps (discovery-fit, univariate)",
        "",
        "| Split | W | H | flow forecast R² | direct price forecast R² | two-stage λ×F̂ R² | oracle |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    oos_view = oos.sort_values(["split", "future_h", "past_w"])
    for row in oos_view.itertuples(index=False):
        lines.append(
            f"| {row.split} | {row.past_w} | {row.future_h} | "
            f"{_fmt(row.flow_forecast_r2)} | {_fmt(row.direct_forecast_r2)} | "
            f"{_fmt(row.twostage_forecast_r2)} | {_fmt(row.oracle_r2)} |"
        )
    lines.extend([
        "",
        "## Out-of-sample maps (all past windows together)",
        "",
        "| Split | H | flow forecast R² | direct price forecast R² | two-stage λ×F̂ R² | oracle |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    multi_view = multi.sort_values(["split", "future_h"])
    for row in multi_view.itertuples(index=False):
        lines.append(
            f"| {row.split} | {row.future_h} | "
            f"{_fmt(row.flow_forecast_r2)} | {_fmt(row.direct_forecast_r2)} | "
            f"{_fmt(row.twostage_forecast_r2)} | {_fmt(row.oracle_r2)} |"
        )
    lines.extend([
        "",
        "## Splits",
        "",
        f"- Discovery: {splits['discovery'][0]} … {splits['discovery'][-1]} ({len(splits['discovery'])} dates)",
        f"- Validation: {splits['validation'][0]} … {splits['validation'][-1]} ({len(splits['validation'])} dates)",
        f"- FINAL: {splits['final'][0]} … {splits['final'][-1]} ({len(splits['final'])} dates)",
        "",
        "`ship_gate` is false. This run does not authorize a live banner, clock, or H5 change.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading features...", flush=True)
    features, meta = load_features(FEATURE_MATRIX)
    features["event_near"] = _near_event(features.index, _event_times(EVENTS_DB))
    print("Past / future windows...", flush=True)
    panel = add_flow_price_windows(features)
    splits = chronological_splits(meta["clean_dates"])
    print("Scoring...", flush=True)
    uni = score_univariate(panel, splits)
    oos = score_oos_maps(panel, splits)
    multi = score_multivariate(panel, splits)
    uni.to_csv(OUTPUT_DIR / "flow_flow_grid.csv", index=False)
    oos.to_csv(OUTPUT_DIR / "price_maps_univariate.csv", index=False)
    multi.to_csv(OUTPUT_DIR / "price_maps_multivariate.csv", index=False)
    val_oos = oos.loc[oos["split"] == "validation"]
    val_multi = multi.loc[multi["split"] == "validation"]
    _write_json(
        OUTPUT_DIR / "summary.json",
        {
            "ship_gate": SHIP_GATE,
            "rows_clean": meta["rows_clean"],
            "dates_clean": meta["dates_clean"],
            "past_w": list(PAST_W),
            "future_h": list(FUTURE_H),
            "splits": {k: v for k, v in splits.items() if k != "all"},
            "validation_univariate": val_oos.to_dict(orient="records"),
            "validation_multivariate": val_multi.to_dict(orient="records"),
        },
    )
    write_report(uni, oos, multi, splits, OUTPUT_DIR / "readout.md")
    print(f"Wrote {OUTPUT_DIR}", flush=True)
    peek = val_multi.loc[val_multi["future_h"] == 1]
    if not peek.empty:
        row = peek.iloc[0]
        print(
            json.dumps(
                {
                    "val_h1_flow_forecast_r2": row["flow_forecast_r2"],
                    "val_h1_direct_forecast_r2": row["direct_forecast_r2"],
                    "val_h1_twostage_forecast_r2": row["twostage_forecast_r2"],
                    "val_h1_oracle_r2": row["oracle_r2"],
                    "ship_gate": SHIP_GATE,
                },
                default=_json_default,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
