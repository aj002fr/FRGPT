"""Versioned, strict schemas used by the Phase 1 research harness."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

SCHEMA_VERSION = 1
METHODOLOGY_SCHEMA_VERSION = 2
EXECUTION_CONTRACT_SCHEMA_VERSION = 1
MAX_YAML_BYTES = 1_048_576
HYPOTHESIS_STATUSES = {"exploratory", "proposed", "blocked", "superseded"}
EXPERIMENT_STATUSES = {"draft", "frozen", "closed"}
RUN_STATUSES = {
    "RESERVED", "COMPLETED", "FAILED_NO_RESULT", "CLOSED_INVALID",
}
DECISIONS = {
    "screen_pass", "screen_pass_limited", "not_confirmed", "inconclusive",
    "invalid", "blocked", "confirmed", "confirmed_limited",
    "blocked_underpowered", "positive_but_trivial", "non_actionable",
}
_ID = re.compile(r"^[A-Z][A-Z0-9_]{2,31}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_HYPOTHESIS_TRANSITIONS = {
    "exploratory": {"proposed", "blocked", "superseded"},
    "proposed": {"blocked", "superseded"},
    "blocked": {"proposed", "superseded"},
    "superseded": set(),
}
_EXPERIMENT_TRANSITIONS = {
    "draft": {"frozen", "closed"},
    "frozen": {"closed"},
    "closed": set(),
}


class SchemaError(ValueError):
    """Raised when untrusted protocol data violates its declared schema."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise SchemaError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping,
)


def _strict(data: Mapping[str, Any], allowed: set[str], required: set[str]) -> None:
    unknown = set(data) - allowed
    missing = required - set(data)
    if unknown or missing:
        raise SchemaError(f"unknown={sorted(unknown)} missing={sorted(missing)}")


def require_id(value: str, label: str = "id") -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise SchemaError(f"invalid {label}: {value!r}")
    return value


def require_hash(value: str, label: str = "hash") -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise SchemaError(f"invalid {label}")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{label} must be non-empty")
    return value


def _string_tuple(value: Any, label: str, *, nonempty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SchemaError(f"{label} must be a list")
    result = tuple(_nonempty(item, label) for item in value)
    if nonempty and not result:
        raise SchemaError(f"{label} cannot be empty")
    if len(result) != len(set(result)):
        raise SchemaError(f"{label} contains duplicates")
    return result


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise SchemaError(f"{label} must be finite")
    return result


def _json_value(value: Any, label: str) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)):
        return _finite_number(value, label)
    if isinstance(value, list):
        return [_json_value(item, label) for item in value]
    if isinstance(value, Mapping):
        return {
            _nonempty(key, label): _json_value(item, label)
            for key, item in value.items()
        }
    raise SchemaError(f"{label} must contain finite JSON values")


def require_utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"invalid UTC timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise SchemaError(f"timestamp must be UTC: {value!r}")
    return parsed.isoformat().replace("+00:00", "Z")


