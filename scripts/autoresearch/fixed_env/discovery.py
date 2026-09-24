"""Authorized discovery orchestration over frozen autoresearch artifacts."""

from __future__ import annotations

import gc
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from scripts.autoresearch.schemas import (
    DescriptiveSpecV2,
    PredictiveExperimentSpecV2,
    PreprocessingIdentity,
    RunIdentity,
    safe_load_yaml,
)

from .candidate_runner import run_candidate
from .evaluator import (
    construct_common_anchors,
    fit_logit,
    transform_logit,
)
from .execution import (
    ExecutionController,
    SelectionResult,
    declared_grid,
    evaluate_d001_map,
    load_execution_contract,
)
from .matrix import CandidateRows, FrozenMatrix, MatrixSlice
from .preregister import canonical_spec_hash, digest_file, digest_object
from .results import build_result_bundle, publish_result_bundle
from .state import StateStore
from .statistics import equal_date_effect, non_overlapping_anchors, power_date_requirement

HORIZON_NS = 30 * 60 * 1_000_000_000
APPROVED_MANIFEST_NAME = "execution_manifest_v3_7.json"
AUTHORIZATION_RECORD_NAME = "discovery_authorization_v8.json"
PREVIOUS_MANIFEST_NAME = "execution_manifest_v3_6.json"
PREVIOUS_MANIFEST_HASH = "ab71e671526100de5f53575b4f7a9491bbd8417a0758bcda23d5a035b2f2fa40"
APPROVAL_TEXT_HASH = "b12fc6900006dc338656d0f5675c0b00630cd1038d77dbb91b1d0bbaa3cf52fd"
BASELINE_RAW = (
    "realized_vol", "trade_intensity", "spread_mean",
    "session_sin", "session_cos",
)


@dataclass(frozen=True)
class DiscoveryContext:
    root: Path
    manifest_path: Path
    manifest: Mapping[str, Any]
    campaign: Path
    adapter: FrozenMatrix


class FeatureFrame(Mapping[pd.Timestamp, Mapping[str, float]]):
    """Compact candidate feature mapping backed by a numeric DataFrame."""

    def __init__(self, rows: Sequence[Any]) -> None:
        if not rows:
            self.frame = pd.DataFrame()
            return
        names = tuple(rows[0].features)
        data = {
            name: np.fromiter(
                (float(row.features[name]) for row in rows),
                dtype=float, count=len(rows),
            )
            for name in names
        }
        timestamps = np.fromiter(
            (pd.Timestamp(row.formation_ts).value for row in rows),
            dtype=np.int64, count=len(rows),
        )
        self.frame = pd.DataFrame(
            data, index=pd.to_datetime(timestamps, utc=True),
        )

    def __len__(self) -> int:
        return len(self.frame)

    def __iter__(self) -> Any:
        return iter(self.frame.index)

    def __getitem__(self, timestamp: pd.Timestamp) -> Mapping[str, float]:
        return self.frame.loc[timestamp].to_dict()


def authorized_context(
    repo_root: str | Path, manifest_path: str | Path,
) -> DiscoveryContext:
    root = Path(repo_root).resolve()
    path = Path(manifest_path).resolve()
    campaign = root / "workspace" / "analysis" / "aggression_autoresearch_campaign"
    manifests = (campaign / "manifests").resolve()
    if path.parent != manifests or path.name != APPROVED_MANIFEST_NAME:
        raise PermissionError("discovery requires the canonical v3 authorization manifest")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (
        manifest.get("authorized_scope") != "discovery_only"
        or manifest.get("real_outcomes_authorized") is not True
        or manifest.get("validation_authorized") is not False
    ):
        raise PermissionError("manifest does not authorize discovery-only outcomes")
    approval = manifest.get("owner_approval", {})
    if (
        manifest.get("previous_manifest_hash") != PREVIOUS_MANIFEST_HASH
        or approval.get("scope") != "discovery_only"
        or approval.get("instruction_sha256") != APPROVAL_TEXT_HASH
    ):
        raise PermissionError("discovery authorization chain is invalid")
    authorization_record = json.loads(
        (manifests / AUTHORIZATION_RECORD_NAME).read_text(encoding="utf-8")
    )
    if (
        authorization_record.get("manifest_sha256") != digest_file(path)
        or authorization_record.get("scope") != "discovery_only"
        or authorization_record.get("instruction_sha256") != APPROVAL_TEXT_HASH
    ):
        raise PermissionError("authorization record does not bind the v3 manifest")
    previous = manifests / PREVIOUS_MANIFEST_NAME
    if digest_file(previous) != PREVIOUS_MANIFEST_HASH:
        raise ValueError("previous execution manifest changed")
    previous_payload = json.loads(previous.read_text(encoding="utf-8"))
    required_code = set(previous_payload["code_files"]) | {
        "scripts/autoresearch/fixed_env/discovery.py",
    }
    if set(manifest.get("code_files", {})) != required_code:
        raise ValueError("authorization manifest code membership mismatch")
    frozen = {
        "split_hash": manifests / "split_manifest_v2.json",
        "event_hash": manifests / "event_manifest.json",
        "exposure_hash": manifests / "exposure_manifest_v2.json",
        "roll_hash": manifests / "roll_manifest_v1.json",
    }
    for field, artifact in frozen.items():
        if digest_file(artifact) != manifest[field]:
            raise ValueError(f"frozen artifact hash mismatch: {field}")
    split = json.loads(frozen["split_hash"].read_text(encoding="utf-8"))
    cohorts = [
        set(split[name]) for name in (
            "discovery_dates", "validation_dates", "historical_quarantine_dates",
        )
    ]
    if any(cohorts[left] & cohorts[right] for left in range(3) for right in range(left + 1, 3)):
        raise ValueError("frozen historical splits overlap")
    for relative, expected in manifest["code_files"].items():
        if digest_file(root / relative) != expected:
            raise ValueError(f"execution code hash mismatch: {relative}")
    version = (
        root / "workspace" / "analysis" / "aggression_autoresearch_data"
        / "versions" / manifest["data_version"]
    )
    adapter = FrozenMatrix(
        version,
        campaign / "manifests" / "split_manifest_v2.json",
        campaign / "manifests" / "event_manifest.json",
        expected_matrix_hash=manifest["data_hash"],
        expected_manifest_hash=manifest["publication_manifest_hash"],
        roll_manifest=manifests / "roll_manifest_v1.json",
        expected_roll_hash=manifest["roll_hash"],
    )
    return DiscoveryContext(root, path, manifest, campaign, adapter)


