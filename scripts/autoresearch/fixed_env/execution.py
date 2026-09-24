"""Typed execution machinery; real outcomes remain disabled by default."""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from scripts.autoresearch.schemas import (
    DescriptiveSpecV2,
    ExecutionContractV1,
    PredictiveExperimentSpecV2,
    PreprocessingIdentity,
    safe_load_yaml,
)

from .candidate_runner import rows_as_dicts, run_candidate
from .evaluator import (
    calibration_intercept_slope,
    evaluate_brier_studentized,
    relative_brier_score,
)
from .matrix import FrozenMatrix
from .outcomes import e001_bounded_4t_30m, e002_close_first_passage
from .preregister import (
    digest_file,
    digest_object,
    effective_spec_hash,
    freeze_json,
)
from .statistics import enforce_sample_gates, holm_with_denominator, stable_seed
from .state import Reservation, StateStore


@dataclass(frozen=True)
class SelectionResult:
    config: Mapping[str, Any]
    positive_folds: int
    pooled_effect: float
    audit: Mapping[str, Any]


def declared_grid(grid: Mapping[str, Sequence[Any]]) -> tuple[dict[str, Any], ...]:
    names = tuple(grid)
    return tuple(
        dict(zip(names, values))
        for values in itertools.product(*(grid[name] for name in names))
    )


def select_e001(
    fold_effects: Mapping[str, Sequence[float]],
    configs: Mapping[str, Mapping[str, Any]],
    pooled_effects: Mapping[str, float] | None = None,
) -> SelectionResult:
    eligible = []
    for key, effects in fold_effects.items():
        if key not in configs or len(effects) != 4:
            raise ValueError("E001 selection requires four effects per registered config")
        positives = sum(float(value) > 0 for value in effects)
        if positives >= 3:
            config = configs[key]
            pooled = float(
                pooled_effects[key] if pooled_effects is not None else np.mean(effects)
            )
            rank = (
                -pooled, int(config["window_minutes"]),
                -float(config["deadband_ticks"]), int(config["dwell_minutes"]), key,
            )
            eligible.append((rank, key, positives, pooled))
    if not eligible:
        raise ValueError("no E001 configuration passed discovery stability")
    _, key, positives, pooled = min(eligible)
    return SelectionResult(
        dict(configs[key]), positives, pooled,
        {"fold_effects": {name: list(values) for name, values in fold_effects.items()}},
    )


def select_e002(
    inner_mse: Mapping[str, Mapping[float, float]],
    outer_effects: Mapping[int, Sequence[float]],
    final_mse: Mapping[float, float] | None = None,
    pooled_effects: Mapping[int, float] | None = None,
) -> SelectionResult:
    if not inner_mse:
        raise ValueError("E002 inner-fold ridge scores are required")
    selected_lambdas = {
        fold: min(scores, key=lambda value: (float(scores[value]), float(value)))
        for fold, scores in inner_mse.items()
    }
    candidates = []
    tie_order = {15: 0, 5: 1, 30: 2}
    for lookback, effects in outer_effects.items():
        if lookback not in tie_order or len(effects) != 4:
            raise ValueError("E002 requires four outer effects for lookbacks 5/15/30")
        positives = sum(float(value) > 0 for value in effects)
        if positives >= 3:
            pooled = float(
                pooled_effects[lookback]
                if pooled_effects is not None else np.mean(effects)
            )
            candidates.append((
                -pooled, tie_order[lookback], lookback,
                positives, pooled,
            ))
    if not candidates:
        raise ValueError("no E002 lookback passed discovery stability")
    _, _, lookback, positives, pooled = min(candidates)
    final_scores = final_mse or {
        value: float(np.mean([
            scores[value] for scores in inner_mse.values() if value in scores
        ]))
        for value in {value for scores in inner_mse.values() for value in scores}
    }
    pooled_lambda = min(final_scores, key=lambda value: (final_scores[value], float(value)))
    return SelectionResult(
        {
            "ridge_lambda": float(pooled_lambda),
            "formation_lookback_minutes": lookback,
            "entry_gap_ticks": 2,
        },
        positives, pooled,
        {
            "selected_lambda_by_inner_fold": selected_lambdas,
            "final_lambda_mse": {
                format(float(value), ".15g"): float(score)
                for value, score in final_scores.items()
            },
        },
    )