def validate_status_transition(kind: str, old: str, new: str) -> None:
    tables = {
        "hypothesis": _HYPOTHESIS_TRANSITIONS,
        "experiment": _EXPERIMENT_TRANSITIONS,
    }
    if kind not in tables or old not in tables[kind] or new not in tables[kind][old]:
        raise SchemaError(f"invalid {kind} status transition: {old} -> {new}")


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    family: str
    title: str
    status: str
    statement: str
    mechanism: str
    observable_proxy: str
    primary_claim: str
    required_data: tuple[str, ...]
    confounders: tuple[str, ...]
    prior_work: tuple[str, ...]
    notes: str = ""
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Hypothesis":
        fields = set(cls.__dataclass_fields__)
        required = fields - {"notes", "schema_version"}
        _strict(data, fields, required)
        if data.get("schema_version", 1) != SCHEMA_VERSION:
            raise SchemaError("unsupported hypothesis schema version")
        require_id(data["hypothesis_id"], "hypothesis_id")
        require_id(data["family"], "family")
        if data["status"] not in HYPOTHESIS_STATUSES:
            raise SchemaError("invalid hypothesis status")
        for key in ("title", "statement", "mechanism", "observable_proxy", "primary_claim"):
            if not isinstance(data[key], str) or not data[key].strip():
                raise SchemaError(f"{key} must be non-empty")
        values = dict(data)
        for key in ("required_data", "confounders", "prior_work"):
            raw = values[key]
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                raise SchemaError(f"{key} must be a list")
            values[key] = tuple(str(item) for item in raw)
        return cls(**values)


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    hypothesis_id: str
    hypothesis_family: str
    status: str
    primary_outcome: str
    primary_horizon_minutes: int
    primary_feature: str
    primary_metric: str
    sesoi: float
    alpha: float
    multiplicity_family: str
    sampling_policy: str
    baseline_features: tuple[str, ...]
    registered_cells: tuple[str, ...]
    registered_directions: tuple[str, ...] = ()
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExperimentSpec":
        fields = set(cls.__dataclass_fields__)
        required = fields - {"registered_directions", "schema_version"}
        _strict(data, fields, required)
        if data.get("schema_version", 1) != SCHEMA_VERSION:
            raise SchemaError("unsupported experiment schema version")
        require_id(data["experiment_id"], "experiment_id")
        require_id(data["hypothesis_id"], "hypothesis_id")
        require_id(data["hypothesis_family"], "hypothesis_family")
        require_id(data["multiplicity_family"], "multiplicity_family")
        if data["status"] not in EXPERIMENT_STATUSES:
            raise SchemaError("invalid experiment status")
        if data["primary_metric"] not in {"brier", "log_loss"}:
            raise SchemaError("primary metric must be a proper scoring rule")
        if data["sampling_policy"] not in {"non_overlapping", "equal_date_all_minutes"}:
            raise SchemaError("invalid sampling policy")
        if not isinstance(data["primary_horizon_minutes"], int) or data["primary_horizon_minutes"] <= 0:
            raise SchemaError("primary horizon must be a positive integer")
        if not isinstance(data["sesoi"], (int, float)) or data["sesoi"] <= 0:
            raise SchemaError("SESOI must be positive")
        if not isinstance(data["alpha"], (int, float)) or not 0 < data["alpha"] <= 0.1:
            raise SchemaError("alpha must be in (0, 0.1]")
        values = dict(data)
        for key in ("baseline_features", "registered_cells", "registered_directions"):
            raw = values.get(key, ())
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                raise SchemaError(f"{key} must be a list")
            values[key] = tuple(str(item) for item in raw)
        if not values["baseline_features"] or not values["registered_cells"]:
            raise SchemaError("baseline features and registered cells cannot be empty")
        return cls(**values)


@dataclass(frozen=True)
class MethodologyHashes:
    data_hash: str
    split_hash: str
    exposure_hash: str
    spec_hash: str
    evaluator_hash: str
    candidate_code_hash: str
    state_code_hash: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MethodologyHashes":
        fields = set(cls.__dataclass_fields__)
        _strict(data, fields, fields)
        for name in fields:
            require_hash(data[name], name)
        return cls(**data)


@dataclass(frozen=True)
class CandidateContract:
    allowed_inputs: tuple[str, ...]
    registered_feature_map: Mapping[str, str]
    numeric_direction: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CandidateContract":
        fields = set(cls.__dataclass_fields__)
        _strict(data, fields, {"allowed_inputs", "registered_feature_map"})
        allowed = _string_tuple(data["allowed_inputs"], "allowed_inputs")
        if "formation_ts" not in allowed:
            raise SchemaError("allowed_inputs must include formation_ts")
        raw_map = data["registered_feature_map"]
        if not isinstance(raw_map, Mapping) or not raw_map:
            raise SchemaError("registered_feature_map must be a non-empty mapping")
        feature_map: dict[str, str] = {}
        for name, description in raw_map.items():
            feature_map[_nonempty(name, "feature name")] = _nonempty(
                description, f"feature description {name}",
            )
        direction = data.get("numeric_direction")
        if direction is not None:
            _nonempty(direction, "numeric_direction")
        return cls(allowed, feature_map, direction)