def _load_methodology(
    context: DiscoveryContext, experiment_id: str,
) -> tuple[Any, Mapping[str, Any], Mapping[str, Any], str]:
    if experiment_id not in {"E001", "E002", "D001"}:
        raise ValueError("experiment must be E001, E002, or D001")
    registry = context.root / "scripts" / "autoresearch" / "registry"
    raw = safe_load_yaml(registry / "experiments" / f"{experiment_id}.yaml")
    spec = (
        DescriptiveSpecV2.from_dict(raw)
        if experiment_id == "D001" else PredictiveExperimentSpecV2.from_dict(raw)
    )
    if canonical_spec_hash(raw) != spec.hashes.spec_hash:
        raise ValueError(f"methodology spec hash mismatch: {experiment_id}")
    contract, contract_raw, effective_hash = load_execution_contract(
        registry / "execution_contracts" / f"{experiment_id}.yaml"
    )
    binding = context.manifest["experiments"][experiment_id]
    if (
        spec.hashes.spec_hash != contract.base_spec_hash
        or binding["base_spec_hash"] != spec.hashes.spec_hash
        or binding["base_spec_hash"] != contract.base_spec_hash
        or binding["execution_contract_hash"] != digest_object(contract_raw)
        or binding["effective_spec_hash"] != effective_hash
        or binding["candidate_source_hash"]
        != digest_file(
            context.root / "scripts" / "autoresearch" / "candidates"
            / f"{experiment_id.lower()}.py"
        )
    ):
        raise ValueError(f"execution binding mismatch: {experiment_id}")
    return spec, raw, contract_raw, effective_hash


def _candidate_call(
    context: DiscoveryContext,
    experiment_id: str,
    spec: Any,
    contract_raw: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    grid: Mapping[str, Sequence[Any]],
    *,
    frozen_state: Any = None,
    operation: str = "fit_transform",
    timeout_seconds: float,
) -> tuple[list[Any], Any, str]:
    candidates = context.root / "scripts" / "autoresearch" / "candidates"
    return run_candidate(
        candidates / f"{experiment_id.lower()}.py",
        rows,
        split="discovery",
        operation=operation,
        frozen_state=frozen_state,
        source_roots=[candidates],
        allowed_input_columns=set(spec.candidate.allowed_inputs),
        registered_feature_map=spec.candidate.registered_feature_map,
        config=config,
        contract_hash=digest_object(contract_raw),
        declared_grid=grid,
        timeout_seconds=timeout_seconds,
    )


def _features_by_timestamp(rows: Sequence[Any]) -> dict[pd.Timestamp, dict[str, float]]:
    return {
        pd.Timestamp(row.formation_ts): {
            str(name): float(value) for name, value in row.features.items()
        }
        for row in rows
    }


def _session_boundaries(frame: pd.DataFrame) -> tuple[dict[str, int], dict[str, int]]:
    opens: dict[str, int] = {}
    closes: dict[str, int] = {}
    for day, group in frame.groupby(frame["et_date"].astype(str), sort=False):
        values = group.index.view("int64")
        opens[str(day)] = int(values.min())
        closes[str(day)] = int(values.max())
    return opens, closes