def declared_d001_cells() -> tuple[str, ...]:
    return tuple(
        "|".join(map(str, values))
        for values in itertools.product(
            ("buysell_ratio", "large_trade_share", "trade_intensity"),
            ("LOW", "MID", "HIGH"),
            ("ASIA", "LONDON", "RATES_RTH"),
            ("LOW", "MID", "HIGH"),
            (15, 30, 60),
        )
    )


def _bucket(value: float, lower: float, upper: float) -> str:
    return "LOW" if value < lower else ("HIGH" if value >= upper else "MID")


def _path_summary(path: Sequence[float], anchor: float) -> tuple[float, float, float]:
    values = np.asarray(path, dtype=float)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise ValueError("D001 path must be finite and non-empty")
    moves = values - float(anchor)
    bounded = float(np.all(np.abs(moves) < 4 / 64))
    total = float(np.abs(np.diff(np.r_[anchor, values])).sum())
    efficiency = 0.0 if total == 0 else abs(float(values[-1] - anchor)) / total
    return bounded, 1.0 - bounded, efficiency


def evaluate_d001_map(
    records: Sequence[Mapping[str, Any]],
    boundaries: Mapping[str, Sequence[float]],
    *,
    first_half_end: str,
) -> Mapping[str, Any]:
    cells = declared_d001_cells()
    collected: dict[str, list[dict[str, Any]]] = {cell: [] for cell in cells}
    context_rows: dict[tuple[str, str, int, str], list[dict[str, Any]]] = {}
    late_rows: dict[int, list[dict[str, float]]] = {15: [], 30: [], 60: []}
    for record in records:
        half = "FIRST" if str(record["date"]) <= first_half_end else "SECOND"
        vol_bucket = _bucket(
            float(record["realized_vol_context"]), *boundaries["realized_vol"],
        )
        for proxy in ("buysell_ratio", "large_trade_share", "trade_intensity"):
            proxy_bucket = _bucket(float(record[proxy]), *boundaries[proxy])
            horizons = (
                (int(record["horizon"]),)
                if "horizon" in record else (15, 30, 60)
            )
            for horizon in horizons:
                path = (
                    record["path"] if "horizon" in record
                    else record["paths"][str(horizon)]
                )
                bounded, resolved, efficiency = _path_summary(
                    path, float(record["price"]),
                )
                if str(record["session"]) == "LATE" and proxy == "buysell_ratio":
                    late_rows[horizon].append({
                        "bounded": bounded, "resolved": resolved,
                        "path_efficiency": efficiency,
                    })
                row = {
                    "date": str(record["date"]), "half": half,
                    "proxy_bucket": proxy_bucket, "bounded": bounded,
                    "resolved": resolved, "path_efficiency": efficiency,
                    "event": bool(record.get("event_blackout", False)),
                }
                cell = "|".join((
                    proxy, proxy_bucket, str(record["session"]), vol_bucket, str(horizon),
                ))
                if cell in collected:
                    collected[cell].append(row)
                context_rows.setdefault(
                    (proxy, str(record["session"]), horizon, vol_bucket), [],
                ).append(row)
    audit, nominations = [], []
    for cell in cells:
        proxy, bucket, session, vol_bucket, horizon_text = cell.split("|")
        rows = collected[cell]
        context = context_rows.get((proxy, session, int(horizon_text), vol_bucket), [])
        summary: dict[str, Any] = {"cell_id": cell, "anchors": len(rows)}
        lifts = []
        eligible = True
        for half in ("FIRST", "SECOND"):
            selected = [row for row in rows if row["half"] == half]
            complement = [
                row for row in context
                if row["half"] == half and row["proxy_bucket"] != bucket
            ]
            dates = len({row["date"] for row in selected})
            lift = (
                float(np.mean([row["bounded"] for row in selected]))
                - float(np.mean([row["bounded"] for row in complement]))
                if selected and complement else float("nan")
            )
            summary[half.lower()] = {
                "anchors": len(selected), "dates": dates,
                "lift": float(lift) if np.isfinite(lift) else None,
            }
            lifts.append(lift)
            eligible &= dates >= 25 and len(selected) >= 50 and np.isfinite(lift)
        eligible &= all(abs(value) >= 0.08 for value in lifts)
        eligible &= np.sign(lifts[0]) == np.sign(lifts[1])
        dominant_sign = np.sign(lifts[0]) if np.isfinite(lifts[0]) else 0
        for event_value in (False, True):
            selected = [row for row in rows if row["event"] is event_value]
            complement = [
                row for row in context
                if row["event"] is event_value and row["proxy_bucket"] != bucket
            ]
            if len(selected) >= 20 and len({row["date"] for row in selected}) >= 10 and complement:
                slice_lift = (
                    float(np.mean([row["bounded"] for row in selected]))
                    - float(np.mean([row["bounded"] for row in complement]))
                )
                if dominant_sign * slice_lift <= -0.04:
                    eligible = False
        summary["nomination_eligible"] = bool(eligible)
        audit.append(summary)
        if eligible:
            nominations.append(summary)
    proxy_order = {"buysell_ratio": 0, "large_trade_share": 1, "trade_intensity": 2}
    bucket_order = {"MID": 0, "LOW": 1, "HIGH": 2}
    session_order = {"RATES_RTH": 0, "LONDON": 1, "ASIA": 2}
    horizon_order = {"30": 0, "15": 1, "60": 2}
    nominations.sort(key=lambda item: (
        -min(abs(item["first"]["lift"]), abs(item["second"]["lift"])),
        proxy_order[item["cell_id"].split("|")[0]],
        bucket_order[item["cell_id"].split("|")[1]],
        session_order[item["cell_id"].split("|")[2]],
        bucket_order[item["cell_id"].split("|")[3]],
        horizon_order[item["cell_id"].split("|")[4]],
        item["cell_id"],
    ))
    late_diagnostic = [{
        "horizon_minutes": horizon,
        "anchors": len(rows),
        "bounded_share": float(np.mean([row["bounded"] for row in rows])) if rows else None,
        "resolved_share": float(np.mean([row["resolved"] for row in rows])) if rows else None,
        "mean_path_efficiency": (
            float(np.mean([row["path_efficiency"] for row in rows])) if rows else None
        ),
    } for horizon, rows in late_rows.items()]
    return {
        "declared_cell_count": 243, "cells": audit,
        "nominations": nominations[:2], "late_aggregate_diagnostic": late_diagnostic,
    }


