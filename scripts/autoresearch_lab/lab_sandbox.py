"""Discovery-only exploratory candidate scoring pipeline."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from scripts.autoresearch.fixed_env.candidate_runner import run_candidate
from scripts.autoresearch.fixed_env.evaluator import (
    fit_logit,
    transform_logit,
)
from scripts.autoresearch.fixed_env.execution import evaluate_predictive_cells
from scripts.autoresearch.fixed_env.preregister import digest_file, digest_object
from scripts.autoresearch.fixed_env.statistics import (
    circular_block_shift,
    equal_date_effect,
)

from .lab_matrix import BASELINE_RAW, discovery_matrix
from .lab_outcomes import OUTCOME_NAMES, TICK_SIZE, evaluate_outcome, slice_future
from .lab_readers import PROJECT_ROOT, REGISTRY_ROOT, load_registry

LAB_ROOT = PROJECT_ROOT / "workspace" / "analysis" / "autoresearch_lab"
CANDIDATE_ROOT = LAB_ROOT / "candidates"
FROZEN_CANDIDATE_ROOT = PROJECT_ROOT / "scripts" / "autoresearch" / "candidates"
LABEL = "EXPLORATORY / DISCOVERY-ONLY / NOT EVIDENCE"
REPETITIONS = 999
SHIFT_REPETITIONS = 999

DEFAULT_TREATMENT = {
    "E001": "flip_count",
    "E002": "price_led_score",
    "D001": "trade_intensity",
}


def _singleton_grid(config: Mapping[str, Any]) -> dict[str, list[Any]]:
    if not config:
        return {"lab_run": [1]}
    return {name: [value] for name, value in config.items()}


def _write_custom_source(source: str) -> Path:
    if not source.strip():
        raise ValueError("custom candidate source is empty")
    digest = digest_object({"source": source})
    CANDIDATE_ROOT.mkdir(parents=True, exist_ok=True)
    destination = CANDIDATE_ROOT / f"{digest}.py"
    if destination.exists() and destination.read_text(encoding="utf-8") != source:
        raise RuntimeError("candidate digest collision")
    if not destination.exists():
        destination.write_text(source, encoding="utf-8", newline="\n")
    return destination


def _candidate_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidate_id = str(payload.get("candidate") or "E001").upper()
    config = dict(payload.get("config") or {})
    if candidate_id == "CUSTOM":
        features = tuple(str(item) for item in payload.get("feature_names") or ())
        allowed = tuple(str(item) for item in payload.get("allowed_inputs") or ())
        if not features or not allowed:
            raise ValueError("custom candidate needs feature_names and allowed_inputs")
        if "formation_ts" not in allowed:
            allowed = ("formation_ts", *allowed)
        source = _write_custom_source(str(payload.get("source") or ""))
        feature_map = {name: "Exploratory user-defined feature" for name in features}
        roots = [CANDIDATE_ROOT]
    elif candidate_id in {"E001", "E002", "D001"}:
        registry = load_registry()
        spec = registry["experiments"][candidate_id]
        allowed = tuple(spec["candidate"]["allowed_inputs"])
        feature_map = dict(spec["candidate"]["registered_feature_map"])
        source = FROZEN_CANDIDATE_ROOT / f"{candidate_id.lower()}.py"
        roots = [FROZEN_CANDIDATE_ROOT]
    else:
        raise ValueError("candidate must be E001, E002, D001, or CUSTOM")
    if not config:
        config = {"lab_run": 1} if candidate_id == "CUSTOM" else _default_config(candidate_id)
    return {
        "candidate_id": candidate_id,
        "source": source,
        "source_roots": roots,
        "allowed_inputs": allowed,
        "feature_map": feature_map,
        "config": config,
        "grid": _singleton_grid(config),
    }


def _default_config(candidate_id: str) -> dict[str, Any]:
    if candidate_id == "E001":
        return {"window_minutes": 60, "deadband_ticks": 1.0, "dwell_minutes": 2}
    if candidate_id == "E002":
        return {
            "ridge_lambda": 10.0,
            "formation_lookback_minutes": 30,
            "entry_gap_ticks": 2,
        }
    return {"map": "DECLARED_243"}


def sandbox_metadata() -> dict[str, Any]:
    matrix = discovery_matrix()
    return {
        "success": True,
        "label": LABEL,
        "matrix": matrix.status(),
        "candidates": {
            candidate: {
                "default_config": _default_config(candidate),
                "treatment_feature": DEFAULT_TREATMENT[candidate],
            }
            for candidate in ("E001", "E002", "D001")
        },
        "outcomes": list(OUTCOME_NAMES),
        "allowed_inputs": list(matrix.allowed_inputs),
        "repetitions": REPETITIONS,
    }


def _candidate_maps(rows: Sequence[Any]) -> dict[pd.Timestamp, dict[str, float]]:
    return {
        pd.Timestamp(row.formation_ts): {
            name: float(value) for name, value in row.features.items()
        }
        for row in rows
    }


def _group_arrays(frame: pd.DataFrame) -> dict[tuple[str, str], tuple[np.ndarray, np.ndarray]]:
    grouped: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
    for key, group in frame.groupby(
        [frame["et_date"].astype(str), frame["session"].astype(str)], sort=False
    ):
        grouped[(str(key[0]), str(key[1]))] = (
            group.index.asi8,
            group["price"].to_numpy(dtype=float),
        )
    return grouped


def _anchor_rows(
    frame: pd.DataFrame,
    candidate_maps: Mapping[pd.Timestamp, Mapping[str, float]],
    outcome: Mapping[str, Any],
    *,
    warmup_minutes: int,
) -> list[dict[str, Any]]:
    horizon = int(outcome.get("horizon_minutes") or 30)
    if horizon <= 0:
        raise ValueError("horizon_minutes must be positive")
    name = str(outcome.get("name") or "bounded_excursion")
    params = dict(outcome.get("params") or {})
    groups = _group_arrays(frame)
    selected_at: dict[tuple[str, str], int] = {}
    records: list[dict[str, Any]] = []
    horizon_ns = horizon * 60 * 1_000_000_000
    for timestamp, features in sorted(candidate_maps.items()):
        if timestamp not in frame.index:
            continue
        raw = frame.loc[timestamp]
        if float(raw["minutes_since_open"]) < warmup_minutes:
            continue
        key = (str(raw["et_date"]), str(raw["session"]))
        timestamp_ns = int(timestamp.value)
        if timestamp_ns - selected_at.get(key, -10**30) < horizon_ns:
            continue
        group_ts, group_prices = groups[key]
        future_ts, future_prices = slice_future(
            group_ts,
            group_prices,
            timestamp_ns,
            horizon_minutes=horizon,
        )
        required = int(np.ceil(0.8 * horizon))
        if len(future_ts) < required:
            continue
        if int(future_ts[-1]) < timestamp_ns + horizon_ns - 2 * 60 * 1_000_000_000:
            continue
        gap = features.get("formation_gap")
        target = evaluate_outcome(
            name,
            future_ts,
            future_prices,
            float(raw["price"]),
            formation_gap=gap,
            params=params,
        )
        selected_at[key] = timestamp_ns
        records.append(_anchor_record(raw, timestamp, features, target))
    return records


def _anchor_record(
    raw: pd.Series,
    timestamp: pd.Timestamp,
    features: Mapping[str, float],
    target: int,
) -> dict[str, Any]:
    model_features = {
        name: float(raw[name]) if pd.notna(raw[name]) else float("nan")
        for name in BASELINE_RAW
    }
    model_features.update({name: float(value) for name, value in features.items()})
    return {
        "formation_ts": timestamp.isoformat().replace("+00:00", "Z"),
        "timestamp_ns": int(timestamp.value),
        "date": str(raw["et_date"]),
        "session": str(raw["session"]),
        "event_blackout": bool(raw["event_blackout"]),
        "roll_flag": bool(raw["roll_flag"]),
        "realized_vol": (
            float(raw["realized_vol"]) if pd.notna(raw["realized_vol"]) else None
        ),
        "absolute_gap_ticks": (
            abs(float(features["formation_gap"])) / TICK_SIZE
            if "formation_gap" in features
            else None
        ),
        "target": int(target),
        "features": model_features,
    }


def _date_partition() -> tuple[tuple[str, ...], tuple[str, ...]]:
    dates = discovery_matrix().assigned_dates()
    cutoff = max(1, min(len(dates) - 1, int(np.floor(0.70 * len(dates)))))
    return dates[:cutoff], dates[cutoff:]


def _run_candidate_pair(
    contract: Mapping[str, Any],
    train_rows: Sequence[Mapping[str, Any]],
    score_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[pd.Timestamp, dict[str, float]], dict[pd.Timestamp, dict[str, float]], Any, str]:
    binding = digest_object(
        {
            "lab": 1,
            "candidate": contract["candidate_id"],
            "features": sorted(contract["feature_map"]),
        }
    )
    common = {
        "source": contract["source"],
        "source_roots": contract["source_roots"],
        "allowed_input_columns": set(contract["allowed_inputs"]),
        "registered_feature_map": contract["feature_map"],
        "config": contract["config"],
        "contract_hash": binding,
        "declared_grid": contract["grid"],
        "timeout_seconds": 900.0,
    }
    train, state, state_hash = run_candidate(
        rows=train_rows, split="discovery", operation="fit_transform", **common
    )
    score, _, _ = run_candidate(
        rows=score_rows,
        split="discovery",
        operation="transform",
        frozen_state=state,
        **common,
    )
    return _candidate_maps(train), _candidate_maps(score), state, state_hash


def _slice_mask(
    records: Sequence[Mapping[str, Any]],
    selectors: Mapping[str, Any],
    volatility_cuts: tuple[float, float] | None,
) -> np.ndarray:
    mask = np.ones(len(records), dtype=bool)
    session = selectors.get("session")
    if session and session != "ALL":
        allowed = {session} if isinstance(session, str) else set(session)
        mask &= np.asarray([row["session"] in allowed for row in records])
    event = str(selectors.get("event_blackout") or "any").lower()
    if event == "exclude":
        mask &= np.asarray([not row["event_blackout"] for row in records])
    elif event == "only":
        mask &= np.asarray([row["event_blackout"] for row in records])
    roll = str(selectors.get("roll") or "any").lower()
    if roll in {"only", "exclude"}:
        wanted = roll == "only"
        mask &= np.asarray([row["roll_flag"] is wanted for row in records])
    gap = selectors.get("minimum_gap_ticks")
    if gap is not None:
        mask &= np.asarray(
            [
                row["absolute_gap_ticks"] is not None
                and row["absolute_gap_ticks"] >= float(gap)
                for row in records
            ]
        )
    bucket = str(selectors.get("volatility_bucket") or "ALL").upper()
    if bucket != "ALL" and volatility_cuts is not None:
        low, high = volatility_cuts
        values = np.asarray(
            [row["realized_vol"] if row["realized_vol"] is not None else np.nan for row in records]
        )
        if bucket == "LOW":
            mask &= values <= low
        elif bucket == "MID":
            mask &= (values > low) & (values < high)
        elif bucket == "HIGH":
            mask &= values >= high
        else:
            raise ValueError("volatility_bucket must be ALL/LOW/MID/HIGH")
    return mask


def _volatility_cuts(records: Sequence[Mapping[str, Any]]) -> tuple[float, float] | None:
    values = np.asarray(
        [row["realized_vol"] if row["realized_vol"] is not None else np.nan for row in records],
        dtype=float,
    )
    values = values[np.isfinite(values)]
    if not len(values):
        return None
    low, high = np.quantile(values, [1 / 3, 2 / 3])
    return float(low), float(high)


def _shift_p_values(
    records: Sequence[Mapping[str, Any]],
    feature_rows: list[dict[str, float]],
    treatment: str,
    model: Any,
    baseline_probability: np.ndarray,
    target: np.ndarray,
    cell_masks: Mapping[str, np.ndarray],
    *,
    enabled: bool,
    seed_key: object,
) -> dict[str, float]:
    if not enabled:
        return {name: 1.0 for name in cell_masks}
    groups = np.asarray([f"{row['date']}|{row['session']}" for row in records])
    timestamps = np.asarray([row["timestamp_ns"] for row in records], dtype=np.int64)
    eligible = np.zeros(len(records), dtype=bool)
    for group in np.unique(groups):
        indices = np.flatnonzero(groups == group)
        if len(indices) >= 2 and timestamps[indices].max() - timestamps[indices].min() >= 30 * 60 * 1e9:
            eligible[indices] = True
    if len(np.unique(groups[eligible])) < 2:
        return {name: 1.0 for name in cell_masks}
    values = np.asarray([row[treatment] for row in feature_rows], dtype=float)
    observed = {}
    valid: dict[str, bool] = {}
    augmented_probability = transform_logit(feature_rows, model)
    score_dates = np.asarray([row["date"] for row in records])
    for name, raw_mask in cell_masks.items():
        mask = np.asarray(raw_mask, dtype=bool) & eligible
        valid[name] = np.count_nonzero(mask) > 0 and len(np.unique(score_dates[mask])) >= 2
        if not valid[name]:
            observed[name] = float("inf")
            continue
        observed[name] = equal_date_effect(
            score_dates[mask],
            (baseline_probability[mask] - target[mask]) ** 2,
            (augmented_probability[mask] - target[mask]) ** 2,
        )
    exceed = {name: 0 for name in cell_masks}
    for repetition in range(SHIFT_REPETITIONS):
        shifted = values.copy()
        shifted[eligible] = circular_block_shift(
            values[eligible],
            groups[eligible],
            timestamps[eligible],
            seed_key=(seed_key, repetition),
        )
        altered = [dict(row) for row in feature_rows]
        for index, value in enumerate(shifted):
            altered[index][treatment] = float(value)
        probability = transform_logit(altered, model)
        for name, raw_mask in cell_masks.items():
            if not valid[name]:
                continue
            mask = np.asarray(raw_mask, dtype=bool) & eligible
            effect = equal_date_effect(
                score_dates[mask],
                (baseline_probability[mask] - target[mask]) ** 2,
                (probability[mask] - target[mask]) ** 2,
            )
            exceed[name] += int(effect >= observed[name])
    return {
        name: (
            float((1 + exceed[name]) / (SHIFT_REPETITIONS + 1))
            if valid[name]
            else 1.0
        )
        for name in cell_masks
    }


def _model_rows(
    records: Sequence[Mapping[str, Any]]
) -> list[dict[str, float]]:
    return [dict(record["features"]) for record in records]


def _default_outcome(candidate_id: str) -> dict[str, Any]:
    if candidate_id == "E002":
        return {
            "name": "convergence_first_passage",
            "horizon_minutes": 30,
            "params": {"convergence_fraction": 0.5, "adverse_fraction": 1.0},
        }
    return {
        "name": "bounded_excursion",
        "horizon_minutes": 30,
        "params": {"threshold_ticks": 4.0},
    }


def _notify(callback: Any, stage: str, percent: int) -> None:
    if callback is not None:
        callback(stage, percent)


def run_sandbox(
    payload: Mapping[str, Any],
    *,
    progress: Any = None,
) -> dict[str, Any]:
    if str(payload.get("split") or "discovery") != "discovery":
        raise PermissionError("sandbox jobs may only request split=discovery")
    contract = _candidate_contract(payload)
    matrix = discovery_matrix()
    train_dates, score_dates = _date_partition()
    _notify(progress, "loading checksum-bound discovery cache", 5)
    train_slice = matrix.load(
        "discovery", contract["allowed_inputs"], dates=train_dates
    )
    score_slice = matrix.load(
        "discovery", contract["allowed_inputs"], dates=score_dates
    )
    _notify(progress, "fitting and transforming isolated candidate", 20)
    train_maps, score_maps, candidate_state, state_hash = _run_candidate_pair(
        contract, train_slice.candidate_rows, score_slice.candidate_rows
    )
    frame = matrix.combined_frame()
    outcome = dict(payload.get("outcome") or _default_outcome(contract["candidate_id"]))
    warmup = max(0, int(payload.get("warmup_minutes") or 60))
    _notify(progress, "constructing evaluator-owned outcomes", 40)
    train_records = _anchor_rows(
        frame, train_maps, outcome, warmup_minutes=warmup
    )
    score_records = _anchor_rows(
        frame, score_maps, outcome, warmup_minutes=warmup
    )
    return _score_sandbox(
        payload,
        contract,
        outcome,
        train_records,
        score_records,
        candidate_state,
        state_hash,
        progress,
    )


def _score_sandbox(
    payload: Mapping[str, Any],
    contract: Mapping[str, Any],
    outcome: Mapping[str, Any],
    train_records: list[dict[str, Any]],
    score_records: list[dict[str, Any]],
    candidate_state: Any,
    state_hash: str,
    progress: Any,
) -> dict[str, Any]:
    if not train_records or not score_records:
        raise ValueError("candidate/outcome combination produced no common anchors")
    treatment = str(
        payload.get("treatment_feature")
        or DEFAULT_TREATMENT.get(contract["candidate_id"])
        or ""
    )
    if treatment not in contract["feature_map"]:
        raise ValueError("treatment_feature is not in the registered feature map")
    baseline_names = [name for name in BASELINE_RAW if name != treatment]
    for name in contract["feature_map"]:
        if name != treatment and name not in baseline_names:
            baseline_names.append(name)
    augmented_names = [*baseline_names, treatment]
    train_features = _model_rows(train_records)
    score_features = _model_rows(score_records)
    train_target = np.asarray([row["target"] for row in train_records], dtype=int)
    score_target = np.asarray([row["target"] for row in score_records], dtype=int)
    _notify(progress, "fitting discovery-only baseline and augmented logits", 58)
    baseline_model = fit_logit(
        train_features, train_target, baseline_names, split="discovery"
    )
    augmented_model = fit_logit(
        train_features, train_target, augmented_names, split="discovery"
    )
    train_baseline = transform_logit(train_features, baseline_model)
    train_augmented = transform_logit(train_features, augmented_model)
    score_baseline = transform_logit(score_features, baseline_model)
    score_augmented = transform_logit(score_features, augmented_model)
    utility_threshold = float(np.quantile(train_augmented - train_baseline, 0.8))
    cuts = _volatility_cuts(train_records)
    selectors = dict(payload.get("slice") or {})
    cell_masks = {"ALL": np.ones(len(score_records), dtype=bool)}
    if selectors:
        cell_masks["FILTERED"] = _slice_mask(score_records, selectors, cuts)
    stability_masks = {
        "RATES_RTH_NO_EVENT": np.asarray(
            [
                row["session"] == "RATES_RTH" and not row["event_blackout"]
                for row in score_records
            ]
        ),
        "OVERNIGHT": np.asarray(
            [row["session"] in {"ASIA", "LONDON"} for row in score_records]
        ),
    }
    seed_key = digest_object(
        {
            "candidate": contract["candidate_id"],
            "config": contract["config"],
            "outcome": outcome,
            "slice": selectors,
            "treatment": treatment,
        }
    )
    _notify(progress, "running optional within-session circular shifts", 68)
    shift_p = _shift_p_values(
        score_records,
        score_features,
        treatment,
        augmented_model,
        score_baseline,
        score_target,
        cell_masks,
        enabled=bool(payload.get("circular_shift", False)),
        seed_key=seed_key,
    )
    _notify(progress, "running 999-resample clustered inference", 82)
    evaluation = evaluate_predictive_cells(
        [row["date"] for row in score_records],
        score_target,
        score_baseline,
        score_augmented,
        cell_masks,
        denominator=len(cell_masks),
        repetitions=REPETITIONS,
        seed_key=seed_key,
        shift_p_values=shift_p,
        utility_threshold=utility_threshold,
        stability_masks=stability_masks,
    )
    _notify(progress, "finalizing exploratory result", 96)
    return {
        "schema_version": 1,
        "label": LABEL,
        "evidentiary_status": "non_evidentiary",
        "split": "discovery",
        "candidate": contract["candidate_id"],
        "config": contract["config"],
        "allowed_inputs": list(contract["allowed_inputs"]),
        "feature_map": dict(contract["feature_map"]),
        "candidate_source_sha256": digest_file(contract["source"]),
        "treatment_feature": treatment,
        "outcome": outcome,
        "slice": selectors,
        "data_hash": discovery_matrix().data_hash,
        "candidate_state_hash": state_hash,
        "candidate_state": candidate_state,
        "baseline_model": asdict(baseline_model),
        "augmented_model": asdict(augmented_model),
        "train": {
            "dates": len({row["date"] for row in train_records}),
            "anchors": len(train_records),
            "positive": int(train_target.sum()),
            "negative": int(len(train_target) - train_target.sum()),
        },
        "score": {
            "dates": len({row["date"] for row in score_records}),
            "anchors": len(score_records),
            "positive": int(score_target.sum()),
            "negative": int(len(score_target) - score_target.sum()),
        },
        "volatility_cuts": list(cuts) if cuts else None,
        "utility_threshold": utility_threshold,
        "circular_shift_enabled": bool(payload.get("circular_shift", False)),
        "inference_repetitions": REPETITIONS,
        "evaluation": evaluation,
        "search_digest": seed_key,
    }
