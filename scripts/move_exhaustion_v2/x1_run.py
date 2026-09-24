"""X1 price-only baseline (Move Exhaustion V2).

Requires X0 PASS artifacts (eligible dates + frozen splits). Reads discovery
and validation store days only. Never opens holdout parquet. Writes under
``workspace/analysis/move_exhaustion_v2/experiments/X1_<run_id>/``.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.move_exhaustion_v2.artifacts import (  # noqa: E402
    ANALYSIS_ROOT,
    STORE_ROOT,
    atomic_write_json,
    ensure_under,
    utc_run_id,
)
from scripts.move_exhaustion_v2.episodes.builder import (  # noqa: E402
    CONSUMED_COLUMNS,
    build_episodes,
)
from scripts.move_exhaustion_v2.labels.barriers import (  # noqa: E402
    CENSORED_INCOMPLETE_HORIZON,
    CONTINUATION,
    OSCILLATORY_UNRESOLVED,
    REVERSAL,
    UNRESOLVED_AMBIGUOUS,
)
from scripts.move_exhaustion_v2.labels.path_metrics import (  # noqa: E402
    compute_path_metrics,
)

PKG_ROOT = Path(__file__).resolve().parent

PRIMARY_B = 8
PRIMARY_X = 2
PRIMARY_R = 4
PRIMARY_C = 4
PRIMARY_H = 15.0
B_GRID = (4, 6, 8, 12)
X_GRID = (1, 2, 3, 4)
TICK_SIZE = 0.015625
MIN_RESOLVED_DISCOVERY = 100
MAX_UNRESOLVED_SHARE = 0.80
MAX_ONE_CLASS_SHARE = 0.90
METRIC_COLUMNS = (
    "trading_date",
    "outcome",
    "forward_return_bps",
    "mae_ticks",
    "mfe_ticks",
)


class X1RunError(RuntimeError):
    """Raised when X1 cannot proceed (fail closed)."""


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise X1RunError(f"required X0 artifact missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _require_x0_pass(analysis_root: Path) -> Dict[str, Any]:
    experiments = analysis_root / "experiments"
    if not experiments.is_dir():
        raise X1RunError("X0 experiment artifacts missing; finalize X0 first")
    conclusions = sorted(experiments.glob("X0_*/conclusion.json"))
    if not conclusions:
        raise X1RunError("X0 conclusion.json missing; finalize X0 first")
    payload = json.loads(conclusions[-1].read_text(encoding="utf-8"))
    if payload.get("verdict") != "PASS":
        raise X1RunError(
            f"X1 requires X0 PASS; found verdict={payload.get('verdict')!r} "
            f"in {conclusions[-1]}"
        )
    if payload.get("holdout_opened") is not False:
        raise X1RunError("X0 conclusion declares holdout access")
    return payload


def research_dates(split_manifest: Mapping[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    splits = split_manifest.get("splits") or {}
    discovery = list(splits.get("discovery", {}).get("dates") or [])
    validation = list(splits.get("validation", {}).get("dates") or [])
    holdout = list(splits.get("final_holdout", {}).get("dates") or [])
    if set(discovery) & set(holdout) or set(validation) & set(holdout):
        raise X1RunError("split_manifest mixes holdout dates into research splits")
    return discovery, validation, holdout


def _base_rates(frame: pd.DataFrame) -> Dict[str, Any]:
    if frame.empty:
        return {
            "n_episodes": 0,
            "n_excluded_ambiguous": 0,
            "n_censored": 0,
            "n_scored": 0,
            "n_resolved": 0,
            "p_reversal": None,
            "p_continuation": None,
            "p_unresolved": None,
            "p_reversal_among_resolved": None,
        }
    excluded = frame["outcome"] == UNRESOLVED_AMBIGUOUS
    censored = frame["outcome"] == CENSORED_INCOMPLETE_HORIZON
    scored = frame.loc[~excluded & ~censored]
    n_scored = int(len(scored))
    n_rev = int((scored["outcome"] == REVERSAL).sum())
    n_cont = int((scored["outcome"] == CONTINUATION).sum())
    n_unr = int((scored["outcome"] == OSCILLATORY_UNRESOLVED).sum())
    n_resolved = n_rev + n_cont
    return {
        "n_episodes": int(len(frame)),
        "n_excluded_ambiguous": int(excluded.sum()),
        "n_censored": int(censored.sum()),
        "n_scored": n_scored,
        "n_resolved": n_resolved,
        "p_reversal": (n_rev / n_scored) if n_scored else None,
        "p_continuation": (n_cont / n_scored) if n_scored else None,
        "p_unresolved": (n_unr / n_scored) if n_scored else None,
        "p_reversal_among_resolved": (n_rev / n_resolved) if n_resolved else None,
    }


def _quantiles(series: pd.Series) -> Dict[str, Optional[float]]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {"q50": None, "q90": None, "q95": None}
    return {
        "q50": float(values.quantile(0.50)),
        "q90": float(values.quantile(0.90)),
        "q95": float(values.quantile(0.95)),
    }


def _guardrails(discovery_rates: Mapping[str, Any]) -> Dict[str, Any]:
    n_resolved = int(discovery_rates.get("n_resolved") or 0)
    n_scored = int(discovery_rates.get("n_scored") or 0)
    unresolved = float(discovery_rates.get("p_unresolved") or 0.0)
    shares = [
        float(discovery_rates.get("p_reversal") or 0.0),
        float(discovery_rates.get("p_continuation") or 0.0),
        unresolved,
    ]
    return {
        "min_resolved_ok": n_resolved >= MIN_RESOLVED_DISCOVERY,
        "unresolved_share_ok": n_scored == 0 or unresolved <= MAX_UNRESOLVED_SHARE,
        "one_class_share_ok": n_scored == 0 or max(shares) <= MAX_ONE_CLASS_SHARE,
        "n_resolved_discovery": n_resolved,
        "unresolved_share": unresolved if n_scored else None,
        "max_class_share": max(shares) if n_scored else None,
    }


def _guardrails_pass(guard: Mapping[str, Any]) -> bool:
    return bool(
        guard.get("min_resolved_ok")
        and guard.get("unresolved_share_ok")
        and guard.get("one_class_share_ok")
        and int(guard.get("n_resolved_discovery") or 0) >= MIN_RESOLVED_DISCOVERY
    )


def _discovery_status(out_dir: Path, discovery_set: set) -> Dict[str, Any]:
    labeled = _concat_shards(
        _shard_dir(out_dir, PRIMARY_B, PRIMARY_X), columns=METRIC_COLUMNS
    )
    if labeled.empty:
        rates = _base_rates(labeled)
        guard = _guardrails(rates)
        return {
            "rates": rates,
            "guard": guard,
            "sufficient": False,
            "n_dates": 0,
        }
    disc = labeled[labeled["trading_date"].astype(str).isin(discovery_set)]
    rates = _base_rates(disc)
    guard = _guardrails(rates)
    n_dates = int(disc["trading_date"].astype(str).nunique()) if not disc.empty else 0
    return {
        "rates": rates,
        "guard": guard,
        "sufficient": _guardrails_pass(guard),
        "n_dates": n_dates,
    }


def _label_day(
    frame: pd.DataFrame,
    *,
    B_ticks: float,
    x_ticks: float,
    R_ticks: float,
    C_ticks: float,
    H_minutes: float,
) -> pd.DataFrame:
    episodes = build_episodes(
        frame,
        B_ticks=B_ticks,
        x_ticks=x_ticks,
        max_horizon_minutes=H_minutes,
        tick_size=TICK_SIZE,
    )
    if episodes.empty:
        return episodes
    return compute_path_metrics(
        episodes,
        frame,
        R_ticks=R_ticks,
        C_ticks=C_ticks,
        H_minutes=H_minutes,
        tick_size=TICK_SIZE,
    )


def _shard_dir(out_dir: Path, B_ticks: float, x_ticks: float) -> Path:
    return out_dir / "days" / f"B{int(B_ticks)}_x{int(x_ticks)}"


def _write_parquet_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)


def _concat_shards(
    shard_dir: Path, columns: Optional[Sequence[str]] = None
) -> pd.DataFrame:
    files = sorted(p for p in shard_dir.glob("*.parquet") if ".tmp-" not in p.name)
    if not files:
        return pd.DataFrame()
    frames = [
        pd.read_parquet(path, columns=list(columns) if columns else None)
        for path in files
    ]
    return pd.concat(frames, ignore_index=True)


def _write_combined_labeled(shard_dir: Path, dest: Path) -> None:
    labeled = _concat_shards(shard_dir)
    if labeled.empty:
        return
    _write_parquet_atomic(dest, labeled)


def run_x1(
    *,
    analysis_root: Path,
    store_root: Path,
    run_id: Optional[str] = None,
    primary_only: bool = False,
    stop_when_sufficient: bool = False,
) -> Dict[str, Any]:
    analysis = Path(analysis_root).resolve()
    store = Path(store_root).resolve()
    x0 = _require_x0_pass(analysis)
    splits = _load_json(analysis / "split_manifest.json")
    if splits.get("holdout_opened") is not False:
        raise X1RunError("split_manifest declares holdout access")
    discovery, validation, holdout = research_dates(splits)
    holdout_set = set(holdout)
    discovery_set = set(discovery)
    if stop_when_sufficient:
        dates = [d for d in discovery if d not in holdout_set]
    else:
        dates = [d for d in discovery + validation if d not in holdout_set]
    if not dates:
        raise X1RunError("no discovery/validation dates after holdout exclusion")

    run_id = run_id or utc_run_id("X1")[3:]
    out_dir = ensure_under(
        (analysis / "experiments" / f"X1_{run_id}").resolve(), analysis
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = out_dir / "checkpoint.json"
    checkpoint = {"completed_dates": [], "holdout_opened": False}
    if checkpoint_path.is_file():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    done = set(checkpoint.get("completed_dates") or [])

    cells: List[Tuple[float, float]] = [(PRIMARY_B, PRIMARY_X)]
    if not primary_only:
        cells = [(float(b), float(x)) for b in B_GRID for x in X_GRID]

    already = _discovery_status(out_dir, discovery_set) if stop_when_sufficient else None
    if already and already["sufficient"]:
        dates = []

    for date_key in dates:
        if date_key in holdout_set:
            raise X1RunError(f"refusing to read holdout date {date_key}")
        parquet = store / f"{date_key}.parquet"
        if date_key in done:
            continue
        if not parquet.is_file():
            continue
        table = pq.read_table(parquet, columns=list(CONSUMED_COLUMNS))
        frame = table.to_pandas()
        for B_ticks, x_ticks in cells:
            labeled = _label_day(
                frame,
                B_ticks=B_ticks,
                x_ticks=x_ticks,
                R_ticks=PRIMARY_R,
                C_ticks=PRIMARY_C,
                H_minutes=PRIMARY_H,
            )
            if labeled.empty:
                continue
            labeled = labeled.copy()
            labeled["B_ticks"] = B_ticks
            labeled["x_ticks"] = x_ticks
            labeled["trading_date"] = (
                pd.to_datetime(labeled["trading_date"], errors="raise")
                .dt.strftime("%Y-%m-%d")
            )
            _write_parquet_atomic(
                _shard_dir(out_dir, B_ticks, x_ticks) / f"{date_key}.parquet",
                labeled,
            )
            del labeled
        del table, frame
        gc.collect()
        pa.default_memory_pool().release_unused()
        done.add(date_key)
        checkpoint = {
            "completed_dates": sorted(done),
            "holdout_opened": False,
            "n_completed": len(done),
        }
        atomic_write_json(checkpoint_path, checkpoint)
        if stop_when_sufficient and _discovery_status(out_dir, discovery_set)["sufficient"]:
            break

    dates_scored = sorted(done)
    cell_metrics: List[Dict[str, Any]] = []
    primary_discovery_rate = None
    for B_ticks, x_ticks in cells:
        shard_dir = _shard_dir(out_dir, B_ticks, x_ticks)
        cell_path = out_dir / f"labeled_B{int(B_ticks)}_x{int(x_ticks)}.parquet"
        _write_combined_labeled(shard_dir, cell_path)
        labeled = _concat_shards(shard_dir, columns=METRIC_COLUMNS)
        disc = labeled[labeled["trading_date"].astype(str).isin(discovery_set)] if not labeled.empty else labeled
        val = labeled[~labeled["trading_date"].astype(str).isin(discovery_set)] if not labeled.empty else labeled
        disc_rates = _base_rates(disc)
        val_rates = _base_rates(val)
        guard = _guardrails(disc_rates)
        path_stats = {}
        if not disc.empty:
            path_stats = {
                "forward_return_bps": _quantiles(disc.get("forward_return_bps", pd.Series(dtype=float))),
                "mae_ticks": _quantiles(disc.get("mae_ticks", pd.Series(dtype=float))),
                "mfe_ticks": _quantiles(disc.get("mfe_ticks", pd.Series(dtype=float))),
            }
        row = {
            "B_ticks": B_ticks,
            "x_ticks": x_ticks,
            "R_ticks": PRIMARY_R,
            "C_ticks": PRIMARY_C,
            "H_minutes": PRIMARY_H,
            "discovery": disc_rates,
            "validation": val_rates,
            "path_metrics_discovery": path_stats,
            "guardrails": guard,
            "primary": B_ticks == PRIMARY_B and x_ticks == PRIMARY_X,
        }
        cell_metrics.append(row)
        if row["primary"]:
            primary_discovery_rate = disc_rates.get("p_reversal")

    metrics = {
        "schema_version": 1,
        "experiment": "X1",
        "run_id": run_id,
        "holdout_opened": False,
        "n_dates_used": len(dates_scored),
        "n_discovery_dates": len(discovery),
        "n_validation_dates": 0 if stop_when_sufficient else len(validation),
        "n_holdout_dates_excluded": len(holdout),
        "stop_when_sufficient": stop_when_sufficient,
        "sample": (
            "cell_guardrail_prefix" if stop_when_sufficient else "discovery_and_validation"
        ),
        "date_min": dates_scored[0] if dates_scored else None,
        "date_max": dates_scored[-1] if dates_scored else None,
        "primary_discovery_reversal_rate": primary_discovery_rate,
        "cells": cell_metrics,
        "x0_conclusion": str(x0.get("verdict")),
    }
    conclusion = {
        "schema_version": 1,
        "experiment": "X1",
        "run_id": run_id,
        "verdict": None,
        "holdout_opened": False,
        "key_number": {
            "name": "discovery_period_base_reversal_rate_primary_cell",
            "B_ticks": PRIMARY_B,
            "x_ticks": PRIMARY_X,
            "R_ticks": PRIMARY_R,
            "C_ticks": PRIMARY_C,
            "H_minutes": PRIMARY_H,
            "value": primary_discovery_rate,
        },
        "summary": (
            "X1 price-only baseline (descriptive, no FDR verdict). "
            "Discovery-period primary-cell P(REVERSAL)="
            f"{primary_discovery_rate}."
        ),
        "limitations": [
            "vol regime / event-day conditioning deferred if those columns "
            "are absent from the price-only episode surface",
            "robustness grid is B x x at the primary (R,C,H) label only",
            *(
                [
                    "sample is the shortest discovery prefix that passes cell "
                    "guardrails (>=100 resolved, unresolved<=80%, one-class<=90%); "
                    "not the full 2022-2024 discovery census; validation dates not scored"
                ]
                if stop_when_sufficient
                else []
            ),
        ],
    }
    manifest = {
        "schema_version": 1,
        "experiment": "X1",
        "run_id": run_id,
        "holdout_opened": False,
        "dates_used_source": str(analysis / "split_manifest.json"),
        "store_root": str(store),
        "primary_only": primary_only,
        "stop_when_sufficient": stop_when_sufficient,
        "dates_scored": dates_scored,
        "cells": [{"B_ticks": b, "x_ticks": x} for b, x in cells],
    }
    atomic_write_json(out_dir / "metrics.json", metrics)
    atomic_write_json(out_dir / "conclusion.json", conclusion)
    atomic_write_json(out_dir / "manifest.json", manifest)
    return {
        "run_id": run_id,
        "out_dir": str(out_dir),
        "primary_discovery_reversal_rate": primary_discovery_rate,
        "holdout_opened": False,
        "n_dates_used": len(dates_scored),
        "stop_when_sufficient": stop_when_sufficient,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="X1 price-only baseline")
    parser.add_argument("--analysis-root", default=str(ANALYSIS_ROOT))
    parser.add_argument("--store-root", default=str(STORE_ROOT))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--primary-only", action="store_true")
    parser.add_argument(
        "--stop-when-sufficient",
        action="store_true",
        help="Stop after discovery dates pass primary-cell guardrails; skip validation",
    )
    args = parser.parse_args(argv)
    try:
        result = run_x1(
            analysis_root=Path(args.analysis_root),
            store_root=Path(args.store_root),
            run_id=args.run_id,
            primary_only=args.primary_only,
            stop_when_sufficient=args.stop_when_sufficient,
        )
    except X1RunError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 2
    print(
        f"x1_run={result['run_id']} "
        f"reversal_rate={result['primary_discovery_reversal_rate']} "
        f"dates={result['n_dates_used']} "
        f"holdout_opened={result['holdout_opened']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