def _utility_summary(
    dates: np.ndarray,
    target: np.ndarray,
    selected: np.ndarray,
    *,
    repetitions: int,
    seed_key: object,
) -> Mapping[str, Any]:
    coverage = float(np.mean(selected))
    selected_count = int(np.count_nonzero(selected))
    lift = (
        float(np.mean(target[selected]) - np.mean(target))
        if selected_count else 0.0
    )
    unique = np.unique(dates)
    rng = np.random.default_rng(stable_seed(seed_key))
    boot = []
    if selected_count and len(unique) >= 2:
        for _ in range(repetitions):
            sampled = rng.choice(unique, size=len(unique), replace=True)
            indices = np.concatenate([np.flatnonzero(dates == label) for label in sampled])
            chosen = selected[indices]
            if np.any(chosen):
                boot.append(float(np.mean(target[indices][chosen]) - np.mean(target[indices])))
    ci_low = float(np.quantile(boot, 0.025)) if boot else -1.0
    return {
        "coverage": coverage, "selected_episodes": selected_count,
        "target_rate_lift": lift, "ci_low": ci_low,
        "passes": bool(
            coverage >= 0.10 and selected_count >= 30
            and lift >= 0.08 and ci_low > 0
        ),
    }


def _stability_ok(
    dates: np.ndarray,
    target: np.ndarray,
    baseline: np.ndarray,
    augmented: np.ndarray,
    masks: Mapping[str, Sequence[bool]],
    *,
    repetitions: int,
    seed_key: object,
) -> bool:
    if not masks:
        return True
    evaluated = False
    for name, raw_mask in masks.items():
        mask = np.asarray(raw_mask, dtype=bool)
        if mask.shape != dates.shape or len(np.unique(dates[mask])) < 2:
            return False
        if not np.any(mask):
            continue
        evaluated = True
        result = evaluate_brier_studentized(
            dates[mask], target[mask], baseline[mask], augmented[mask],
            repetitions=repetitions, seed_key=(seed_key, name),
        )
        if result.inference.effect <= -0.0025 or result.inference.ci_high < 0:
            return False
    return evaluated


