"""Fail-closed one-shot validation and non-evidentiary quarantine execution."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from scripts.autoresearch.schemas import (
    PredictiveExperimentSpecV2,
    ResultBundleV1,
    RunIdentity,
)

from .candidate_runner import run_candidate
from .discovery import (
    BASELINE_RAW,
    FeatureFrame,
    _common_anchor_timestamps,
    _features_by_timestamp,
    _load_methodology,
    _model_rows,
    _outcome_records,
)
from .evaluator import FrozenLogit, transform_logit
from .execution import ExecutionController, evaluate_predictive_cells
from .matrix import FrozenMatrix
from .preregister import digest_file, digest_object
from .results import build_result_bundle, publish_result_bundle
from .state import StateStore
from .statistics import circular_block_shift, equal_date_effect

VALIDATION_SCOPE = "validation_once"
QUARANTINE_SCOPE = "quarantine_non_evidentiary"
VALIDATION_REPETITIONS = 9_999
MINIMUM_SHIFT_NS = 30 * 60 * 1_000_000_000
TICK_SIZE = 1 / 64
APPROVAL_TEXT_HASH = "3d21daccf67d8845a188de905323e61a5a99c0e16e5de695146028b6f7c41bab"


@dataclass(frozen=True)
class ExecutionContext:
    root: Path
    manifest_path: Path
    authorization_record_path: Path
    manifest: Mapping[str, Any]
    campaign: Path
    adapter: FrozenMatrix
    scope: str


@dataclass(frozen=True)
class FrozenDiscovery:
    bundle: ResultBundleV1
    payload: Mapping[str, Any]
    frozen_state: Mapping[str, Any]
    state_hash: str
    result_hash: str


def authorized_execution_context(
    repo_root: str | Path,
    manifest_path: str | Path,
    authorization_record_path: str | Path,
    *,
    scope: str,
) -> ExecutionContext:
    """Verify a future append-only authorization pair without creating either file."""
    if scope not in {VALIDATION_SCOPE, QUARANTINE_SCOPE}:
        raise ValueError("unsupported execution authorization scope")
    root = Path(repo_root).resolve()
    campaign = root / "workspace" / "analysis" / "aggression_autoresearch_campaign"
    manifests = (campaign / "manifests").resolve()
    manifest_file = Path(manifest_path).resolve()
    record_file = Path(authorization_record_path).resolve()
    if manifest_file.parent != manifests or record_file.parent != manifests:
        raise PermissionError("authorization artifacts must be canonical campaign manifests")
    if not manifest_file.is_file() or not record_file.is_file():
        raise PermissionError("authorization manifest and record are both required")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    record = json.loads(record_file.read_text(encoding="utf-8"))
    approval = manifest.get("owner_approval", {})
    if (
        manifest.get("schema_version") != 1
        or manifest.get("canonical_name") != manifest_file.name
        or manifest.get("append_only") is not True
        or manifest.get("authorized_scope") != scope
        or manifest.get("real_outcomes_authorized") is not True
        or manifest.get("previous_manifest_hash") in {None, "0" * 64}
        or approval.get("scope") != scope
        or approval.get("instruction_sha256") != APPROVAL_TEXT_HASH
    ):
        raise PermissionError("execution manifest is not canonical and append-only")
    if (
        record.get("schema_version") != 1
        or record.get("canonical_name") != record_file.name
        or record.get("scope") != scope
        or record.get("manifest_sha256") != digest_file(manifest_file)
        or record.get("authorization_id") != manifest.get("authorization_id")
        or record.get("instruction_sha256") != APPROVAL_TEXT_HASH
    ):
        raise PermissionError("authorization record does not bind the execution manifest")
    previous_name = manifest.get("previous_manifest_name")
    previous = manifests / str(previous_name)
    if (
        not previous_name
        or previous.resolve().parent != manifests
        or digest_file(previous) != manifest["previous_manifest_hash"]
    ):
        raise ValueError("append-only predecessor binding failed")
    expected_flags = (
        (True, False) if scope == VALIDATION_SCOPE else (False, True)
    )
    if (
        bool(manifest.get("validation_authorized")),
        bool(manifest.get("quarantine_authorized")),
    ) != expected_flags:
        raise PermissionError("authorization scope flags are inconsistent")
    for relative, expected in manifest.get("code_files", {}).items():
        if digest_file(root / relative) != expected:
            raise ValueError(f"execution code hash mismatch: {relative}")
    required_code = {
        "scripts/autoresearch/fixed_env/candidate_runner.py",
        "scripts/autoresearch/fixed_env/discovery.py",
        "scripts/autoresearch/fixed_env/evaluator.py",
        "scripts/autoresearch/fixed_env/validation.py",
        "scripts/autoresearch/fixed_env/execution.py",
        "scripts/autoresearch/fixed_env/matrix.py",
        "scripts/autoresearch/fixed_env/outcomes.py",
        "scripts/autoresearch/fixed_env/preregister.py",
        "scripts/autoresearch/fixed_env/results.py",
        "scripts/autoresearch/fixed_env/state.py",
        "scripts/autoresearch/fixed_env/statistics.py",
    }
    if not required_code.issubset(manifest.get("code_files", {})):
        raise ValueError("execution manifest omits validation-layer code")
    frozen = {
        "split_hash": manifests / "split_manifest_v2.json",
        "event_hash": manifests / "event_manifest.json",
        "exposure_hash": manifests / "exposure_manifest_v2.json",
        "roll_hash": manifests / "roll_manifest_v1.json",
    }
    for field, artifact in frozen.items():
        if digest_file(artifact) != manifest.get(field):
            raise ValueError(f"frozen artifact hash mismatch: {field}")
    version = (
        root / "workspace" / "analysis" / "aggression_autoresearch_data"
        / "versions" / manifest["data_version"]
    )
    adapter = FrozenMatrix(
        version,
        frozen["split_hash"],
        frozen["event_hash"],
        expected_matrix_hash=manifest["data_hash"],
        expected_manifest_hash=manifest["publication_manifest_hash"],
        roll_manifest=frozen["roll_hash"],
        expected_roll_hash=manifest["roll_hash"],
    )
    return ExecutionContext(
        root, manifest_file, record_file, manifest, campaign, adapter, scope,
    )


def load_frozen_discovery(
    context: ExecutionContext,
    experiment_id: str,
    *,
    base_spec_hash: str,
    effective_spec_hash: str,
) -> FrozenDiscovery:
    """Load and independently hash-check the immutable discovery winner."""
    binding = context.manifest.get("discovery_results", {}).get(experiment_id)
    if not isinstance(binding, Mapping):
        raise PermissionError("authorization does not bind a discovery result")
    path = context.campaign / "results" / f"{experiment_id}_discovery.json"
    result_hash = digest_file(path)
    if result_hash != binding.get("sha256"):
        raise ValueError("discovery result hash mismatch")
    raw = json.loads(path.read_text(encoding="utf-8"))
    bundle = ResultBundleV1(**raw)
    payload = bundle.result_payload
    frozen = payload.get("frozen_state")
    state_hash = payload.get("frozen_state_hash")
    if (
        payload.get("experiment_id") != experiment_id
        or payload.get("status") != "selected"
        or payload.get("validation_opened") is not False
        or payload.get("power", {}).get("passes") is not True
        or not isinstance(frozen, Mapping)
        or digest_object(frozen) != state_hash
        or binding.get("frozen_state_hash") != state_hash
        or bundle.base_spec_hash != base_spec_hash
        or bundle.effective_spec_hash != effective_spec_hash
        or frozen.get("effective_spec_hash") != effective_spec_hash
    ):
        raise ValueError("discovery result is not an eligible frozen winner")
    return FrozenDiscovery(bundle, payload, frozen, state_hash, result_hash)


def predictive_cell_masks(
    experiment_id: str,
    frame: pd.DataFrame,
    anchors: Sequence[pd.Timestamp],
    features: Mapping[pd.Timestamp, Mapping[str, float]],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    raw = frame.loc[list(anchors)]
    local = raw.index.tz_convert("America/New_York")
    minute = np.asarray(local.hour * 60 + local.minute)
    rth_no_event = (minute >= 480) & (minute <= 900) & ~raw[
        "event_blackout"
    ].to_numpy(dtype=bool)
    overnight = (minute >= 1080) | (minute < 480)
    if experiment_id == "E001":
        cells = {
            "ALL": np.ones(len(anchors), dtype=bool),
            "RATES_RTH_NO_EVENT": rth_no_event,
            "OVERNIGHT": overnight,
        }
    elif experiment_id == "E002":
        gaps = np.asarray(
            [abs(float(features[item]["formation_gap"])) for item in anchors],
        )
        cells = {
            "ALL_ELIGIBLE": gaps >= 2 * TICK_SIZE,
            "LARGE_GAP": gaps >= 4 * TICK_SIZE,
        }
    else:
        raise ValueError("validation supports E001/E002 only")
    return cells, {"RATES_RTH_NO_EVENT": rth_no_event, "OVERNIGHT": overnight}


def circular_shift_p_values(
    dates: Sequence[object],
    sessions: Sequence[object],
    timestamps_ns: Sequence[int],
    target: Sequence[int],
    baseline_probability: Sequence[float],
    augmented_probability: Sequence[float],
    incremental_feature: Sequence[float],
    *,
    coefficient: float,
    scale: float,
    cell_masks: Mapping[str, Sequence[bool]],
    repetitions: int = VALIDATION_REPETITIONS,
    seed_key: object,
) -> dict[str, float]:
    """Frozen-model falsification by >=30m circular shifts within date/session."""
    if repetitions < 99 or scale <= 0:
        raise ValueError("invalid falsification repetitions or frozen scale")
    day = np.asarray(dates).astype(str)
    session = np.asarray(sessions).astype(str)
    groups = np.char.add(np.char.add(day, "|"), session)
    timestamps = np.asarray(timestamps_ns, dtype=np.int64)
    y = np.asarray(target, dtype=float)
    base = np.asarray(baseline_probability, dtype=float)
    augmented = np.asarray(augmented_probability, dtype=float)
    feature = np.asarray(incremental_feature, dtype=float)
    logits = np.log(np.clip(augmented, 1e-12, 1 - 1e-12) /
                    np.clip(1 - augmented, 1e-12, 1))
    masks = {name: np.asarray(mask, dtype=bool) for name, mask in cell_masks.items()}
    shiftable = np.zeros(len(day), dtype=bool)
    for label in np.unique(groups):
        indices = np.flatnonzero(groups == label)
        if len(indices) >= 2 and int(timestamps[indices].max() - timestamps[indices].min()) >= MINIMUM_SHIFT_NS:
            shiftable[indices] = True
    effective_masks = {
        name: mask & shiftable for name, mask in masks.items()
    }
    observed = {
        name: equal_date_effect(
            day[mask], (base[mask] - y[mask]) ** 2,
            (augmented[mask] - y[mask]) ** 2,
        )
        for name, mask in effective_masks.items()
        if len(np.unique(day[mask])) >= 2
    }
    exceed = {name: 0 for name in masks}
    for repetition in range(repetitions):
        shifted = feature.copy()
        shifted[shiftable] = circular_block_shift(
            feature[shiftable], groups[shiftable], timestamps[shiftable],
            minimum_shift_ns=MINIMUM_SHIFT_NS,
            seed_key=(seed_key, repetition),
        )
        shifted_probability = 1 / (
            1 + np.exp(-np.clip(
                logits + coefficient * (shifted - feature) / scale, -35, 35,
            ))
        )
        for name, mask in effective_masks.items():
            if name not in observed:
                continue
            effect = equal_date_effect(
                day[mask], (base[mask] - y[mask]) ** 2,
                (shifted_probability[mask] - y[mask]) ** 2,
            )
            exceed[name] += int(effect >= observed[name])
    return {
        name: (
            float((1 + count) / (repetitions + 1))
            if name in observed else 1.0
        )
        for name, count in exceed.items()
    }


def e002_tradability_diagnostic(
    frame: pd.DataFrame,
    anchors: Sequence[pd.Timestamp],
    features: Mapping[pd.Timestamp, Mapping[str, float]],
    *,
    repetitions: int = VALIDATION_REPETITIONS,
    seed_key: object = "E002-tradability",
) -> Mapping[str, Any]:
    """Delayed-entry frozen-FV diagnostic with a 0.5-tick round-trip cost."""
    rows: list[dict[str, Any]] = []
    for anchor in anchors:
        formation_price = float(frame.at[anchor, "price"])
        gap = float(features[anchor]["formation_gap"])
        direction = 1.0 if gap > 0 else -1.0
        path = frame.loc[
            (frame.index > anchor) & (frame.index <= anchor + pd.Timedelta(minutes=30)),
            "price",
        ]
        delayed = path.loc[path.index >= anchor + pd.Timedelta(minutes=1)]
        if delayed.empty:
            continue
        entry = float(delayed.iloc[0])
        exit_price = float(delayed.iloc[-1])
        for price in delayed.iloc[1:].to_numpy(dtype=float):
            favorable = direction * (float(price) - formation_price) >= 0.5 * abs(gap)
            adverse = direction * (float(price) - formation_price) <= -abs(gap)
            if favorable or adverse:
                exit_price = float(price)
                break
        signed_path = direction * (delayed.to_numpy(dtype=float) - entry) / TICK_SIZE
        raw = frame.loc[anchor]
        rows.append({
            "date": str(raw["et_date"]),
            "session": str(raw["session"]),
            "event_blackout": bool(raw["event_blackout"]),
            "net_ticks": direction * (exit_price - entry) / TICK_SIZE - 0.5,
            "mae_ticks": float(min(0.0, signed_path.min(initial=0.0))),
        })
    if not rows:
        return {"status": "non_actionable", "reason": "no delayed entries"}
    dates = np.asarray([row["date"] for row in rows])
    net = np.asarray([row["net_ticks"] for row in rows], dtype=float)
    from .statistics import studentized_date_cluster_wild_bootstrap

    inference = studentized_date_cluster_wild_bootstrap(
        dates, np.zeros(len(net)), -net,
        repetitions=repetitions, seed_key=seed_key,
    )
    slices: dict[str, Any] = {}
    for name, mask in {
        "RATES_RTH_NO_EVENT": np.asarray([
            row["session"] == "RATES_RTH" and not row["event_blackout"] for row in rows
        ]),
        "OVERNIGHT": np.asarray([
            row["session"] in {"ASIA", "LONDON"} for row in rows
        ]),
    }.items():
        slices[name] = {
            "count": int(np.count_nonzero(mask)),
            "equal_date_net_ticks": (
                float(np.mean([
                    np.mean(net[mask][dates[mask] == day])
                    for day in np.unique(dates[mask])
                ])) if np.any(mask) else None
            ),
        }
    actionable = (
        inference.effect > 0 and inference.ci_low > 0
        and all(
            item["count"] > 0
            and item["equal_date_net_ticks"] is not None
            and item["equal_date_net_ticks"] > 0
            for item in slices.values()
        )
    )
    return {
        "status": "actionable_diagnostic" if actionable else "non_actionable",
        "entry_delay_minutes": 1,
        "round_trip_cost_ticks": 0.5,
        "coverage": len(rows) / len(anchors) if anchors else 0.0,
        "episodes": len(rows),
        "equal_date_net_ticks": inference.effect,
        "ci_low": inference.ci_low,
        "ci_high": inference.ci_high,
        "mean_mae_ticks": float(np.mean([row["mae_ticks"] for row in rows])),
        "turnover_round_trips": len(rows),
        "stability": slices,
    }


def validation_decision(
    experiment_id: str,
    evaluation: Mapping[str, Any],
    tradability: Mapping[str, Any] | None = None,
) -> str:
    headline = "ALL" if experiment_id == "E001" else "ALL_ELIGIBLE"
    cell = next(item for item in evaluation["cells"] if item["cell_id"] == headline)
    decision = str(cell["decision"])
    limited = (
        decision != "screen_pass"
        and float(cell.get("effect", 0.0)) > 0
        and any(
            item["cell_id"] != headline and item["decision"] == "screen_pass"
            for item in evaluation["cells"]
        )
    )
    if limited:
        decision = "screen_pass_limited"
    if (
        experiment_id == "E002"
        and decision in {"screen_pass", "screen_pass_limited"}
        and (tradability or {}).get("status") != "actionable_diagnostic"
    ):
        return "non_actionable"
    return decision


def build_execution_payload(
    *,
    experiment_id: str,
    split: str,
    frozen: FrozenDiscovery,
    anchor_count: int,
    date_count: int,
    repetitions: int,
    evaluation: Mapping[str, Any],
    tradability: Mapping[str, Any] | None,
    decision: str,
    validation_decision_value: str | None = None,
) -> dict[str, Any]:
    """Keep quarantine explicitly diagnostic and unable to overwrite validation."""
    if split not in {"validation", "quarantine"}:
        raise ValueError("historical split required")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "completed" if split == "validation" else "non_evidentiary",
        "experiment_id": experiment_id,
        "split": split,
        "frozen_discovery_result_hash": frozen.result_hash,
        "frozen_state_hash": frozen.state_hash,
        "transform_only": True,
        "anchor_count": anchor_count,
        "date_count": date_count,
        "inference_repetitions": repetitions,
        "serial_falsification": "within_date_session_circular_shift_min_30m",
        "evaluation": evaluation,
        "tradability_diagnostic": tradability,
    }
    if split == "validation":
        payload["decision"] = decision
    else:
        if not validation_decision_value:
            raise ValueError("quarantine requires the completed validation decision")
        payload.update({
            "evidentiary_status": "non_evidentiary",
            "validation_decision_unchanged": validation_decision_value,
            "decision_overwrite": False,
        })
    return payload


def _run_frozen_split(
    context: ExecutionContext,
    experiment_id: str,
    *,
    split: str,
    timeout_seconds: float,
    repetitions: int,
) -> Mapping[str, Any]:
    if split not in {"validation", "quarantine"}:
        raise ValueError("frozen historical split required")
    spec, _, contract, effective_hash = _load_methodology(context, experiment_id)
    if not isinstance(spec, PredictiveExperimentSpecV2):
        raise ValueError("D001 has no validation/quarantine decision path")
    frozen = load_frozen_discovery(
        context, experiment_id,
        base_spec_hash=spec.hashes.spec_hash,
        effective_spec_hash=effective_hash,
    )
    binding = context.manifest["experiments"][experiment_id]
    identity = RunIdentity(
        experiment_id, spec.hypothesis_family, split,
        binding["candidate_source_hash"], frozen.state_hash,
        spec.hashes.evaluator_hash, context.manifest["data_hash"], effective_hash,
    )
    store = StateStore(
        context.campaign / "state" / "campaign.sqlite",
        output_roots=[context.campaign],
    )
    if split == "quarantine":
        validation_identity = RunIdentity(
            experiment_id, spec.hypothesis_family, "validation",
            identity.candidate_hash, identity.state_hash, identity.evaluator_hash,
            identity.data_hash, identity.spec_hash,
        )
        completed = store.completed_validation(validation_identity)
        expected = context.manifest.get("validation_results", {}).get(
            experiment_id, {},
        ).get("sha256")
        validation_path = context.campaign / "results" / f"{experiment_id}_validation.json"
        if (
            not expected
            or digest_file(validation_path) != expected
            or completed["output_hash"] != expected
        ):
            raise PermissionError("quarantine is not bound to completed validation")
        validation_bundle = json.loads(validation_path.read_text(encoding="utf-8"))
        validation_decision_value = validation_bundle["scorecard"]["decision"]
    else:
        validation_decision_value = None
    dates = context.adapter.assigned_dates(split)
    reservation = store.reserve_historical_with_exposure(
        identity, target=str(spec.outcome.name), trading_dates=dates,
    )
    try:
        loaded = context.adapter.load(
            split, spec.candidate.allowed_inputs, dates=dates,
            evaluator_inputs=BASELINE_RAW,
        )
        candidates = context.root / "scripts" / "autoresearch" / "candidates"
        parsed, unchanged, state_hash = run_candidate(
            candidates / f"{experiment_id.lower()}.py",
            loaded.candidate_rows, split=split, operation="transform",
            frozen_state=frozen.frozen_state["candidate_state"],
            source_roots=[candidates],
            allowed_input_columns=set(spec.candidate.allowed_inputs),
            registered_feature_map=spec.candidate.registered_feature_map,
            config=frozen.frozen_state["config"],
            contract_hash=digest_object(contract),
            declared_grid=spec.design.grids,
            timeout_seconds=timeout_seconds,
        )
        if (
            digest_object(unchanged)
            != digest_object(frozen.frozen_state["candidate_state"])
            or state_hash != digest_object(frozen.frozen_state["candidate_state"])
        ):
            raise ValueError("candidate mutated the discovery-frozen transform state")
        features = (
            FeatureFrame(parsed)
            if experiment_id == "E002" else _features_by_timestamp(parsed)
        )
        anchors = _common_anchor_timestamps(
            loaded.evaluator_frame, [features], experiment_id=experiment_id,
        )
        controller = ExecutionController(actual_run=True)
        target = controller.construct_outcomes(
            experiment_id,
            _outcome_records(loaded.evaluator_frame, anchors, features),
        )
        rows = _model_rows(loaded.evaluator_frame, features, anchors)
        baseline_state = FrozenLogit(**frozen.frozen_state["baseline_model"])
        augmented_state = FrozenLogit(**frozen.frozen_state["augmented_model"])
        baseline = transform_logit(rows, baseline_state)
        augmented = transform_logit(rows, augmented_state)
        cell_masks, stability_masks = predictive_cell_masks(
            experiment_id, loaded.evaluator_frame, anchors, features,
        )
        anchor_frame = loaded.evaluator_frame.loc[list(anchors)]
        incremental_name = augmented_state.feature_names[-1]
        incremental = np.asarray([row[incremental_name] for row in rows], dtype=float)
        feature_index = augmented_state.feature_names.index(incremental_name)
        shifts = circular_shift_p_values(
            anchor_frame["et_date"].astype(str).to_numpy(),
            anchor_frame["session"].astype(str).to_numpy(),
            anchor_frame.index.view("int64"), target, baseline, augmented, incremental,
            coefficient=augmented_state.coefficients[feature_index],
            scale=augmented_state.scales[feature_index],
            cell_masks=cell_masks, repetitions=repetitions,
            seed_key=(experiment_id, split, "circular-shift"),
        )
        evaluation = evaluate_predictive_cells(
            anchor_frame["et_date"].astype(str).to_numpy(),
            target, baseline, augmented, cell_masks,
            denominator=3 if experiment_id == "E001" else 2,
            repetitions=repetitions, seed_key=(experiment_id, split),
            shift_p_values=shifts,
            utility_threshold=float(frozen.frozen_state["utility_threshold"]),
            stability_masks=stability_masks,
        )
        tradability = (
            e002_tradability_diagnostic(
                loaded.evaluator_frame, anchors, features,
                repetitions=repetitions, seed_key=(experiment_id, split, "tradability"),
            )
            if experiment_id == "E002" else None
        )
        decision = validation_decision(experiment_id, evaluation, tradability)
        payload = build_execution_payload(
            experiment_id=experiment_id, split=split, frozen=frozen,
            anchor_count=len(anchors), date_count=len(dates),
            repetitions=repetitions, evaluation=evaluation,
            tradability=tradability, decision=decision,
            validation_decision_value=validation_decision_value,
        )
        headline = "ALL" if experiment_id == "E001" else "ALL_ELIGIBLE"
        bundle, payload_hash, envelope_hash = build_result_bundle(
            payload, identity=identity, base_spec_hash=spec.hashes.spec_hash,
            effective_spec_hash=effective_hash,
            code_manifest_hash=digest_file(context.manifest_path),
            decision=decision if split == "validation" else None,
            headline_cell=headline if split == "validation" else None,
        )
        destination = context.campaign / "results" / f"{experiment_id}_{split}.json"
        receipt_hash = publish_result_bundle(
            store, reservation.run_id, destination, bundle,
            payload_hash=payload_hash, envelope_hash=envelope_hash,
        )
        return {
            "experiment_id": experiment_id, "split": split,
            "status": payload["status"], "decision": (
                decision if split == "validation" else "non_evidentiary"
            ),
            "result_path": str(destination), "result_hash": envelope_hash,
            "receipt_hash": receipt_hash,
        }
    except Exception as exc:
        run = store.get_run(reservation.run_id)
        if run["status"] == "RESERVED" and not run["stats_emitted"]:
            store.fail_no_result(reservation.run_id, str(exc))
        raise


def run_validation(
    repo_root: str | Path,
    manifest_path: str | Path,
    authorization_record_path: str | Path,
    experiment_id: str,
    *,
    timeout_seconds: float = 600.0,
) -> Mapping[str, Any]:
    context = authorized_execution_context(
        repo_root, manifest_path, authorization_record_path,
        scope=VALIDATION_SCOPE,
    )
    if experiment_id not in context.manifest.get("authorized_experiments", ()):
        raise PermissionError(f"manifest does not authorize {experiment_id}")
    return _run_frozen_split(
        context, experiment_id, split="validation",
        timeout_seconds=timeout_seconds, repetitions=VALIDATION_REPETITIONS,
    )


def run_quarantine(
    repo_root: str | Path,
    manifest_path: str | Path,
    authorization_record_path: str | Path,
    experiment_id: str,
    *,
    timeout_seconds: float = 600.0,
) -> Mapping[str, Any]:
    context = authorized_execution_context(
        repo_root, manifest_path, authorization_record_path,
        scope=QUARANTINE_SCOPE,
    )
    if experiment_id not in context.manifest.get("authorized_experiments", ()):
        raise PermissionError(f"manifest does not authorize {experiment_id}")
    return _run_frozen_split(
        context, experiment_id, split="quarantine",
        timeout_seconds=timeout_seconds, repetitions=VALIDATION_REPETITIONS,
    )
