"""Checksum-bound discovery-only matrix cache."""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import pandas as pd

from scripts.autoresearch.fixed_env.matrix import (
    CandidateRows,
    FrozenMatrix,
    MatrixSlice,
)

from .lab_readers import MANIFESTS_ROOT, PROJECT_ROOT, load_json, load_registry

BASELINE_RAW = (
    "realized_vol",
    "trade_intensity",
    "spread_mean",
    "session_sin",
    "session_cos",
)
_CACHE_LOCK = threading.Lock()


def _candidate_columns() -> tuple[str, ...]:
    registry = load_registry()
    values: list[str] = []
    for spec in registry["experiments"].values():
        for column in spec["candidate"]["allowed_inputs"]:
            if column not in values:
                values.append(column)
    return tuple(values)


def _version_dir(manifest: dict) -> Path:
    return (
        PROJECT_ROOT
        / "workspace"
        / "analysis"
        / "aggression_autoresearch_data"
        / "versions"
        / manifest["data_version"]
    )


class DiscoveryOnlyMatrix:
    """A firewall around FrozenMatrix that can expose discovery and nothing else."""

    def __init__(self, adapter: FrozenMatrix) -> None:
        self._adapter = adapter
        self._cached: MatrixSlice | None = None
        self.allowed_inputs = _candidate_columns()
        self.data_hash = str(adapter.publication["matrix_sha256"])

    def assigned_dates(self, split: str = "discovery") -> tuple[str, ...]:
        self._require_discovery(split)
        return self._adapter.assigned_dates("discovery")

    @staticmethod
    def _require_discovery(split: str) -> None:
        if split != "discovery":
            raise PermissionError(
                "Autoresearch Lab compute is discovery-only; holdouts are unavailable"
            )

    def warm(self) -> MatrixSlice:
        if self._cached is not None:
            return self._cached
        with _CACHE_LOCK:
            if self._cached is None:
                self._cached = self._adapter.load(
                    "discovery",
                    self.allowed_inputs,
                    evaluator_inputs=BASELINE_RAW,
                )
        return self._cached

    def load(
        self,
        split: str,
        allowed_inputs: Sequence[str],
        *,
        dates: Sequence[str] | None = None,
        evaluator_inputs: Sequence[str] = (),
    ) -> MatrixSlice:
        self._require_discovery(split)
        cached = self.warm()
        selected = tuple(dates or cached.dates)
        if not selected or not set(selected).issubset(cached.dates):
            raise PermissionError("requested dates are not assigned to discovery")
        missing_candidate = set(allowed_inputs) - set(self.allowed_inputs)
        missing_evaluator = set(evaluator_inputs) - set(cached.evaluator_frame.columns)
        if missing_candidate or missing_evaluator:
            raise ValueError(
                f"uncached columns requested: {sorted(missing_candidate | missing_evaluator)}"
            )
        date_values = cached.evaluator_frame["et_date"].astype(str)
        index = cached.evaluator_frame.index[date_values.isin(selected)]
        candidate = CandidateRows(
            cached.candidate_rows.frame.loc[index],
            tuple(allowed_inputs),
        )
        evaluator = cached.evaluator_frame.loc[index].copy()
        return MatrixSlice("discovery", selected, candidate, evaluator)

    def combined_frame(self) -> pd.DataFrame:
        cached = self.warm()
        candidate = cached.candidate_rows.frame
        evaluator = cached.evaluator_frame
        additions = [column for column in candidate.columns if column not in evaluator]
        return evaluator.join(candidate[additions]).copy()

    def status(self) -> dict:
        return {
            "split": "discovery",
            "loaded": self._cached is not None,
            "dates": len(self._cached.dates) if self._cached else len(self.assigned_dates()),
            "rows": len(self._cached.evaluator_frame) if self._cached else None,
            "data_hash": self.data_hash,
        }


@lru_cache(maxsize=1)
def discovery_matrix() -> DiscoveryOnlyMatrix:
    manifest = load_json(MANIFESTS_ROOT / "execution_manifest_v5.json")
    adapter = FrozenMatrix(
        _version_dir(manifest),
        MANIFESTS_ROOT / "split_manifest_v2.json",
        MANIFESTS_ROOT / "event_manifest.json",
        expected_matrix_hash=manifest["data_hash"],
        expected_manifest_hash=manifest["publication_manifest_hash"],
        roll_manifest=MANIFESTS_ROOT / "roll_manifest_v1.json",
        expected_roll_hash=manifest["roll_hash"],
    )
    return DiscoveryOnlyMatrix(adapter)


def warm_discovery_cache() -> dict:
    matrix = discovery_matrix()
    matrix.warm()
    return matrix.status()