@dataclass(frozen=True)
class EvaluatorDesign:
    grids: Mapping[str, tuple[Any, ...]]
    selection_rule: str
    cells: Mapping[str, str]
    cells_may_overlap: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvaluatorDesign":
        fields = set(cls.__dataclass_fields__)
        _strict(data, fields, {"grids", "selection_rule", "cells"})
        raw_grids = data["grids"]
        if not isinstance(raw_grids, Mapping) or not raw_grids:
            raise SchemaError("grids must be a non-empty mapping")
        grids: dict[str, tuple[Any, ...]] = {}
        for name, values in raw_grids.items():
            key = _nonempty(name, "grid name")
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise SchemaError(f"grid {key} must be a finite list")
            parsed = tuple(_json_value(item, f"grid {key}") for item in values)
            if not parsed:
                raise SchemaError(f"grid {key} cannot be empty")
            grids[key] = parsed
        raw_cells = data["cells"]
        if not isinstance(raw_cells, Mapping) or not raw_cells:
            raise SchemaError("cells must be a non-empty mapping")
        cells = {
            _nonempty(name, "cell name"): _nonempty(mask, f"cell mask {name}")
            for name, mask in raw_cells.items()
        }
        if data.get("cells_may_overlap", True) is not True:
            raise SchemaError("registered evaluator cells must permit overlap")
        return cls(
            grids, _nonempty(data["selection_rule"], "selection_rule"), cells, True,
        )


@dataclass(frozen=True)
class OutcomeContract:
    name: str
    definition: str
    horizon_minutes: int
    censoring: str
    boundary_policy: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OutcomeContract":
        fields = set(cls.__dataclass_fields__)
        _strict(data, fields, fields)
        horizon = data["horizon_minutes"]
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
            raise SchemaError("horizon_minutes must be a positive integer")
        return cls(
            *(_nonempty(data[name], name) for name in ("name", "definition")),
            horizon,
            _nonempty(data["censoring"], "censoring"),
            _nonempty(data["boundary_policy"], "boundary_policy"),
        )


@dataclass(frozen=True)
class AnalysisContract:
    model: Mapping[str, Any]
    inference: Mapping[str, Any]
    power: Mapping[str, Any]
    utility: Mapping[str, Any]
    diagnostics: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AnalysisContract":
        fields = set(cls.__dataclass_fields__)
        _strict(data, fields, fields)
        sections: list[Mapping[str, Any]] = []
        for name in ("model", "inference", "power", "utility"):
            raw = data[name]
            if not isinstance(raw, Mapping) or not raw:
                raise SchemaError(f"{name} must be a non-empty mapping")
            sections.append(_json_value(raw, name))
        diagnostics = _string_tuple(data["diagnostics"], "diagnostics")
        return cls(*sections, diagnostics)


@dataclass(frozen=True)
class ConfirmationContract:
    eligible: bool
    policy: str
    prospective_only: bool = True
    approval_required: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConfirmationContract":
        fields = set(cls.__dataclass_fields__)
        _strict(data, fields, {"eligible", "policy"})
        for name in ("eligible", "prospective_only", "approval_required"):
            if name in data and not isinstance(data[name], bool):
                raise SchemaError(f"{name} must be boolean")
        if not data.get("prospective_only", True) or not data.get("approval_required", True):
            raise SchemaError("confirmation must be prospective and approval-bound")
        return cls(
            data["eligible"], _nonempty(data["policy"], "policy"), True, True,
        )


