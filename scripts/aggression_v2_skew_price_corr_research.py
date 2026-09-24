#!/usr/bin/env python3
"""Research-only TY skew-change versus TY price correlation study.

Guardrail: reads existing joined CSV artifacts only. It does not open/write the
gamma tracker DB, import live dashboard apps, write *_live/, or touch ports.
Outputs are confined to workspace/analysis/aggression_v2_skew_price_corr/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr, ttest_1samp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_gate_lib import benjamini_hochberg  # noqa: E402

V2_JOINED = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_options_context" / "v2_options_minute_joined.csv"
V3_JOINED = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v3_options_context" / "options_context_joined.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_skew_price_corr"
EASTERN = ZoneInfo("America/New_York")
TICK_SIZE = 1.0 / 64.0
METRICS = ("rr25", "rr50", "rr75", "skew_index")
HORIZONS = (1, 5, 15, 30)
LAGS = (-30, -15, -10, -5, -1, 0, 1, 5, 10, 15, 30)
N_BOOT = 10_000
BH_Q = 0.10
SEED = 19
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


def _source_panel(path: Path, source: str) -> pd.DataFrame:
    raw = pd.read_csv(path, low_memory=False)
    if source.startswith("v2"):
        ts_col, price_candidates = "ts", ("v2_price",)
    else:
        ts_col, price_candidates = "Unnamed: 0", ("price", "tbbo_price", "pred_price", "v3_price")
    panel = pd.DataFrame({"ts": pd.to_datetime(raw[ts_col], errors="coerce", utc=True)})
    panel["price"] = np.nan
    for col in price_candidates:
        if col in raw:
            panel["price"] = panel["price"].fillna(pd.to_numeric(raw[col], errors="coerce"))
    for metric in METRICS:
        panel[metric] = pd.to_numeric(raw.get(f"opt_{metric}"), errors="coerce")
    panel = panel.dropna(subset=["ts", "price"]).sort_values("ts").drop_duplicates("ts", keep="last")
    panel["source"] = source
    panel["et_date"] = panel["ts"].dt.tz_convert(EASTERN).dt.strftime("%Y-%m-%d")
    parts = []
    for _, day in panel.groupby("et_date", sort=False):
        d = day.sort_values("ts").copy()
        for horizon in HORIZONS:
            d[f"d_price_{horizon}m_ticks"] = d["price"].diff(horizon) / TICK_SIZE
            for metric in METRICS:
                d[f"d_{metric}_{horizon}m"] = d[metric].diff(horizon)
        parts.append(d)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def build_panel(v2_path: Path, v3_path: Path) -> pd.DataFrame:
    return pd.concat(
        [_source_panel(v2_path, "v2_primary"), _source_panel(v3_path, "v3_crosscheck")],
        ignore_index=True,
    )


def _corr_from_sums(n: np.ndarray, sx: np.ndarray, sy: np.ndarray, sx2: np.ndarray, sy2: np.ndarray, sxy: np.ndarray) -> np.ndarray:
    numerator = n * sxy - sx * sy
    denominator = np.sqrt(np.maximum(n * sx2 - sx * sx, 0.0) * np.maximum(n * sy2 - sy * sy, 0.0))
    return np.divide(numerator, denominator, out=np.full_like(numerator, np.nan, dtype=float), where=denominator > EPS)


def _cluster_boot_corr(
    frame: pd.DataFrame,
    x_col: str,
    y_col: str,
    n_boot: int = N_BOOT,
) -> tuple[float, float, float, float, float, float, float, float, int, int]:
    data = frame[["et_date", x_col, y_col]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(data) < 3:
        return (np.nan,) * 6 + (int(len(data)), int(data["et_date"].nunique()))
    x = data[x_col].to_numpy(float)
    y = data[y_col].to_numpy(float)
    pearson = float(np.corrcoef(x, y)[0, 1]) if np.std(x) > EPS and np.std(y) > EPS else np.nan
    spearman = float(spearmanr(x, y).statistic) if len(np.unique(x)) > 1 and len(np.unique(y)) > 1 else np.nan
    data = data.assign(_rx=rankdata(x), _ry=rankdata(y))
    grouped = []
    daily_pearson, daily_spearman = [], []
    for _, day in data.groupby("et_date", sort=False):
        dx, dy = day[x_col].to_numpy(float), day[y_col].to_numpy(float)
        if len(day) >= 3 and np.std(dx) > EPS and np.std(dy) > EPS:
            daily_pearson.append(float(np.corrcoef(dx, dy)[0, 1]))
            daily_spearman.append(float(spearmanr(dx, dy).statistic))
        grouped.append(
            [
                len(day),
                day[x_col].sum(),
                day[y_col].sum(),
                np.square(day[x_col]).sum(),
                np.square(day[y_col]).sum(),
                (day[x_col] * day[y_col]).sum(),
                day["_rx"].sum(),
                day["_ry"].sum(),
                np.square(day["_rx"]).sum(),
                np.square(day["_ry"]).sum(),
                (day["_rx"] * day["_ry"]).sum(),
            ]
        )
    stats = np.asarray(grouped, dtype=float)
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(stats), size=(n_boot, len(stats)))
    sampled = stats[draws].sum(axis=1)
    boot_p = _corr_from_sums(*[sampled[:, i] for i in range(6)])
    boot_s = _corr_from_sums(
        sampled[:, 0],
        sampled[:, 6],
        sampled[:, 7],
        sampled[:, 8],
        sampled[:, 9],
        sampled[:, 10],
    )
    pearson_ci = np.nanquantile(boot_p, [0.025, 0.975])
    spearman_ci = np.nanquantile(boot_s, [0.025, 0.975])
    pearson_p = float(ttest_1samp(daily_pearson, 0.0).pvalue) if len(daily_pearson) >= 3 else np.nan
    spearman_p = float(ttest_1samp(daily_spearman, 0.0).pvalue) if len(daily_spearman) >= 3 else np.nan
    return (
        pearson,
        float(pearson_ci[0]),
        float(pearson_ci[1]),
        spearman,
        float(spearman_ci[0]),
        float(spearman_ci[1]),
        pearson_p,
        spearman_p,
        int(len(data)),
        int(data["et_date"].nunique()),
    )


def _result_row(frame: pd.DataFrame, x_col: str, y_col: str, **keys: Any) -> dict[str, Any]:
    result = _cluster_boot_corr(frame, x_col, y_col)
    return {
        **keys,
        "x_col": x_col,
        "y_col": y_col,
        "pearson": result[0],
        "pearson_ci_low": result[1],
        "pearson_ci_high": result[2],
        "spearman": result[3],
        "spearman_ci_low": result[4],
        "spearman_ci_high": result[5],
        "pearson_p": result[6],
        "spearman_p": result[7],
        "n": result[8],
        "cluster_n": result[9],
    }


def contemporaneous(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for source, data in panel.groupby("source", sort=False):
        for metric in METRICS:
            for horizon in HORIZONS:
                rows.append(
                    _result_row(
                        data,
                        f"d_{metric}_{horizon}m",
                        f"d_price_{horizon}m_ticks",
                        source=source,
                        metric=metric,
                        horizon=horizon,
                    )
                )
    return pd.DataFrame(rows)


def lead_lag(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for source, source_data in panel.groupby("source", sort=False):
        parts = []
        for _, day in source_data.groupby("et_date", sort=False):
            d = day.sort_values("ts").copy()
            for lag in LAGS:
                if lag > 0:
                    d[f"price_move_lag_{lag}"] = (d["price"].shift(-lag) - d["price"]) / TICK_SIZE
                elif lag < 0:
                    d[f"price_move_lag_{lag}"] = (d["price"] - d["price"].shift(abs(lag))) / TICK_SIZE
                else:
                    d["price_move_lag_0"] = d["price"].diff(5) / TICK_SIZE
            parts.append(d)
        data = pd.concat(parts, ignore_index=True)
        for metric in METRICS:
            for lag in LAGS:
                rows.append(
                    _result_row(
                        data,
                        f"d_{metric}_5m",
                        f"price_move_lag_{lag}",
                        source=source,
                        metric=metric,
                        lag=lag,
                        lag_role="skew_leads_price" if lag > 0 else "price_leads_skew" if lag < 0 else "contemporaneous_5m",
                    )
                )
    return pd.DataFrame(rows)


def apply_fdr(contemp: pd.DataFrame, lagged: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    combined = []
    for table_name, table in (("contemporaneous", contemp), ("lead_lag", lagged)):
        for method in ("pearson", "spearman"):
            table[f"{method}_bh_p"] = np.nan
            table[f"{method}_bh_significant"] = False
            for index, p_value in table[f"{method}_p"].items():
                combined.append((table_name, index, method, p_value))
    bh = benjamini_hochberg([item[3] for item in combined], q=BH_Q)
    for pos, (table_name, index, method, _) in enumerate(combined):
        table = contemp if table_name == "contemporaneous" else lagged
        table.loc[index, f"{method}_bh_p"] = bh["adjusted_p"][pos]
        table.loc[index, f"{method}_bh_significant"] = bool(bh["reject"][pos])
    return contemp, lagged


def write_heatmap(lagged: pd.DataFrame, output_dir: Path) -> str | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    sources = list(lagged["source"].dropna().unique())
    fig, axes = plt.subplots(len(sources), 1, figsize=(11, 3.2 * len(sources)), squeeze=False)
    for ax, source in zip(axes.ravel(), sources):
        pivot = lagged[lagged["source"] == source].pivot(index="metric", columns="lag", values="pearson").reindex(index=METRICS, columns=LAGS)
        image = ax.imshow(pivot.to_numpy(), aspect="auto", cmap="RdBu_r", vmin=-0.25, vmax=0.25)
        ax.set_xticks(range(len(LAGS)), LAGS)
        ax.set_yticks(range(len(METRICS)), METRICS)
        ax.set_xlabel("Lag minutes (positive = skew leads price)")
        ax.set_title(source)
        for i in range(len(METRICS)):
            for j in range(len(LAGS)):
                value = pivot.iloc[i, j]
                ax.text(j, i, f"{value:.2f}" if pd.notna(value) else "", ha="center", va="center", fontsize=7)
    fig.colorbar(image, ax=axes.ravel().tolist(), label="Pearson correlation")
    fig.suptitle("TY skew 5m change vs TY price move lead/lag")
    path = output_dir / "lead_lag_heatmap.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    panel = build_panel(Path(args.v2_joined), Path(args.v3_joined))
    contemp = contemporaneous(panel)
    lagged = lead_lag(panel)
    contemp, lagged = apply_fdr(contemp, lagged)
    contemp_path = output_dir / "contemporaneous_corr.csv"
    lagged_path = output_dir / "lead_lag_corr.csv"
    summary_path = output_dir / "skew_price_corr_summary.json"
    contemp.to_csv(contemp_path, index=False)
    lagged.to_csv(lagged_path, index=False)
    heatmap = write_heatmap(lagged, output_dir)
    significant_leads = lagged[
        (lagged["lag"] > 0)
        & (
            lagged["pearson_bh_significant"]
            | lagged["spearman_bh_significant"]
        )
    ].copy()
    rr50_crosscheck = contemp[
        (contemp["source"] == "v2_primary")
        & (contemp["metric"] == "rr50")
        & (contemp["horizon"] == 30)
    ]
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows_panel": int(len(panel)),
        "source_coverage": {
            source: {
                "rows": int(len(data)),
                "days": int(data["et_date"].nunique()),
                "start": data["ts"].min(),
                "end": data["ts"].max(),
                **{f"{metric}_rows": int(data[metric].notna().sum()) for metric in METRICS},
            }
            for source, data in panel.groupby("source")
        },
        "tests": {"contemporaneous": int(len(contemp)), "lead_lag": int(len(lagged)), "methods": ["pearson", "spearman"], "bh_q": BH_Q},
        "significant_positive_lag_cells": significant_leads.to_dict(orient="records"),
        "rr50_30m_crosscheck": rr50_crosscheck.to_dict(orient="records"),
        "headline": (
            "At least one skew-leading-price correlation survives BH-FDR; correlation alone does not establish tradability."
            if len(significant_leads)
            else "No positive-lag skew-leading-price correlation survives BH-FDR across both sources and methods."
        ),
        "artifacts": {"contemporaneous": str(contemp_path), "lead_lag": str(lagged_path), "heatmap": heatmap, "summary": str(summary_path)},
        "isolation": {"opened_gamma_db": False, "modified_v1_v2_v3": False, "wrote_live_stores": False},
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps({"ok": True, "headline": summary["headline"], "significant_leads": len(significant_leads), "crosscheck": summary["rr50_30m_crosscheck"], "artifacts": summary["artifacts"]}, indent=2, default=_json_default))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="TY skew-change vs TY price correlation study.")
    parser.add_argument("--v2-joined", default=str(V2_JOINED))
    parser.add_argument("--v3-joined", default=str(V3_JOINED))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
