
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .dependency_analyzer import DependencyAnalyzer


@dataclass
class StructuralMetrics:
    """Cheap, deterministic metrics computed from the task DAG itself."""

    dag_ok: bool
    has_cycles: bool
    max_depth: int
    total_tasks: int
    independent_tasks: int
    parallel_group_count: int
    schema_validity_rate: float


def _validate_task_schema(task: Dict[str, Any]) -> bool:
    """Lightweight schema validation for a single task dictionary."""
    required_keys = {"id", "description", "dependencies"}

    if not required_keys.issubset(task.keys()):
        return False

    if not isinstance(task["id"], str):
        return False

    if not isinstance(task.get("description", ""), str):
        return False

    deps = task.get("dependencies", [])
    if not isinstance(deps, list):
        return False

    return True


def compute_structural_metrics(
    subtasks: List[Dict[str, Any]]
) -> StructuralMetrics:
    """Compute deterministic structural metrics for a list of subtasks.
    """
    if not subtasks:
        return StructuralMetrics(
            dag_ok=True,
            has_cycles=False,
            max_depth=0,
            total_tasks=0,
            independent_tasks=0,
            parallel_group_count=0,
            schema_validity_rate=1.0,
        )

    # Basic schema validity
    valid_count = sum(1 for t in subtasks if _validate_task_schema(t))
    schema_validity_rate = valid_count / max(len(subtasks), 1)

    analyzer = DependencyAnalyzer()
    try:
        analysis = analyzer.analyze(subtasks)
        has_cycles = analysis["has_cycles"]
        dag_ok = not has_cycles
        max_depth = int(analysis.get("max_depth", 0))
        total_tasks = int(analysis.get("total_tasks", len(subtasks)))
        independent_tasks = int(analysis.get("independent_tasks", 0))
        parallel_group_count = len(analysis.get("parallel_groups", []))
    except Exception:
        # Any exception here means the DAG is suspect; return conservative values.
        dag_ok = False
        has_cycles = True
        max_depth = 0
        total_tasks = len(subtasks)
        independent_tasks = 0
        parallel_group_count = 0

    return StructuralMetrics(
        dag_ok=dag_ok,
        has_cycles=has_cycles,
        max_depth=max_depth,
        total_tasks=total_tasks,
        independent_tasks=independent_tasks,
        parallel_group_count=parallel_group_count,
        schema_validity_rate=schema_validity_rate,
    )


def combine_structural_and_llm_scores(
    struct: StructuralMetrics,
    llm_scores: Dict[str, Any],
    *,
    clamp: bool = True,
) -> float:
    """Combine structural metrics and LLM-as-judge scores into a scalar.

    Parameters
    ----------
    struct:
        Output from :func:`compute_structural_metrics`.
    llm_scores:
        Dictionary returned by an LLM evaluator. Expected keys:
        - coverage_score (0–1)
        - granularity_score (0–1)
        - dependency_recall (0–1)
        - dependency_precision (0–1)
        - task_sufficiency_score (0–1)
        - task_precision_score (0–1)
        - faithfulness_score (0–1)
        - agent_mapping_accuracy (0–1)
        - silent_drop_count (int)
        - misallocation_severity (\"none\" | \"minor\" | \"moderate\" | \"severe\")

    """

    def _get(name: str, default: float) -> float:
        value = llm_scores.get(name, default)
        if isinstance(value, (int, float)):
            return float(value)
        return default

    coverage = _get("coverage_score", 0.0)
    granularity = _get("granularity_score", 0.0)
    dep_recall = _get("dependency_recall", 0.0)
    dep_precision = _get("dependency_precision", 0.0)
    task_suff = _get("task_sufficiency_score", 0.0)
    task_prec = _get("task_precision_score", 0.0)
    faithfulness = _get("faithfulness_score", 0.0)
    agent_mapping = _get("agent_mapping_accuracy", 0.0)
    silent_drop_count = int(llm_scores.get("silent_drop_count", 0) or 0)
    misalloc = str(llm_scores.get("misallocation_severity", "none") or "none").lower()

    # Base score: weighted sum of the main semantic metrics.
    base = (
        0.2 * coverage
        + 0.15 * granularity
        + 0.15 * dep_recall
        + 0.1 * dep_precision
        + 0.1 * task_suff
        + 0.1 * task_prec
        + 0.1 * faithfulness
        + 0.1 * agent_mapping
    )

    # Structural penalties.
    penalty = 0.0

    if not struct.dag_ok:
        penalty += 0.3

    # Penalise poor schema validity.
    if struct.schema_validity_rate < 0.5:
        penalty += (1- struct.schema_validity_rate) * 0.2

    # Penalise silent drops and serious misallocations.
    penalty += 0.05 * max(silent_drop_count, 0)

    if misalloc == "minor":
        penalty += 0.05
    elif misalloc == "moderate":
        penalty += 0.15
    elif misalloc == "severe":
        penalty += 0.3

    score = base - penalty

    if clamp:
        if score < 0.0:
            return 0.0
        if score > 1.0:
            return 1.0

    return score


__all__ = [
    "StructuralMetrics",
    "compute_structural_metrics",
    "combine_structural_and_llm_scores",
]