@dataclass(frozen=True)
class PredictiveExperimentSpecV2:
    experiment_id: str
    hypothesis_id: str
    hypothesis_family: str
    status: str
    hashes: MethodologyHashes
    candidate: CandidateContract
    design: EvaluatorDesign
    outcome: OutcomeContract
    analysis: AnalysisContract
    attempt_cap: int
    confirmation: ConfirmationContract
    schema_version: int = METHODOLOGY_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PredictiveExperimentSpecV2":
        fields = set(cls.__dataclass_fields__)
        _strict(data, fields, fields - {"schema_version"})
        if data.get("schema_version", METHODOLOGY_SCHEMA_VERSION) != METHODOLOGY_SCHEMA_VERSION:
            raise SchemaError("unsupported predictive methodology schema version")
        for name in ("experiment_id", "hypothesis_id", "hypothesis_family"):
            require_id(data[name], name)
        if data["status"] not in EXPERIMENT_STATUSES:
            raise SchemaError("invalid experiment status")
        if data["attempt_cap"] != 5:
            raise SchemaError("predictive attempt_cap must equal five")
        return cls(
            data["experiment_id"], data["hypothesis_id"], data["hypothesis_family"],
            data["status"], MethodologyHashes.from_dict(data["hashes"]),
            CandidateContract.from_dict(data["candidate"]),
            EvaluatorDesign.from_dict(data["design"]),
            OutcomeContract.from_dict(data["outcome"]),
            AnalysisContract.from_dict(data["analysis"]), 5,
            ConfirmationContract.from_dict(data["confirmation"]),
            data.get("schema_version", METHODOLOGY_SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class DescriptiveSpecV2:
    study_id: str
    hypothesis_id: str
    hypothesis_family: str
    status: str
    hashes: MethodologyHashes
    candidate: CandidateContract
    design: EvaluatorDesign
    outcome: OutcomeContract
    diagnostics: tuple[str, ...]
    nomination_rule: str
    nomination_cap: int
    attempt_cap: int
    confirmation: ConfirmationContract
    schema_version: int = METHODOLOGY_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DescriptiveSpecV2":
        fields = set(cls.__dataclass_fields__)
        _strict(data, fields, fields - {"schema_version"})
        if data.get("schema_version", METHODOLOGY_SCHEMA_VERSION) != METHODOLOGY_SCHEMA_VERSION:
            raise SchemaError("unsupported descriptive methodology schema version")
        for name in ("study_id", "hypothesis_id", "hypothesis_family"):
            require_id(data[name], name)
        if data["status"] not in EXPERIMENT_STATUSES:
            raise SchemaError("invalid descriptive status")
        if data["attempt_cap"] != 5 or data["nomination_cap"] not in {0, 1, 2}:
            raise SchemaError("descriptive caps must be attempt=5 and nomination<=2")
        confirmation = ConfirmationContract.from_dict(data["confirmation"])
        if confirmation.eligible:
            raise SchemaError("descriptive studies cannot be confirmation-eligible")
        return cls(
            data["study_id"], data["hypothesis_id"], data["hypothesis_family"],
            data["status"], MethodologyHashes.from_dict(data["hashes"]),
            CandidateContract.from_dict(data["candidate"]),
            EvaluatorDesign.from_dict(data["design"]),
            OutcomeContract.from_dict(data["outcome"]),
            _string_tuple(data["diagnostics"], "diagnostics"),
            _nonempty(data["nomination_rule"], "nomination_rule"),
            data["nomination_cap"], 5, confirmation,
            data.get("schema_version", METHODOLOGY_SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class ExecutionContractV1:
    experiment_id: str
    base_spec_hash: str
    kind: str
    candidate: Mapping[str, Any]
    evaluator: Mapping[str, Any]
    selection: Mapping[str, Any]
    outcome: Mapping[str, Any]
    schema_version: int = EXECUTION_CONTRACT_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionContractV1":
        fields = set(cls.__dataclass_fields__)
        _strict(data, fields, fields - {"schema_version"})
        if data.get("schema_version", 1) != EXECUTION_CONTRACT_SCHEMA_VERSION:
            raise SchemaError("unsupported execution-contract schema version")
        require_id(data["experiment_id"], "experiment_id")
        require_hash(data["base_spec_hash"], "base_spec_hash")
        if data["kind"] not in {"predictive", "descriptive"}:
            raise SchemaError("execution contract kind must be predictive or descriptive")
        sections: list[Mapping[str, Any]] = []
        for name in ("candidate", "evaluator", "selection", "outcome"):
            raw = data[name]
            if not isinstance(raw, Mapping) or not raw:
                raise SchemaError(f"execution contract {name} must be non-empty")
            sections.append(_json_value(raw, name))
        return cls(
            data["experiment_id"], data["base_spec_hash"], data["kind"],
            *sections, data.get("schema_version", 1),
        )


@dataclass(frozen=True)
class CandidateRow:
    formation_ts: str
    features: Mapping[str, float]
    direction: float | None = None

    @property
    def feature(self) -> float:
        if len(self.features) != 1:
            raise AttributeError("feature is ambiguous for a multi-feature candidate row")
        return next(iter(self.features.values()))

    @property
    def cell_id(self) -> str:
        if len(self.features) != 1:
            raise AttributeError("legacy cell_id is unavailable for multi-feature rows")
        return next(iter(self.features))

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        registered_features: set[str],
    ) -> "CandidateRow":
        if "cell_id" in data or "feature" in data:
            _strict(
                data, {"formation_ts", "cell_id", "feature", "direction"},
                {"formation_ts", "cell_id", "feature"},
            )
            cell = str(data["cell_id"])
            if cell not in registered_features:
                raise SchemaError(f"unregistered legacy feature: {cell}")
            direction = data.get("direction")
            if direction is not None and not isinstance(direction, (int, float)):
                raise SchemaError("legacy direction must be numeric")
            return cls(
                require_utc(data["formation_ts"]),
                {cell: _finite_number(data["feature"], f"feature {cell}")},
                None if direction is None else _finite_number(direction, "direction"),
            )
        _strict(data, {"formation_ts", "features", "direction"},
                {"formation_ts", "features"})
        ts = require_utc(data["formation_ts"])
        raw_features = data["features"]
        if not isinstance(raw_features, Mapping):
            raise SchemaError("features must be a mapping")
        if set(raw_features) != registered_features:
            raise SchemaError("candidate feature map differs from registration")
        features = {
            str(name): _finite_number(value, f"feature {name}")
            for name, value in raw_features.items()
        }
        direction = data.get("direction")
        if direction is not None:
            direction = _finite_number(direction, "direction")
            if direction == 0:
                raise SchemaError("numeric direction cannot be zero")
        return cls(ts, features, direction)


def validate_candidate_rows(
    rows: Sequence[Mapping[str, Any]],
    registered_features: set[str],
) -> list[CandidateRow]:
    if not isinstance(rows, list):
        raise SchemaError("candidate output must be a JSON list")
    parsed = [
        CandidateRow.from_dict(row, registered_features)
        for row in rows
    ]
    timestamps = [row.formation_ts for row in parsed]
    if len(timestamps) != len(set(timestamps)):
        raise SchemaError("duplicate candidate timestamp")
    return parsed


@dataclass(frozen=True)
class RunIdentity:
    experiment: str
    hypothesis_family: str
    split: str
    candidate_hash: str
    state_hash: str
    evaluator_hash: str
    data_hash: str
    spec_hash: str = "0" * 64

    def __post_init__(self) -> None:
        require_id(self.experiment, "experiment")
        require_id(self.hypothesis_family, "hypothesis_family")
        if self.split not in {"discovery", "validation", "quarantine", "confirmation"}:
            raise SchemaError("invalid split")
        for name in (
            "candidate_hash", "state_hash", "evaluator_hash", "data_hash", "spec_hash",
        ):
            require_hash(getattr(self, name), name)

    def values(self) -> tuple[str, ...]:
        return tuple(str(value) for value in asdict(self).values())


@dataclass(frozen=True)
class PreprocessingIdentity:
    experiment: str
    hypothesis_family: str
    split: str
    candidate_hash: str
    spec_hash: str
    evaluator_hash: str
    data_hash: str

    def __post_init__(self) -> None:
        require_id(self.experiment, "experiment")
        require_id(self.hypothesis_family, "hypothesis_family")
        if self.split not in {"discovery", "validation", "quarantine", "confirmation"}:
            raise SchemaError("invalid preprocessing split")
        for name in ("candidate_hash", "spec_hash", "evaluator_hash", "data_hash"):
            require_hash(getattr(self, name), name)

    def values(self) -> tuple[str, ...]:
        return tuple(str(value) for value in asdict(self).values())


@dataclass(frozen=True)
class Approval:
    hypothesis_family: str
    experiment: str
    candidate_hash: str
    state_hash: str
    evaluator_hash: str
    data_hash: str
    primary_claim_hash: str
    confirmation_cutoff: str
    date_manifest_hash: str
    spec_hash: str
    approver: str
    approved_at: str
    schema_version: int = field(default=1)

    def __post_init__(self) -> None:
        require_id(self.hypothesis_family, "hypothesis_family")
        require_id(self.experiment, "experiment")
        for name in (
            "candidate_hash", "state_hash", "evaluator_hash", "data_hash",
            "primary_claim_hash", "date_manifest_hash", "spec_hash",
        ):
            require_hash(getattr(self, name), name)
        require_utc(self.approved_at)
        datetime.fromisoformat(self.confirmation_cutoff)
        if not self.approver.strip() or self.schema_version != SCHEMA_VERSION:
            raise SchemaError("invalid approval")


@dataclass(frozen=True)
class Scorecard:
    experiment: str
    hypothesis_family: str
    split: str
    decision: str
    effect: float
    ci_low: float
    ci_high: float
    p_value: float
    n_dates: int
    run_identity_hash: str
    output_hash: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        require_id(self.experiment, "experiment")
        require_id(self.hypothesis_family, "hypothesis_family")
        if self.split not in {"validation", "confirmation"} or self.decision not in DECISIONS:
            raise SchemaError("invalid scorecard split or decision")
        values = (self.effect, self.ci_low, self.ci_high, self.p_value)
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
            raise SchemaError("scorecard statistics must be finite")
        if self.ci_low > self.ci_high or not 0 <= self.p_value <= 1 or self.n_dates < 1:
            raise SchemaError("invalid scorecard statistics")
        require_hash(self.run_identity_hash, "run_identity_hash")
        require_hash(self.output_hash, "output_hash")
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError("unsupported scorecard schema version")


@dataclass(frozen=True)
class ScorecardV2:
    experiment: str
    hypothesis_family: str
    split: str
    decision: str
    headline_cell: str
    run_identity_hash: str
    result_payload_hash: str
    base_spec_hash: str
    effective_spec_hash: str
    schema_version: int = METHODOLOGY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_id(self.experiment, "experiment")
        require_id(self.hypothesis_family, "hypothesis_family")
        if self.split not in {"validation", "confirmation"}:
            raise SchemaError("scorecard v2 is validation/confirmation only")
        if self.decision not in DECISIONS:
            raise SchemaError("invalid scorecard v2 decision")
        _nonempty(self.headline_cell, "headline_cell")
        for name in (
            "run_identity_hash", "result_payload_hash", "base_spec_hash",
            "effective_spec_hash",
        ):
            require_hash(getattr(self, name), name)
        if self.schema_version != METHODOLOGY_SCHEMA_VERSION:
            raise SchemaError("unsupported scorecard v2 schema version")


@dataclass(frozen=True)
class ResultBundleV1:
    result_payload: Mapping[str, Any]
    scorecard: Mapping[str, Any] | None
    base_spec_hash: str
    effective_spec_hash: str
    code_manifest_hash: str
    run_identity_hash: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        _json_value(self.result_payload, "result_payload")
        if self.scorecard is not None:
            _json_value(self.scorecard, "scorecard")
        for name in (
            "base_spec_hash", "effective_spec_hash", "code_manifest_hash",
            "run_identity_hash",
        ):
            require_hash(getattr(self, name), name)
        if self.schema_version != 1:
            raise SchemaError("unsupported result bundle schema version")


@dataclass(frozen=True)
class CellScorecard:
    cell_id: str
    selected_config: str
    decision: str
    effect: float
    ci_low: float
    ci_high: float
    p_raw: float
    p_adjusted: float
    n_dates: int
    n_positive: int
    n_negative: int
    calibration_intercept: float
    calibration_slope: float
    utility_lift: float
    stability: Mapping[str, Any]
    diagnostics: Mapping[str, Any]

    def __post_init__(self) -> None:
        _nonempty(self.cell_id, "cell_id")
        _nonempty(self.selected_config, "selected_config")
        if self.decision not in DECISIONS:
            raise SchemaError("invalid cell decision")
        numeric = (
            self.effect, self.ci_low, self.ci_high, self.p_raw, self.p_adjusted,
            self.calibration_intercept, self.calibration_slope, self.utility_lift,
        )
        if not all(math.isfinite(float(value)) for value in numeric):
            raise SchemaError("cell scorecard statistics must be finite")
        if self.ci_low > self.ci_high or not (
            0 <= self.p_raw <= 1 and 0 <= self.p_adjusted <= 1
        ):
            raise SchemaError("invalid cell confidence or p-values")
        if self.n_dates < 0 or self.n_positive < 0 or self.n_negative < 0:
            raise SchemaError("cell counts cannot be negative")
        _json_value(self.stability, "stability")
        _json_value(self.diagnostics, "diagnostics")


@dataclass(frozen=True)
class ExperimentResult:
    experiment: str
    hypothesis_family: str
    split: str
    selected_config: str
    decision: str
    cells: tuple[CellScorecard, ...]
    run_identity_hash: str
    output_hash: str
    receipt_hash: str | None = None
    evidentiary: bool = True
    schema_version: int = METHODOLOGY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_id(self.experiment, "experiment")
        require_id(self.hypothesis_family, "hypothesis_family")
        if self.split not in {"discovery", "validation", "quarantine", "confirmation"}:
            raise SchemaError("invalid result split")
        if self.split == "quarantine" and self.evidentiary:
            raise SchemaError("quarantine results must be explicitly non-evidentiary")
        if not isinstance(self.evidentiary, bool):
            raise SchemaError("evidentiary must be boolean")
        _nonempty(self.selected_config, "selected_config")
        if self.decision not in DECISIONS or not self.cells:
            raise SchemaError("invalid experiment result")
        require_hash(self.run_identity_hash, "run_identity_hash")
        require_hash(self.output_hash, "output_hash")
        if self.receipt_hash is not None:
            require_hash(self.receipt_hash, "receipt_hash")
            if self.receipt_hash == self.output_hash:
                raise SchemaError("receipt hash must not be self-referential")
        if self.schema_version != METHODOLOGY_SCHEMA_VERSION:
            raise SchemaError("unsupported result schema version")


@dataclass(frozen=True)
class DescriptiveResult:
    study_id: str
    hypothesis_family: str
    split: str
    declared_cell_count: int
    cells: tuple[Mapping[str, Any], ...]
    nominations: tuple[Mapping[str, Any], ...]
    diagnostics: Mapping[str, Any]
    output_hash: str
    receipt_hash: str | None = None
    schema_version: int = METHODOLOGY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_id(self.study_id, "study_id")
        require_id(self.hypothesis_family, "hypothesis_family")
        if self.split != "discovery" or self.declared_cell_count < 1:
            raise SchemaError("descriptive results are discovery-only")
        if len(self.cells) != self.declared_cell_count or len(self.nominations) > 2:
            raise SchemaError("descriptive result cell/nomination count mismatch")
        for value in (*self.cells, *self.nominations, self.diagnostics):
            _json_value(value, "descriptive result")
        require_hash(self.output_hash, "output_hash")
        if self.receipt_hash is not None:
            require_hash(self.receipt_hash, "receipt_hash")
            if self.receipt_hash == self.output_hash:
                raise SchemaError("receipt hash must not be self-referential")
        if self.schema_version != METHODOLOGY_SCHEMA_VERSION:
            raise SchemaError("unsupported descriptive result schema version")


def safe_load_yaml(path: str | Path, *, max_bytes: int = MAX_YAML_BYTES) -> Any:
    """Load plain YAML only; custom tags and duplicate object tricks are rejected."""
    if max_bytes <= 0:
        raise SchemaError(f"YAML file exceeds {max_bytes} byte limit")
    with Path(path).open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise SchemaError(f"YAML file exceeds {max_bytes} byte limit")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SchemaError("YAML file must be UTF-8") from exc
    return yaml.load(content, Loader=_UniqueKeyLoader)


def load_registry(path: str | Path) -> list[Hypothesis]:
    raw = safe_load_yaml(path)
    _strict(raw, {"schema_version", "hypotheses"}, {"schema_version", "hypotheses"})
    if raw["schema_version"] != SCHEMA_VERSION:
        raise SchemaError("unsupported registry schema version")
    hypotheses = [Hypothesis.from_dict(item) for item in raw["hypotheses"]]
    ids = [item.hypothesis_id for item in hypotheses]
    if len(ids) != len(set(ids)):
        raise SchemaError("duplicate hypothesis id")
    return hypotheses