def _common_anchor_timestamps(
    frame: pd.DataFrame,
    feature_maps: Sequence[Mapping[pd.Timestamp, Mapping[str, float]]],
    *,
    experiment_id: str,
    horizon_minutes: int = 30,
) -> tuple[pd.Timestamp, ...]:
    common = set.intersection(*(set(item) for item in feature_maps))
    available = frame.index.isin(common)
    if experiment_id == "E002":
        reference = feature_maps[0]
        eligible = {
            timestamp for timestamp in common
            if abs(reference[timestamp]["formation_gap"]) >= 2 / 64
        }
        available &= frame.index.isin(eligible)
    opens, closes = _session_boundaries(frame)
    anchors = construct_common_anchors(
        frame["et_date"].astype(str).to_numpy(),
        frame.index.view("int64"),
        {
            "price": np.isfinite(frame["price"].to_numpy(dtype=float)),
            "candidate": available,
        },
        horizon_ns=horizon_minutes * 60 * 1_000_000_000,
        session_open_ns=opens,
        session_close_ns=closes,
        minimum_future_coverage=0.8,
        horizon_tolerance_ns=2 * 60 * 1_000_000_000,
    )
    selected: list[pd.Timestamp] = []
    for day in np.unique(anchors.dates):
        day_ns = anchors.timestamps_ns[anchors.dates == day]
        spaced = non_overlapping_anchors(
            day_ns, horizon_minutes * 60 * 1_000_000_000,
        )
        selected.extend(pd.Timestamp(value, tz="UTC") for value in spaced)
    return tuple(sorted(selected))


def _model_rows(
    frame: pd.DataFrame,
    features: Mapping[pd.Timestamp, Mapping[str, float]],
    anchors: Sequence[pd.Timestamp],
) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for timestamp in anchors:
        raw = frame.loc[timestamp]
        row = dict(features[timestamp])
        for name in BASELINE_RAW:
            row[name] = float(raw[name])
        row["event_blackout"] = float(bool(raw["event_blackout"]))
        row["roll_flag"] = float(bool(raw["roll_flag"]))
        rows.append(row)
    return rows


