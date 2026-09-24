#!/usr/bin/env python3
"""Re-gate existing V1 dispersion validation cells under relaxed thresholds.

Research-only. Reads prior artifacts; does not touch live apps/stores.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ART = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v1_flow_price_dispersion"
OUT = ART / "gate_threshold_sweep"

CONFIGS = [
    {
        "name": "baseline_strict",
        "min_n": 50,
        "min_dates": 8,
        "min_lift_pp": 8.0,
        "require_ci": True,
        "require_bh": True,
        "require_dates": True,
    },
    {
        "name": "relax_lift_5pp",
        "min_n": 50,
        "min_dates": 8,
        "min_lift_pp": 5.0,
        "require_ci": True,
        "require_bh": True,
        "require_dates": True,
    },
    {
        "name": "relax_lift_3pp",
        "min_n": 50,
        "min_dates": 8,
        "min_lift_pp": 3.0,
        "require_ci": True,
        "require_bh": True,
        "require_dates": True,
    },
    {
        "name": "relax_n30_dates5",
        "min_n": 30,
        "min_dates": 5,
        "min_lift_pp": 5.0,
        "require_ci": True,
        "require_bh": True,
        "require_dates": True,
    },
    {
        "name": "no_fdr_lift5",
        "min_n": 50,
        "min_dates": 8,
        "min_lift_pp": 5.0,
        "require_ci": True,
        "require_bh": False,
        "require_dates": True,
    },
    {
        "name": "no_fdr_lift3",
        "min_n": 40,
        "min_dates": 6,
        "min_lift_pp": 3.0,
        "require_ci": True,
        "require_bh": False,
        "require_dates": True,
    },
    {
        "name": "ci_only_lift5",
        "min_n": 40,
        "min_dates": 5,
        "min_lift_pp": 5.0,
        "require_ci": True,
        "require_bh": False,
        "require_dates": False,
    },
    {
        "name": "very_loose",
        "min_n": 30,
        "min_dates": 4,
        "min_lift_pp": 2.0,
        "require_ci": False,
        "require_bh": False,
        "require_dates": True,
    },
]


def gate_row(row: pd.Series, cfg: dict) -> str:
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


def main() -> None:
    lb = pd.read_csv(ART / "validation_leaderboard.csv")
    OUT.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    gated_frames = []
    for cfg in CONFIGS:
        g = lb.copy()
        g["gate_config"] = cfg["name"]
        g["gate"] = g.apply(lambda r, c=cfg: gate_row(r, c), axis=1)
        counts = g["gate"].value_counts().to_dict()
        summary_rows.append(
            {
                **cfg,
                "n_PASS": int(counts.get("PASS", 0)),
                "n_PROVISIONAL": int(counts.get("PROVISIONAL", 0)),
                "n_FAIL": int(counts.get("FAIL", 0)),
            }
        )
        g.sort_values("lift_pp", ascending=False).to_csv(OUT / f"validation_{cfg['name']}.csv", index=False)
        gated_frames.append(g)

    all_df = pd.concat(gated_frames, ignore_index=True)
    all_df.to_csv(OUT / "validation_all_configs.csv", index=False)
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(OUT / "gate_config_summary.csv", index=False)

    cols = [
        "cell_id",
        "family",
        "n",
        "n_dates",
        "rate",
        "baseline_rate",
        "lift_pp",
        "one_sided_lower",
        "p_one_sided",
        "bh_adjusted_p",
        "bh_significant",
        "gate",
    ]
    raw_cols = [
        "cell_id",
        "family",
        "n",
        "n_dates",
        "rate",
        "baseline_rate",
        "lift_pp",
        "ci_low",
        "ci_high",
        "one_sided_lower",
        "p_one_sided",
        "bh_adjusted_p",
        "bh_significant",
    ]

    print("=== GATE CONFIG SUMMARY ===")
    print(
        summary[
            [
                "name",
                "min_n",
                "min_dates",
                "min_lift_pp",
                "require_ci",
                "require_bh",
                "require_dates",
                "n_PASS",
                "n_PROVISIONAL",
                "n_FAIL",
            ]
        ].to_string(index=False)
    )

    for cfg in CONFIGS:
        g = all_df[all_df["gate_config"] == cfg["name"]].sort_values("lift_pp", ascending=False)
        print()
        print(
            f"=== {cfg['name']} "
            f"(n>={cfg['min_n']}, dates>={cfg['min_dates'] if cfg['require_dates'] else 'any'}, "
            f"lift>={cfg['min_lift_pp']}pp, ci={cfg['require_ci']}, bh={cfg['require_bh']}) ==="
        )
        print("counts:", g["gate"].value_counts().to_dict())
        show = g[g["gate"].isin(["PASS", "PROVISIONAL"])][cols]
        if show.empty:
            print("No PASS/PROVISIONAL. Top 10 by lift:")
            print(g.head(10)[cols].to_string(index=False))
        else:
            print(show.to_string(index=False))

    print()
    print("=== RAW VALIDATION CELLS BY FAMILY (sorted by lift) ===")
    for family, part in lb.sort_values(["family", "lift_pp"], ascending=[True, False]).groupby("family", sort=True):
        print()
        print(f"--- {family} ---")
        print(part[raw_cols].to_string(index=False))


if __name__ == "__main__":
    main()
