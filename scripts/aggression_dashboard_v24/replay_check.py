"""Parity check: replay the V2.4 paper engine over history.

Confirms the live paper book reproduces the offline harness
(`scripts/aggression_v2_lifo_dislocation_trade_spec_research.py`) on the same
explore dates and the same spec. Read-only; writes nothing.

Run: py -3 -m scripts.aggression_dashboard_v24.replay_check
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_dashboard_v24.lifo_context import annotate_lifo_flags  # noqa: E402
from scripts.aggression_dashboard_v24.trade_spec import PaperBook, SpecConfig  # noqa: E402

PREDICTIONS = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models" / "walkforward_predictions.csv"
FEATURES = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"

# Harness reference values for explore / z=2.0 / h=30 / stop=3 / target=2.
REFERENCE = {
    "DISLOCATION_ONLY": {"n": 951, "gross": 0.091},
    "DISLOCATION_X_LIFO": {"n": 280, "gross": 0.218},
}


def load() -> pd.DataFrame:
    pred = pd.read_csv(PREDICTIONS, parse_dates=["bucket_ts"])
    pred = pred[pred["model"] == "ridge"].copy()
    feat = pd.read_csv(FEATURES, parse_dates=["bucket_ts"], usecols=["bucket_ts", "et_date", "trade_intensity"])
    pred["bucket_ts"] = pd.to_datetime(pred["bucket_ts"], utc=True)
    feat["bucket_ts"] = pd.to_datetime(feat["bucket_ts"], utc=True)
    df = pred.merge(feat, on=["bucket_ts", "et_date"], how="left").sort_values(["et_date", "bucket_ts"])
    grp = df.groupby("et_date", sort=False)["residual"]
    mean = grp.transform(lambda s: s.shift(1).expanding(min_periods=20).mean())
    std = grp.transform(lambda s: s.shift(1).expanding(min_periods=20).std(ddof=1))
    df["residual_z"] = (df["residual"] - mean) / std.replace(0.0, np.nan)
    return df.set_index("bucket_ts")


def main() -> None:
    df = load()
    dates = sorted(df["et_date"].unique().tolist())
    explore = dates[:48]
    sub = df[df["et_date"].isin(explore)]
    print(f"replay rows={len(sub)} dates={len(explore)}", flush=True)

    for name, gate in (("DISLOCATION_ONLY", False), ("DISLOCATION_X_LIFO", True)):
        book = PaperBook(
            config=SpecConfig(
                name=name, z_entry=2.0, lifo_gate=gate,
                stop_ticks=3.0, target_ticks=2.0, horizon_min=30, cost_ticks=0.5,
            )
        )
        for date in explore:
            # The live store annotates LIFO flags before updating; mirror that here.
            book.update(annotate_lifo_flags(sub[sub["et_date"] == date]))
            # Flat at session end, matching the harness's per-day boundary.
            book.open_trade = None
            book.armed = None
            book.last_ts = None
        stats = book.stats()
        ref = REFERENCE[name]
        if not stats["n_trades"]:
            print(f"{name}: NO TRADES (expected ~{ref['n']}) - engine or gate is broken", flush=True)
            continue
        print(
            f"{name}: n={stats['n_trades']} (ref {ref['n']}) "
            f"gross={stats['mean_gross_ticks']:.3f} (ref {ref['gross']:.3f}) "
            f"net={stats['mean_net_ticks']:.3f} hit={stats['hit_rate']:.3f} "
            f"exits={stats['exit_mix']}",
            flush=True,
        )

    print("\nBoth books should land near the harness reference; large gaps mean the "
          "live engine and the research artifact disagree.", flush=True)


if __name__ == "__main__":
    main()