def _outcome_records(
    frame: pd.DataFrame,
    anchors: Sequence[pd.Timestamp],
    features: Mapping[pd.Timestamp, Mapping[str, float]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for timestamp in anchors:
        stop = timestamp + pd.Timedelta(minutes=30)
        path = frame.loc[(frame.index > timestamp) & (frame.index <= stop), "price"]
        records.append({
            "anchor_price": float(frame.at[timestamp, "price"]),
            "close_path": path.to_numpy(dtype=float).tolist(),
            "close_timestamps_ns": path.index.view("int64").tolist(),
            "formation_gap": float(features[timestamp].get("formation_gap", 0.0)),
        })
    return records


def _fold_effect(
    train_frame: pd.DataFrame,
    score_frame: pd.DataFrame,
    train_features: Mapping[pd.Timestamp, Mapping[str, float]],
    score_features: Mapping[pd.Timestamp, Mapping[str, float]],
    train_anchors: Sequence[pd.Timestamp],
    score_anchors: Sequence[pd.Timestamp],
    train_target: np.ndarray,
    score_target: np.ndarray,
    contract_raw: Mapping[str, Any],
) -> tuple[float, np.ndarray, np.ndarray]:
    baseline = tuple(contract_raw["evaluator"]["baseline_features"])
    augmented = baseline + tuple(contract_raw["evaluator"]["augmented_features"])
    train_rows = _model_rows(train_frame, train_features, train_anchors)
    score_rows = _model_rows(score_frame, score_features, score_anchors)
    base_state = fit_logit(train_rows, train_target, baseline, split="discovery")
    aug_state = fit_logit(train_rows, train_target, augmented, split="discovery")
    base_probability = transform_logit(score_rows, base_state)
    aug_probability = transform_logit(score_rows, aug_state)
    dates = score_frame.loc[list(score_anchors), "et_date"].astype(str).to_numpy()
    effect = equal_date_effect(
        dates,
        (base_probability - score_target) ** 2,
        (aug_probability - score_target) ** 2,
    )
    return effect, base_probability, aug_probability


def _preprocessing_identity(
    context: DiscoveryContext,
    experiment_id: str,
    spec: Any,
    effective_hash: str,
) -> PreprocessingIdentity:
    binding = context.manifest["experiments"][experiment_id]
    return PreprocessingIdentity(
        experiment_id,
        spec.hypothesis_family,
        "discovery",
        binding["candidate_source_hash"],
        effective_hash,
        spec.hashes.evaluator_hash,
        context.manifest["data_hash"],
    )


def _publish_discovery(
    context: DiscoveryContext,
    store: StateStore,
    reservation: Any,
    identity: PreprocessingIdentity,
    state_hash: str,
    payload: Mapping[str, Any],
    *,
    base_spec_hash: str,
    effective_hash: str,
) -> Mapping[str, Any]:
    run_identity = RunIdentity(
        identity.experiment,
        identity.hypothesis_family,
        identity.split,
        identity.candidate_hash,
        state_hash,
        identity.evaluator_hash,
        identity.data_hash,
        identity.spec_hash,
    )
    bundle, payload_hash, envelope_hash = build_result_bundle(
        payload,
        identity=run_identity,
        base_spec_hash=base_spec_hash,
        effective_spec_hash=effective_hash,
        code_manifest_hash=digest_file(context.manifest_path),
    )
    destination = context.campaign / "results" / f"{identity.experiment}_discovery.json"
    try:
        receipt_hash = publish_result_bundle(
            store,
            reservation.run_id,
            destination,
            bundle,
            payload_hash=payload_hash,
            envelope_hash=envelope_hash,
        )
    except Exception as exc:
        run = store.get_run(reservation.run_id)
        if run["status"] == "RESERVED":
            store.reopen_preprocessing_after_run_failure(
                reservation.run_id, str(exc),
            )
        raise
    return {
        "experiment_id": identity.experiment,
        "result_path": str(destination),
        "result_hash": envelope_hash,
        "receipt_hash": receipt_hash,
        "status": payload["status"],
    }


def _fail_unsealed_job(store: StateStore, job_id: int, error: Exception) -> None:
    try:
        store.fail_preprocessing_no_result(job_id, str(error))
    except RuntimeError as exc:
        if "only reserved preprocessing" not in str(exc):
            raise


def _pooled_oof_effect(values: Mapping[str, Sequence[Any]]) -> float:
    dates = np.asarray(values["dates"]).astype(str)
    baseline = np.asarray(values["base"], dtype=float)
    augmented = np.asarray(values["aug"], dtype=float)
    target = np.asarray(values["target"], dtype=float)
    return equal_date_effect(
        dates, (baseline - target) ** 2, (augmented - target) ** 2,
    )


def _float_keyed_audit(values: Mapping[float, float]) -> dict[str, float]:
    return {
        format(float(key), ".15g"): float(value)
        for key, value in values.items()
    }


def _publish_no_selection(
    context: DiscoveryContext,
    spec: PredictiveExperimentSpecV2,
    effective_hash: str,
    store: StateStore,
    job: Any,
    identity: PreprocessingIdentity,
    audit: Mapping[str, Any],
) -> Mapping[str, Any]:
    frozen_state = {
        "schema_version": 1,
        "experiment_id": spec.experiment_id,
        "status": "no_stable_configuration",
        "effective_spec_hash": effective_hash,
    }
    state_hash = digest_object(frozen_state)
    reservation = store.seal_preprocessing(job.run_id, state_hash)
    payload = {
        "schema_version": 1,
        "status": "no_stable_configuration",
        "experiment_id": spec.experiment_id,
        "selection_audit": dict(audit),
        "frozen_state": frozen_state,
        "frozen_state_hash": state_hash,
        "outcomes_loaded": True,
        "validation_opened": False,
    }
    return _publish_discovery(
        context, store, reservation, identity, state_hash, payload,
        base_spec_hash=spec.hashes.spec_hash, effective_hash=effective_hash,
    )


def _fit_and_transform(
    context: DiscoveryContext,
    experiment_id: str,
    spec: Any,
    contract_raw: Mapping[str, Any],
    train: MatrixSlice,
    score: MatrixSlice,
    config: Mapping[str, Any],
    grid: Mapping[str, Sequence[Any]],
    timeout_seconds: float,
) -> tuple[list[Any], list[Any], Any]:
    fitted, state, _ = _candidate_call(
        context, experiment_id, spec, contract_raw, train.candidate_rows,
        config, grid, timeout_seconds=timeout_seconds,
    )
    scored, unchanged, _ = _candidate_call(
        context, experiment_id, spec, contract_raw, score.candidate_rows,
        config, grid, frozen_state=state, operation="transform",
        timeout_seconds=timeout_seconds,
    )
    if digest_object(unchanged) != digest_object(state):
        raise ValueError("candidate state changed during discovery scoring")
    return fitted, scored, state


def _subset(loaded: MatrixSlice, dates: Sequence[str]) -> MatrixSlice:
    selected = tuple(dates)
    mask = loaded.evaluator_frame["et_date"].astype(str).isin(selected)
    frame = loaded.evaluator_frame.loc[mask]
    rows = (
        loaded.candidate_rows.select(frame.index)
        if isinstance(loaded.candidate_rows, CandidateRows)
        else tuple(
            row for row in loaded.candidate_rows
            if pd.Timestamp(row["formation_ts"]) in set(frame.index)
        )
    )
    return MatrixSlice(loaded.split, selected, rows, frame)


def _embargo_before_score(
    train: MatrixSlice, score: MatrixSlice, minutes: int,
) -> tuple[MatrixSlice, int]:
    cutoff = score.evaluator_frame.index.min() - pd.Timedelta(minutes=minutes)
    keep = train.evaluator_frame.index < cutoff
    removed = int((~keep).sum())
    if not removed:
        return train, 0
    frame = train.evaluator_frame.loc[keep]
    rows = (
        train.candidate_rows.select(frame.index)
        if isinstance(train.candidate_rows, CandidateRows)
        else tuple(
            row for row in train.candidate_rows
            if pd.Timestamp(row["formation_ts"]) in set(frame.index)
        )
    )
    return MatrixSlice(train.split, train.dates, rows, frame), removed


def _e002_lambda_mse(
    context: DiscoveryContext,
    spec: PredictiveExperimentSpecV2,
    contract_raw: Mapping[str, Any],
    loaded: MatrixSlice,
    *,
    timeout_seconds: float,
) -> dict[float, float]:
    months = sorted({day[:7] for day in loaded.dates})
    if len(months) < 3:
        raise ValueError("E002 inner selection needs at least three calendar months")
    lambdas = tuple(float(value) for value in spec.design.grids["ridge_lambda"])
    scores = {value: [] for value in lambdas}
    for index in range(2, len(months)):
        train_dates = tuple(day for day in loaded.dates if day[:7] in months[:index])
        score_dates = tuple(day for day in loaded.dates if day[:7] == months[index])
        train = _subset(loaded, train_dates)
        score = _subset(loaded, score_dates)
        for ridge in lambdas:
            config = {
                "ridge_lambda": ridge,
                "formation_lookback_minutes": 5,
                "entry_gap_ticks": 2,
            }
            fitted, parsed, state = _fit_and_transform(
                context, "E002", spec, contract_raw, train, score,
                config, spec.design.grids, timeout_seconds,
            )
            gaps = [
                float(row.features["formation_gap"]) for row in parsed
            ]
            if not gaps:
                raise ValueError("E002 inner score month has no candidate rows")
            scores[ridge].append(float(np.mean(np.square(gaps))))
            print(json.dumps({
                "experiment_id": "E002", "inner_score_month": months[index],
                "ridge_lambda": ridge, "mse": scores[ridge][-1],
            }, sort_keys=True), flush=True)
            del fitted, parsed, state, gaps
            gc.collect()
    return {ridge: float(np.mean(values)) for ridge, values in scores.items()}


def _run_e001(
    context: DiscoveryContext,
    spec: PredictiveExperimentSpecV2,
    contract_raw: Mapping[str, Any],
    effective_hash: str,
    store: StateStore,
    *,
    timeout_seconds: float,
) -> Mapping[str, Any]:
    controller = ExecutionController(actual_run=True)
    identity = _preprocessing_identity(context, "E001", spec, effective_hash)
    job = store.reserve_preprocessing(identity)
    grid = spec.design.grids
    configs = declared_grid(grid)
    config_map = {
        json.dumps(item, sort_keys=True, separators=(",", ":")): item
        for item in configs
    }
    fold_effects = {key: [] for key in config_map}
    oof = {key: {"dates": [], "base": [], "aug": [], "target": []} for key in config_map}
    fold_audit: list[dict[str, Any]] = []
    all_dates = context.adapter.assigned_dates("discovery")
    try:
        for fold_index, fold in enumerate(contract_raw["selection"]["outer_folds"], 1):
            train_dates = tuple(day for day in all_dates if day <= fold["train_end"])
            score_dates = tuple(
                day for day in all_dates
                if fold["score_start"] <= day <= fold["score_end"]
            )
            train = context.adapter.load(
                "discovery", spec.candidate.allowed_inputs, dates=train_dates,
                evaluator_inputs=BASELINE_RAW,
            )
            score = context.adapter.load(
                "discovery", spec.candidate.allowed_inputs, dates=score_dates,
                evaluator_inputs=BASELINE_RAW,
            )
            train, embargoed = _embargo_before_score(
                train, score, int(contract_raw["selection"]["embargo_minutes"]),
            )
            reference_train: set[pd.Timestamp] | None = None
            reference_score: set[pd.Timestamp] | None = None
            train_anchors: tuple[pd.Timestamp, ...] | None = None
            score_anchors: tuple[pd.Timestamp, ...] | None = None
            train_target: np.ndarray | None = None
            score_target: np.ndarray | None = None
            for key, config in config_map.items():
                fitted, scored, _ = _fit_and_transform(
                    context, "E001", spec, contract_raw, train, score,
                    config, grid, timeout_seconds,
                )
                train_features = _features_by_timestamp(fitted)
                score_features = _features_by_timestamp(scored)
                del fitted, scored
                if reference_train is None:
                    reference_train, reference_score = set(train_features), set(score_features)
                    train_anchors = _common_anchor_timestamps(
                        train.evaluator_frame, [train_features], experiment_id="E001",
                    )
                    score_anchors = _common_anchor_timestamps(
                        score.evaluator_frame, [score_features], experiment_id="E001",
                    )
                    train_target = controller.construct_outcomes(
                        "E001", _outcome_records(
                            train.evaluator_frame, train_anchors, train_features,
                        ),
                    )
                    score_target = controller.construct_outcomes(
                        "E001", _outcome_records(
                            score.evaluator_frame, score_anchors, score_features,
                        ),
                    )
                elif set(train_features) != reference_train or set(score_features) != reference_score:
                    raise ValueError("E001 configurations do not share candidate timestamps")
                assert train_anchors is not None and score_anchors is not None
                assert train_target is not None and score_target is not None
                effect, base, aug = _fold_effect(
                    train.evaluator_frame, score.evaluator_frame,
                    train_features, score_features, train_anchors, score_anchors,
                    train_target, score_target, contract_raw,
                )
                fold_effects[key].append(effect)
                dates = score.evaluator_frame.loc[list(score_anchors), "et_date"].astype(str)
                oof[key]["dates"].extend(dates.tolist())
                oof[key]["base"].extend(base.tolist())
                oof[key]["aug"].extend(aug.tolist())
                oof[key]["target"].extend(score_target.tolist())
                print(json.dumps({
                    "experiment_id": "E001", "fold": fold_index,
                    "config": config, "effect": effect,
                }, sort_keys=True), flush=True)
                del train_features, score_features, base, aug, dates
                gc.collect()
            fold_audit.append({
                "fold": fold_index, "train_dates": len(train_dates),
                "score_dates": len(score_dates), "train_anchors": len(train_anchors or ()),
                "score_anchors": len(score_anchors or ()), "embargoed_rows": embargoed,
            })
            del (
                train, score, reference_train, reference_score,
                train_anchors, score_anchors, train_target, score_target,
            )
            gc.collect()
        try:
            selection = controller.select_discovery(
                "E001", fold_effects=fold_effects, configs=config_map,
                pooled_effects={
                    key: _pooled_oof_effect(values) for key, values in oof.items()
                },
            )
        except ValueError as exc:
            if "no E001 configuration passed" not in str(exc):
                raise
            return _publish_no_selection(
                context, spec, effective_hash, store, job, identity,
                {"fold_effects": fold_effects, "folds": fold_audit},
            )
        return _finalize_predictive(
            context, spec, contract_raw, effective_hash, store, job, identity,
            selection, fold_audit, oof, timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        _fail_unsealed_job(store, job.run_id, exc)
        raise


def _run_e002(
    context: DiscoveryContext,
    spec: PredictiveExperimentSpecV2,
    contract_raw: Mapping[str, Any],
    effective_hash: str,
    store: StateStore,
    *,
    timeout_seconds: float,
) -> Mapping[str, Any]:
    controller = ExecutionController(actual_run=True)
    identity = _preprocessing_identity(context, "E002", spec, effective_hash)
    job = store.reserve_preprocessing(identity)
    all_dates = context.adapter.assigned_dates("discovery")
    outer_effects = {5: [], 15: [], 30: []}
    inner_mse: dict[str, dict[float, float]] = {}
    oof = {str(value): {"dates": [], "base": [], "aug": [], "target": []}
           for value in outer_effects}
    fold_audit: list[dict[str, Any]] = []
    try:
        for fold_index, fold in enumerate(contract_raw["selection"]["outer_folds"], 1):
            train_dates = tuple(day for day in all_dates if day <= fold["train_end"])
            score_dates = tuple(
                day for day in all_dates
                if fold["score_start"] <= day <= fold["score_end"]
            )
            train = context.adapter.load(
                "discovery", spec.candidate.allowed_inputs, dates=train_dates,
                evaluator_inputs=BASELINE_RAW,
            )
            score = context.adapter.load(
                "discovery", spec.candidate.allowed_inputs, dates=score_dates,
                evaluator_inputs=BASELINE_RAW,
            )
            train, embargoed = _embargo_before_score(
                train, score, int(contract_raw["selection"]["embargo_minutes"]),
            )
            fold_scores = _e002_lambda_mse(
                context, spec, contract_raw, train, timeout_seconds=timeout_seconds,
            )
            inner_mse[f"fold{fold_index}"] = fold_scores
            ridge = min(fold_scores, key=lambda value: (fold_scores[value], value))
            train_maps: dict[int, dict[pd.Timestamp, dict[str, float]]] = {}
            score_maps: dict[int, dict[pd.Timestamp, dict[str, float]]] = {}
            for lookback in outer_effects:
                config = {
                    "ridge_lambda": ridge,
                    "formation_lookback_minutes": lookback,
                    "entry_gap_ticks": 2,
                }
                fitted, scored, _ = _fit_and_transform(
                    context, "E002", spec, contract_raw, train, score,
                    config, spec.design.grids, timeout_seconds,
                )
                train_maps[lookback] = FeatureFrame(fitted)
                score_maps[lookback] = FeatureFrame(scored)
                del fitted, scored
                gc.collect()
            train_anchors = _common_anchor_timestamps(
                train.evaluator_frame, list(train_maps.values()), experiment_id="E002",
            )
            score_anchors = _common_anchor_timestamps(
                score.evaluator_frame, list(score_maps.values()), experiment_id="E002",
            )
            for timestamp in (*train_anchors, *score_anchors):
                maps = train_maps if timestamp in train_maps[5] else score_maps
                gaps = [maps[value][timestamp]["formation_gap"] for value in outer_effects]
                if max(gaps) - min(gaps) > 1e-12:
                    raise ValueError("E002 lookbacks changed the frozen formation gap")
            train_target = controller.construct_outcomes(
                "E002", _outcome_records(
                    train.evaluator_frame, train_anchors, train_maps[5],
                ),
            )
            score_target = controller.construct_outcomes(
                "E002", _outcome_records(
                    score.evaluator_frame, score_anchors, score_maps[5],
                ),
            )
            for lookback in outer_effects:
                effect, base, aug = _fold_effect(
                    train.evaluator_frame, score.evaluator_frame,
                    train_maps[lookback], score_maps[lookback],
                    train_anchors, score_anchors, train_target, score_target,
                    contract_raw,
                )
                outer_effects[lookback].append(effect)
                dates = score.evaluator_frame.loc[list(score_anchors), "et_date"].astype(str)
                selected_oof = oof[str(lookback)]
                selected_oof["dates"].extend(dates.tolist())
                selected_oof["base"].extend(base.tolist())
                selected_oof["aug"].extend(aug.tolist())
                selected_oof["target"].extend(score_target.tolist())
                print(json.dumps({
                    "experiment_id": "E002", "fold": fold_index,
                    "lookback": lookback, "ridge_lambda": ridge,
                    "effect": effect,
                }, sort_keys=True), flush=True)
                del base, aug, dates
                gc.collect()
            fold_audit.append({
                "fold": fold_index, "selected_ridge_lambda": ridge,
                "inner_mse": _float_keyed_audit(fold_scores),
                "train_dates": len(train_dates),
                "score_dates": len(score_dates), "train_anchors": len(train_anchors),
                "score_anchors": len(score_anchors), "embargoed_rows": embargoed,
            })
            del train, score, train_maps, score_maps, train_target, score_target
            gc.collect()
        full = context.adapter.load(
            "discovery", spec.candidate.allowed_inputs, dates=all_dates,
            evaluator_inputs=BASELINE_RAW,
        )
        final_mse = _e002_lambda_mse(
            context, spec, contract_raw, full, timeout_seconds=timeout_seconds,
        )
        try:
            selection = controller.select_discovery(
                "E002", fold_effects=outer_effects,
                inner_mse=inner_mse, final_mse=final_mse,
                pooled_effects={
                    lookback: _pooled_oof_effect(oof[str(lookback)])
                    for lookback in outer_effects
                },
            )
        except ValueError as exc:
            if "no E002 lookback passed" not in str(exc):
                raise
            return _publish_no_selection(
                context, spec, effective_hash, store, job, identity,
                {
                    "outer_effects": {
                        str(key): list(values)
                        for key, values in outer_effects.items()
                    },
                    "inner_mse": {
                        fold: _float_keyed_audit(scores)
                        for fold, scores in inner_mse.items()
                    },
                    "final_mse": _float_keyed_audit(final_mse),
                    "folds": fold_audit,
                },
            )
        return _finalize_predictive(
            context, spec, contract_raw, effective_hash, store, job, identity,
            selection, fold_audit, oof, timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        _fail_unsealed_job(store, job.run_id, exc)
        raise


def _run_d001(
    context: DiscoveryContext,
    spec: DescriptiveSpecV2,
    contract_raw: Mapping[str, Any],
    effective_hash: str,
    store: StateStore,
    *,
    timeout_seconds: float,
) -> Mapping[str, Any]:
    identity = _preprocessing_identity(context, "D001", spec, effective_hash)
    job = store.reserve_preprocessing(identity)
    try:
        loaded = context.adapter.load(
            "discovery", spec.candidate.allowed_inputs,
            dates=context.adapter.assigned_dates("discovery"),
        )
        config = {"map": "DECLARED_243"}
        grid = {"map": ["DECLARED_243"]}
        parsed, candidate_state, state_hash = _candidate_call(
            context, "D001", spec, contract_raw, loaded.candidate_rows,
            config, grid, timeout_seconds=timeout_seconds,
        )
        features = _features_by_timestamp(parsed)
        records: list[dict[str, Any]] = []
        for horizon in (15, 30, 60):
            anchors = _common_anchor_timestamps(
                loaded.evaluator_frame, [features], experiment_id="D001",
                horizon_minutes=horizon,
            )
            for timestamp in anchors:
                stop = timestamp + pd.Timedelta(minutes=horizon)
                path = loaded.evaluator_frame.loc[
                    (loaded.evaluator_frame.index > timestamp)
                    & (loaded.evaluator_frame.index <= stop),
                    "price",
                ]
                raw = loaded.evaluator_frame.loc[timestamp]
                records.append({
                    "date": str(raw["et_date"]),
                    "price": float(raw["price"]),
                    "session": str(raw["session"]),
                    "event_blackout": bool(raw["event_blackout"]),
                    "horizon": horizon,
                    "path": path.to_numpy(dtype=float).tolist(),
                    **features[timestamp],
                })
        result = evaluate_d001_map(
            records,
            candidate_state["candidate_state"]["boundaries"],
            first_half_end=contract_raw["selection"]["first_half_end"],
        )
        reservation = store.seal_preprocessing(job.run_id, state_hash)
        payload = {
            "schema_version": 1,
            "status": "descriptive_complete",
            "experiment_id": "D001",
            "map": result,
            "frozen_state": candidate_state,
            "frozen_state_hash": state_hash,
            "record_count": len(records),
            "outcomes_loaded": True,
            "validation_opened": False,
        }
        return _publish_discovery(
            context, store, reservation, identity, state_hash, payload,
            base_spec_hash=spec.hashes.spec_hash, effective_hash=effective_hash,
        )
    except Exception as exc:
        _fail_unsealed_job(store, job.run_id, exc)
        raise


def run_discovery(
    repo_root: str | Path,
    manifest_path: str | Path,
    experiment_id: str,
    *,
    timeout_seconds: float = 600.0,
) -> Mapping[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    context = authorized_context(repo_root, manifest_path)
    allowed = tuple(context.manifest.get("authorized_experiments", ()))
    if experiment_id not in allowed:
        raise PermissionError(f"manifest does not authorize {experiment_id}")
    spec, _, contract_raw, effective_hash = _load_methodology(
        context, experiment_id,
    )
    store = StateStore(
        context.campaign / "state" / "campaign.sqlite",
        output_roots=[context.campaign],
    )
    if experiment_id == "E001":
        return _run_e001(
            context, spec, contract_raw, effective_hash, store,
            timeout_seconds=timeout_seconds,
        )
    if experiment_id == "E002":
        return _run_e002(
            context, spec, contract_raw, effective_hash, store,
            timeout_seconds=timeout_seconds,
        )
    return _run_d001(
        context, spec, contract_raw, effective_hash, store,
        timeout_seconds=timeout_seconds,
    )


def _finalize_predictive(
    context: DiscoveryContext,
    spec: PredictiveExperimentSpecV2,
    contract_raw: Mapping[str, Any],
    effective_hash: str,
    store: StateStore,
    job: Any,
    identity: PreprocessingIdentity,
    selection: SelectionResult,
    fold_audit: Sequence[Mapping[str, Any]],
    oof: Mapping[str, Mapping[str, Sequence[Any]]],
    *,
    timeout_seconds: float,
) -> Mapping[str, Any]:
    experiment_id = spec.experiment_id
    all_dates = context.adapter.assigned_dates("discovery")
    loaded = context.adapter.load(
        "discovery", spec.candidate.allowed_inputs, dates=all_dates,
        evaluator_inputs=BASELINE_RAW,
    )
    parsed, candidate_state, _ = _candidate_call(
        context, experiment_id, spec, contract_raw, loaded.candidate_rows,
        selection.config, spec.design.grids, timeout_seconds=timeout_seconds,
    )
    features = (
        FeatureFrame(parsed)
        if experiment_id == "E002" else _features_by_timestamp(parsed)
    )
    del parsed
    gc.collect()
    anchors = _common_anchor_timestamps(
        loaded.evaluator_frame, [features], experiment_id=experiment_id,
    )
    controller = ExecutionController(actual_run=True)
    target = controller.construct_outcomes(
        experiment_id,
        _outcome_records(loaded.evaluator_frame, anchors, features),
    )
    rows = _model_rows(loaded.evaluator_frame, features, anchors)
    baseline_names = tuple(contract_raw["evaluator"]["baseline_features"])
    augmented_names = baseline_names + tuple(
        contract_raw["evaluator"]["augmented_features"]
    )
    baseline_state = fit_logit(rows, target, baseline_names, split="discovery")
    augmented_state = fit_logit(rows, target, augmented_names, split="discovery")
    selected_key = (
        json.dumps(selection.config, sort_keys=True, separators=(",", ":"))
        if experiment_id == "E001" else str(selection.config["formation_lookback_minutes"])
    )
    selected_oof = oof[selected_key]
    dates = np.asarray(selected_oof["dates"]).astype(str)
    baseline = np.asarray(selected_oof["base"], dtype=float)
    augmented = np.asarray(selected_oof["aug"], dtype=float)
    outcomes = np.asarray(selected_oof["target"], dtype=float)
    loss_difference = (baseline - outcomes) ** 2 - (augmented - outcomes) ** 2
    per_date = np.asarray([
        np.mean(loss_difference[dates == day]) for day in np.unique(dates)
    ])
    if len(per_date) < 2:
        raise ValueError("selected discovery configuration has fewer than two OOF dates")
    sigma = float(per_date.std(ddof=1))
    denominator = len(spec.design.cells)
    alpha = float(spec.analysis.inference["alpha"])
    minimum_dates = int(spec.analysis.power["minimum_dates"])
    available_validation_dates = len(context.adapter.assigned_dates("validation"))
    required_dates = (
        minimum_dates if sigma == 0 else max(
            minimum_dates,
            power_date_requirement(
                float(spec.analysis.power["absolute_sesoi_brier"]),
                sigma,
                alpha=alpha / denominator,
                power=float(spec.analysis.power["target_power"]),
            ),
        )
    )
    utility_threshold = float(np.quantile(augmented - baseline, 0.8))
    frozen_state = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "effective_spec_hash": effective_hash,
        "config": dict(selection.config),
        "candidate_state": candidate_state,
        "baseline_model": json.loads(json.dumps(asdict(baseline_state), allow_nan=False)),
        "augmented_model": json.loads(json.dumps(asdict(augmented_state), allow_nan=False)),
        "utility_threshold": utility_threshold,
    }
    state_hash = digest_object(frozen_state)
    reservation = store.seal_preprocessing(job.run_id, state_hash)
    status = (
        "selected" if required_dates <= available_validation_dates
        else "blocked_underpowered"
    )
    payload = {
        "schema_version": 1,
        "status": status,
        "experiment_id": experiment_id,
        "selection": asdict(selection),
        "fold_audit": list(fold_audit),
        "power": {
            "discovery_oof_dates": len(np.unique(dates)),
            "per_date_sigma": sigma,
            "required_validation_dates": required_dates,
            "available_validation_dates": available_validation_dates,
            "passes": required_dates <= available_validation_dates,
        },
        "frozen_state": frozen_state,
        "frozen_state_hash": state_hash,
        "discovery_anchor_count": len(anchors),
        "outcomes_loaded": True,
        "validation_opened": False,
    }
    return _publish_discovery(
        context, store, reservation, identity, state_hash, payload,
        base_spec_hash=spec.hashes.spec_hash, effective_hash=effective_hash,
    )