def evaluate_predictive_cells(
    dates: Sequence[object],
    target: Sequence[int],
    baseline_probability: Sequence[float],
    augmented_probability: Sequence[float],
    cell_masks: Mapping[str, Sequence[bool]],
    *,
    denominator: int,
    repetitions: int,
    seed_key: object,
    shift_p_values: Mapping[str, float] | None = None,
    utility_threshold: float | None = None,
    stability_masks: Mapping[str, Sequence[bool]] | None = None,
) -> Mapping[str, Any]:
    day = np.asarray(dates).astype(str)
    outcome = np.asarray(target, dtype=int)
    base = np.asarray(baseline_probability, dtype=float)
    augmented = np.asarray(augmented_probability, dtype=float)
    raw: list[dict[str, Any]] = []
    for cell, raw_mask in cell_masks.items():
        mask = np.asarray(raw_mask, dtype=bool)
        if mask.shape != day.shape:
            raise ValueError("cell mask must align with evaluation vectors")
        try:
            enforce_sample_gates(
                day[mask], outcome[mask], min_dates=25,
                min_positive=30, min_negative=30,
            )
        except ValueError as exc:
            raw.append({"cell_id": cell, "decision": "blocked_underpowered", "error": str(exc)})
            continue
        result = evaluate_brier_studentized(
            day[mask], outcome[mask], base[mask], augmented[mask],
            repetitions=repetitions, seed_key=(seed_key, cell),
        )
        loss_difference = (base[mask] - outcome[mask]) ** 2 - (
            augmented[mask] - outcome[mask]
        ) ** 2
        cell_dates = day[mask]
        date_effects = {
            label: float(np.mean(loss_difference[cell_dates == label]))
            for label in np.unique(cell_dates)
        }
        positive_sum = sum(max(value, 0.0) for value in date_effects.values())
        concentration = (
            max((max(value, 0.0) for value in date_effects.values()), default=0.0)
            / positive_sum if positive_sum else 1.0
        )
        effect_dates = np.asarray(sorted(date_effects))
        months = np.asarray([value[:7] for value in effect_dates])
        effect_values = np.asarray([date_effects[value] for value in effect_dates])
        month_effects = [
            float(np.mean(effect_values[months == label]))
            for label in np.unique(months)
        ]
        threshold = (
            float(utility_threshold) if utility_threshold is not None
            else float(np.quantile((augmented - base)[mask], 0.8))
        )
        selected = (augmented - base)[mask] >= threshold
        utility = _utility_summary(
            cell_dates, outcome[mask], selected,
            repetitions=repetitions, seed_key=(seed_key, cell, "utility"),
        )
        raw.append({
            "cell_id": cell, "effect": result.inference.effect,
            "ci_low": result.inference.ci_low, "ci_high": result.inference.ci_high,
            "p_raw": result.inference.p_one_sided,
            "n_dates": result.inference.n_dates,
            "n_positive": int(np.count_nonzero(outcome[mask] == 1)),
            "n_negative": int(np.count_nonzero(outcome[mask] == 0)),
            "calibration": list(
                calibration_intercept_slope(outcome[mask], augmented[mask])
            ),
            "relative_brier_skill": relative_brier_score(
                outcome[mask], augmented[mask], base[mask],
            ),
            "positive_month_fraction": float(np.mean(np.asarray(month_effects) > 0)),
            "month_count": len(month_effects),
            "positive_effect_concentration": concentration,
            "utility": utility,
        })
    tested = [item for item in raw if "p_raw" in item]
    adjusted, _, _ = holm_with_denominator(
        [item["p_raw"] for item in tested], denominator=denominator,
    )
    for item, p_adjusted in zip(tested, adjusted):
        item["p_adjusted"] = p_adjusted
        shift_p = float((shift_p_values or {}).get(item["cell_id"], 1.0))
        item["shift_p"] = shift_p
        stability_source = (
            {item["cell_id"]: stability_masks[item["cell_id"]]}
            if stability_masks and item["cell_id"] in stability_masks
            else (stability_masks or {})
        )
        local_stability = {
            name: np.asarray(value, dtype=bool)[mask]
            for name, value in stability_source.items()
        }
        stability_ok = _stability_ok(
            day[mask], outcome[mask], base[mask], augmented[mask], local_stability,
            repetitions=repetitions, seed_key=(seed_key, item["cell_id"], "stability"),
        )
        item["stability_passes"] = stability_ok
        gate = (
            item["effect"] >= 0.005 and p_adjusted <= 0.05
            and item["ci_low"] > 0 and shift_p <= 0.05
            and item["relative_brier_skill"] >= 0.02
            and item["month_count"] >= 4
            and item["positive_month_fraction"] >= 0.60
            and item["positive_effect_concentration"] <= 0.25
            and item["utility"]["passes"] and stability_ok
        )
        if gate:
            item["decision"] = "screen_pass"
        elif item["ci_high"] < 0.005:
            item["decision"] = "not_confirmed"
        elif item["effect"] > 0:
            item["decision"] = "positive_but_trivial"
        else:
            item["decision"] = "inconclusive"
    return {"cells": raw, "denominator": denominator}


