"""Target-free candidate subprocess for the cooperative threat model."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.autoresearch.schemas import (
    CandidateRow,
    SchemaError,
    require_utc,
    validate_candidate_rows,
)

from .data_access import assert_no_forbidden_source, contained_path
from .preregister import canonical_bytes, digest_object

_EVALUATOR_OWNED_TOKENS = {
    "target", "outcome", "future", "forward", "fwd", "pnl", "cost", "censor",
    "label",
}
_EVALUATOR_OWNED_EXACT = {
    "trading_date", "exit_price", "future_price", "forward_return", "realized_pnl",
}

_LAUNCHER = r"""
import json, runpy, socket, sys
def denied(*args, **kwargs):
    raise PermissionError("network disabled by autoresearch runner")
socket.socket = denied
socket.create_connection = denied
payload = json.load(sys.stdin)
module = runpy.run_path(payload["source"], run_name="autoresearch_candidate")
if payload.get("rows_path"):
    class JsonlRows:
        def __len__(self):
            return payload["row_count"]
        def __iter__(self):
            with open(payload["rows_path"], "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        yield json.loads(line)
        def __getitem__(self, index):
            if isinstance(index, slice):
                return list(self)[index]
            if index < 0:
                index += len(self)
            for offset, row in enumerate(self):
                if offset == index:
                    return row
            raise IndexError(index)
    rows = JsonlRows()
else:
    rows = payload["rows"]
if payload["operation"] == "fit_transform":
    if "fit" not in module:
        raise RuntimeError("candidate must define fit(rows)")
    state = (
        module["fit"](rows, payload["config"])
        if payload["config"] is not None else module["fit"](rows)
    )
else:
    state = payload["state"]
if "transform" not in module:
    raise RuntimeError("candidate must define transform(rows, frozen_state)")
output = module["transform"](rows, state)
json.dump({"state": state, "rows": output}, sys.stdout, allow_nan=False)
"""


def target_free_rows(
    rows: Sequence[Mapping[str, Any]], allowed_columns: set[str]
) -> list[dict[str, Any]]:
    lowered = {column.lower() for column in allowed_columns}
    forbidden = {
        column for column in lowered
        if column in _EVALUATOR_OWNED_EXACT
        or _EVALUATOR_OWNED_TOKENS.intersection(column.split("_"))
    }
    if forbidden:
        raise SchemaError("allowed input schema contains evaluator-owned columns")
    projected: list[dict[str, Any]] = []
    for row in rows:
        missing = allowed_columns - set(row)
        if missing:
            raise SchemaError(f"candidate input missing columns: {sorted(missing)}")
        projected.append({column: row[column] for column in sorted(allowed_columns)})
    return projected


def _input_formation_timestamps(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    timestamps = [require_utc(row["formation_ts"]) for row in rows]
    if len(timestamps) != len(set(timestamps)):
        raise SchemaError("input formation_ts values must be unique")
    return set(timestamps)


def _stream_target_free_rows(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    allowed_columns: set[str],
) -> set[str]:
    lowered = {column.lower() for column in allowed_columns}
    if any(
        column in _EVALUATOR_OWNED_EXACT
        or _EVALUATOR_OWNED_TOKENS.intersection(column.split("_"))
        for column in lowered
    ):
        raise SchemaError("allowed input schema contains evaluator-owned columns")
    timestamps: set[str] = set()
    with path.open("wb") as handle:
        for row in rows:
            missing = allowed_columns - set(row)
            if missing:
                raise SchemaError(f"candidate input missing columns: {sorted(missing)}")
            projected = {
                column: row[column] for column in sorted(allowed_columns)
            }
            timestamp = require_utc(projected["formation_ts"])
            if timestamp in timestamps:
                raise SchemaError("input formation_ts values must be unique")
            timestamps.add(timestamp)
            handle.write(canonical_bytes(projected) + b"\n")
    return timestamps


def run_candidate(
    source: str | Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    split: str,
    source_roots: Sequence[str | Path],
    allowed_input_columns: set[str],
    registered_feature_map: Mapping[str, str] | set[str] | None = None,
    registered_cells: set[str] | None = None,
    registered_directions: set[str] | None = None,
    frozen_state: Any = None,
    config: Mapping[str, Any] | None = None,
    contract_hash: str | None = None,
    declared_grid: Mapping[str, Sequence[Any]] | None = None,
    operation: str | None = None,
    timeout_seconds: float = 10.0,
) -> tuple[list[CandidateRow], Any, str]:
    """Fit only on discovery, then transform in an isolated subprocess."""
    if registered_feature_map is not None and registered_cells is not None:
        raise SchemaError("use registered_feature_map; cells are evaluator-owned")
    registered_features = set(registered_feature_map or registered_cells or ())
    if not registered_features:
        raise SchemaError("registered feature map cannot be empty")
    if registered_feature_map is not None and isinstance(registered_feature_map, Mapping):
        if any(not isinstance(name, str) or not name for name in registered_feature_map):
            raise SchemaError("registered feature names must be non-empty strings")
    if registered_directions is not None:
        # Kept only so old callers fail explicitly when using string directions.
        if registered_feature_map is not None:
            raise SchemaError("directions are numeric and need no string registry")
    source_path = contained_path(source, list(source_roots))
    source_text = source_path.read_text(encoding="utf-8")
    assert_no_forbidden_source(source_text)
    if split not in {"discovery", "validation", "quarantine", "confirmation"}:
        raise SchemaError("invalid split")
    selected_operation = operation or (
        "fit_transform" if split == "discovery" else "transform"
    )
    if selected_operation not in {"fit_transform", "transform"}:
        raise SchemaError("operation must be fit_transform or transform")
    if selected_operation == "fit_transform" and split != "discovery":
        raise SchemaError("candidate fitting is restricted to discovery")
    if selected_operation == "transform" and frozen_state is None:
        raise SchemaError("candidate transform requires frozen state")
    bound = config is not None or contract_hash is not None or declared_grid is not None
    if bound and (config is None or contract_hash is None or declared_grid is None):
        raise SchemaError("config, contract_hash, and declared_grid must be supplied together")
    if bound:
        if len(contract_hash) != 64 or any(c not in "0123456789abcdef" for c in contract_hash):
            raise SchemaError("contract_hash must be lowercase SHA-256")
        if set(config) != set(declared_grid):
            raise SchemaError("candidate config keys differ from declared grid")
        if any(config[name] not in declared_grid[name] for name in config):
            raise SchemaError("candidate config contains undeclared value")
        canonical_bytes(config)
        if selected_operation == "transform":
            if not isinstance(frozen_state, Mapping) or set(frozen_state) != {
                "contract_hash", "config", "candidate_state",
            }:
                raise SchemaError("invalid runner-bound state envelope")
            if frozen_state["contract_hash"] != contract_hash or frozen_state["config"] != config:
                raise SchemaError("frozen state binding differs from requested execution")
    if "formation_ts" not in allowed_input_columns:
        raise SchemaError("candidate input must include formation_ts")
    environment = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if os.name == "nt" and "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    with tempfile.TemporaryDirectory(prefix="autoresearch-payload-") as temporary:
        temporary_path = Path(temporary)
        rows_path = temporary_path / "rows.ndjson"
        input_timestamps = _stream_target_free_rows(
            rows_path, rows, allowed_input_columns,
        )
        empty_cwd = temporary_path / "cwd"
        empty_cwd.mkdir()
        payload = {
            "source": str(source_path),
            "rows_path": str(rows_path),
            "row_count": len(input_timestamps),
            "operation": selected_operation,
            "state": (
                frozen_state["candidate_state"]
                if bound and selected_operation == "transform" else frozen_state
            ),
            "config": dict(config) if config is not None else None,
        }
        completed = subprocess.run(
            [sys.executable, "-I", "-c", _LAUNCHER],
            input=canonical_bytes(payload),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(empty_cwd),
            env=environment,
            timeout=timeout_seconds,
            check=False,
        )
    if completed.returncode:
        message = completed.stderr.decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"candidate subprocess failed: {message}")
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SchemaError("candidate did not emit one JSON envelope") from exc
    if set(envelope) != {"state", "rows"}:
        raise SchemaError("invalid candidate envelope")
    candidate_state = envelope["state"]
    state = (
        {"contract_hash": contract_hash, "config": dict(config), "candidate_state": candidate_state}
        if bound else candidate_state
    )
    canonical_bytes(state)  # frozen state must be finite JSON
    if selected_operation == "transform" and digest_object(state) != digest_object(frozen_state):
        raise SchemaError("candidate mutated frozen state during transform")
    parsed = validate_candidate_rows(envelope["rows"], registered_features)
    invented = {row.formation_ts for row in parsed} - input_timestamps
    if invented:
        raise SchemaError(f"candidate emitted absent formation_ts: {sorted(invented)}")
    return parsed, state, digest_object(state)


def rows_as_dicts(rows: Sequence[CandidateRow]) -> list[dict[str, Any]]:
    return [asdict(row) for row in rows]