class ExecutionController:
    def __init__(self, *, actual_run: bool = False) -> None:
        self.actual_run = actual_run

    def require_actual_run(self) -> None:
        if not self.actual_run:
            raise PermissionError("preflight cannot score outcomes or reserve campaign state")

    def evaluate(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        self.require_actual_run()
        return evaluate_predictive_cells(*args, **kwargs)

    def select_discovery(
        self,
        experiment_id: str,
        *,
        fold_effects: Mapping[Any, Sequence[float]],
        configs: Mapping[str, Mapping[str, Any]] | None = None,
        inner_mse: Mapping[str, Mapping[float, float]] | None = None,
        final_mse: Mapping[float, float] | None = None,
        pooled_effects: Mapping[Any, float] | None = None,
    ) -> SelectionResult:
        self.require_actual_run()
        if experiment_id == "E001":
            if configs is None:
                raise ValueError("E001 requires registered configs")
            return select_e001(fold_effects, configs, pooled_effects)
        if experiment_id == "E002":
            if inner_mse is None:
                raise ValueError("E002 requires inner ridge MSE")
            return select_e002(
                inner_mse, fold_effects, final_mse, pooled_effects,
            )
        raise ValueError("descriptive D001 has no predictive discovery winner")

    def seal_discovery(
        self,
        store: StateStore,
        identity: PreprocessingIdentity,
        state_hash: str,
    ) -> Reservation:
        self.require_actual_run()
        reservation = store.reserve_preprocessing(identity)
        return store.seal_preprocessing(reservation.run_id, state_hash)

    def construct_outcomes(
        self, experiment_id: str, records: Sequence[Mapping[str, Any]]
    ) -> np.ndarray:
        self.require_actual_run()
        if experiment_id == "E001":
            return np.asarray([
                e001_bounded_4t_30m(
                    np.asarray(record["close_path"], dtype=float),
                    float(record["anchor_price"]),
                )
                for record in records
            ], dtype=int)
        if experiment_id == "E002":
            return np.asarray([
                e002_close_first_passage(
                    np.asarray(record["close_timestamps_ns"], dtype=np.int64),
                    np.asarray(record["close_path"], dtype=float),
                    float(record["anchor_price"]),
                    formation_gap=float(record["formation_gap"]),
                )
                for record in records
            ], dtype=int)
        raise ValueError("D001 uses descriptive path summaries, not predictive outcomes")


def load_execution_contract(path: str | Path) -> tuple[ExecutionContractV1, Mapping[str, Any], str]:
    raw = safe_load_yaml(path)
    contract = ExecutionContractV1.from_dict(raw)
    return contract, raw, effective_spec_hash(contract.base_spec_hash, raw)


def _candidate_configs(experiment_id: str, spec: Any) -> tuple[dict[str, Any], ...]:
    if experiment_id == "D001":
        return ({"map": "DECLARED_243"},)
    return declared_grid(spec.design.grids)


def run_preflight(repo_root: str | Path, manifest_path: str | Path) -> Mapping[str, Any]:
    root = Path(repo_root).resolve()
    manifest_file = Path(manifest_path).resolve()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("real_outcomes_authorized") or manifest.get("validation_authorized"):
        raise PermissionError("preflight manifest must keep real outcomes and validation locked")
    for relative, expected in manifest["code_files"].items():
        if digest_file(root / relative) != expected:
            raise ValueError(f"execution code hash mismatch: {relative}")
    campaign = root / "workspace" / "analysis" / "aggression_autoresearch_campaign"
    data_version = (
        root / "workspace" / "analysis" / "aggression_autoresearch_data"
        / "versions" / manifest["data_version"]
    )
    adapter = FrozenMatrix(
        data_version, campaign / "manifests" / "split_manifest_v2.json",
        campaign / "manifests" / "event_manifest.json",
        expected_matrix_hash=manifest["data_hash"],
        expected_manifest_hash=manifest["publication_manifest_hash"],
    )
    experiment_reports = []
    candidates_root = root / "scripts" / "autoresearch" / "candidates"
    for experiment_id in ("E001", "E002", "D001"):
        spec_raw = safe_load_yaml(
            root / "scripts" / "autoresearch" / "registry" / "experiments"
            / f"{experiment_id}.yaml"
        )
        spec = (
            DescriptiveSpecV2.from_dict(spec_raw)
            if experiment_id == "D001" else PredictiveExperimentSpecV2.from_dict(spec_raw)
        )
        contract_path = (
            root / "scripts" / "autoresearch" / "registry" / "execution_contracts"
            / f"{experiment_id}.yaml"
        )
        contract, contract_raw, effective_hash = load_execution_contract(contract_path)
        binding = manifest["experiments"][experiment_id]
        if (
            binding["base_spec_hash"] != contract.base_spec_hash
            or binding["execution_contract_hash"] != digest_object(contract_raw)
            or binding["effective_spec_hash"] != effective_hash
        ):
            raise ValueError(f"execution binding mismatch: {experiment_id}")
        first_date = adapter.assigned_dates("discovery")[0]
        loaded = adapter.load(
            "discovery", spec.candidate.allowed_inputs, dates=[first_date],
        )
        smoke_rows = list(loaded.candidate_rows[:90])
        configs = _candidate_configs(experiment_id, spec)
        grid = (
            {"map": ["DECLARED_243"]}
            if experiment_id == "D001" else spec.design.grids
        )
        hashes = []
        for config in configs:
            parsed, state, state_hash = run_candidate(
                candidates_root / f"{experiment_id.lower()}.py",
                smoke_rows, split="discovery", source_roots=[candidates_root],
                allowed_input_columns=set(spec.candidate.allowed_inputs),
                registered_feature_map=spec.candidate.registered_feature_map,
                config=config, contract_hash=digest_object(contract_raw),
                declared_grid=grid, timeout_seconds=60,
            )
            first, unchanged, _ = run_candidate(
                candidates_root / f"{experiment_id.lower()}.py",
                smoke_rows, split="validation", frozen_state=state,
                source_roots=[candidates_root],
                allowed_input_columns=set(spec.candidate.allowed_inputs),
                registered_feature_map=spec.candidate.registered_feature_map,
                config=config, contract_hash=digest_object(contract_raw),
                declared_grid=grid, timeout_seconds=60,
            )
            second, unchanged_again, _ = run_candidate(
                candidates_root / f"{experiment_id.lower()}.py",
                smoke_rows, split="validation", frozen_state=state,
                source_roots=[candidates_root],
                allowed_input_columns=set(spec.candidate.allowed_inputs),
                registered_feature_map=spec.candidate.registered_feature_map,
                config=config, contract_hash=digest_object(contract_raw),
                declared_grid=grid, timeout_seconds=60,
            )
            if unchanged != state or unchanged_again != state:
                raise ValueError("candidate mutated runner-bound state")
            first_hash = digest_object(rows_as_dicts(first))
            if first_hash != digest_object(rows_as_dicts(second)):
                raise ValueError("candidate transform is nondeterministic")
            hashes.append({"config": config, "state_hash": state_hash, "output_hash": first_hash})
        experiment_reports.append({
            "experiment_id": experiment_id, "formation_date": first_date,
            "formation_rows": len(smoke_rows), "configurations": hashes,
        })
    report = {
        "schema_version": 1, "execution_manifest_hash": digest_file(manifest_file),
        "experiments": experiment_reports,
        "forbidden_call_counters": {
            "outcome_constructions": 0, "scoring_calls": 0,
            "state_reservations": 0, "exposure_writes": 0,
        },
        "status": "PASS",
    }
    destination = campaign / "integrity" / (
        f"preflight_{report['execution_manifest_hash'][:12]}.json"
    )
    freeze_json(destination, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", nargs="?",
        choices=("run-discovery", "run-validation", "run-quarantine"),
    )
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--experiment", choices=("E001", "E002", "D001"))
    parser.add_argument("--manifest")
    parser.add_argument("--authorization-record")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    manifests = (
        root / "workspace" / "analysis" / "aggression_autoresearch_campaign"
        / "manifests"
    )
    if args.preflight:
        if args.command:
            parser.error("--preflight cannot be combined with an execution command")
        report = run_preflight(root, manifests / "execution_manifest_v2.json")
    elif args.command == "run-discovery":
        if not args.experiment or not args.manifest:
            parser.error("run-discovery requires --experiment and --manifest")
        from .discovery import run_discovery

        manifest = Path(args.manifest)
        if not manifest.is_absolute():
            manifest = manifests / manifest
        report = run_discovery(
            root, manifest, args.experiment,
            timeout_seconds=args.timeout_seconds,
        )
    elif args.command in {"run-validation", "run-quarantine"}:
        if (
            not args.experiment or args.experiment == "D001"
            or not args.manifest or not args.authorization_record
        ):
            parser.error(
                f"{args.command} requires E001/E002, --manifest, "
                "and --authorization-record"
            )
        from .validation import run_quarantine, run_validation

        manifest = Path(args.manifest)
        record = Path(args.authorization_record)
        if not manifest.is_absolute():
            manifest = manifests / manifest
        if not record.is_absolute():
            record = manifests / record
        runner = run_validation if args.command == "run-validation" else run_quarantine
        report = runner(
            root, manifest, record, args.experiment,
            timeout_seconds=args.timeout_seconds,
        )
    else:
        parser.error("choose --preflight or run-discovery")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    main()
