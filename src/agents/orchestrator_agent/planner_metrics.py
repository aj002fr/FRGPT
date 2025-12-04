
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .dependency_analyzer import DependencyAnalyzer
from src.servers.marketdata.schema import (
    ALLOWED_COLUMNS,
    QUERY_TEMPLATES,
    SORTABLE_COLUMNS,
)
from src.agents.polymarket_agent.config import MAX_QUERY_LENGTH


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
    agent_params_ok: bool


def _extract_agent_and_params(task: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
    """Best-effort extraction of agent name and params from a task dict.

    Supports both runtime subtasks (PlannerStage1/2) and offline GEPA
    planner outputs, which may use slightly different field names.
    """
    raw_agent = (
        task.get("assigned_agent")
        or task.get("mapped_agent")
        or task.get("agent")
    )

    agent_name: Optional[str]
    if isinstance(raw_agent, str):
        agent_name = raw_agent.lower().replace("-", "_")
    else:
        agent_name = None

    params_obj = task.get("agent_params")
    if not isinstance(params_obj, dict):
        params_obj = task.get("params")
    if not isinstance(params_obj, dict):
        params: Dict[str, Any] = {}
    else:
        params = params_obj

    return agent_name, params


def _validate_market_data_agent_params(params: Dict[str, Any]) -> bool:
    """Validate params for the Market Data agent.

    We require that:
    - template is one of the known QUERY_TEMPLATES keys
    - for non-custom templates, params include the expected basic keys
    - for custom templates, conditions/values are present
    - optional columns / order_by fields are drawn from the whitelist
    """
    if not params:
        return False

    template = str(params.get("template", "by_symbol"))
    if template not in QUERY_TEMPLATES:
        return False

    inner = params.get("params", {})
    if not isinstance(inner, dict):
        return False

    if template == "by_symbol":
        sym = inner.get("symbol_pattern")
        if not isinstance(sym, str) or not sym:
            return False
    elif template == "by_date":
        date_str = inner.get("file_date")
        if not isinstance(date_str, str) or len(date_str) < 8:
            return False
    elif template == "by_symbol_and_date":
        sym = inner.get("symbol_pattern")
        date_str = inner.get("file_date")
        if not (isinstance(sym, str) and sym):
            return False
        if not (isinstance(date_str, str) and len(date_str) >= 8):
            return False
    elif template == "custom":
        conditions = inner.get("conditions")
        values = inner.get("values")
        if not isinstance(conditions, str) or not conditions.strip():
            return False
        if not isinstance(values, list) or not values:
            return False

    # Optional: columns
    columns = params.get("columns")
    if columns is not None:
        if not isinstance(columns, list) or not columns:
            return False
        for col in columns:
            if not isinstance(col, str) or (col not in ALLOWED_COLUMNS and col != "*"):
                return False

    # Optional: ORDER BY validation
    order_col = params.get("order_by_column")
    if order_col is not None:
        if not isinstance(order_col, str) or order_col not in SORTABLE_COLUMNS:
            return False
        direction = str(params.get("order_by_direction", "ASC")).upper()
        if direction not in {"ASC", "DESC"}:
            return False

    # Optional: numeric limit
    limit = params.get("limit")
    if limit is not None:
        if not isinstance(limit, int) or limit <= 0:
            return False

    return True


def _validate_polymarket_agent_params(
    description: str,
    params: Dict[str, Any],
) -> bool:
    """Validate params for the Polymarket agent.

    Key expectations:
    - a concise natural-language `query` string
    - optional `limit` within a reasonable range
    - any explicit dates/timestamps should be non-empty strings
    """
    if not params:
        return False

    query = params.get("query")
    if not isinstance(query, str) or not query.strip():
        return False
    if len(query) > MAX_QUERY_LENGTH:
        return False

    # Limit, if present, should be a small positive int (<=50 as per mapper).
    limit = params.get("limit")
    if limit is not None:
        if not isinstance(limit, int) or not (1 <= limit <= 50):
            return False

    # If session_id is present, allow None or non-empty string.
    session_id = params.get("session_id")
    if session_id is not None and not (
        isinstance(session_id, str) and session_id.strip()
    ):
        return False

    # Light check for any date-like fields (if the planner starts emitting them).
    for key, value in params.items():
        if any(tok in key.lower() for tok in ("date", "time", "timestamp")):
            if not isinstance(value, str) or not value.strip():
                return False

    # Fallback: if mapper-style params were not present but description is
    # usable as a query, allow that as a lenient success case.
    if not query.strip() and isinstance(description, str) and description.strip():
        if len(description.strip()) <= MAX_QUERY_LENGTH:
            return True

    return True


def _validate_web_search_agent_params(params: Dict[str, Any]) -> bool:
    """Validate params for conceptual web_search_agent used in GEPA.

    Expectations (kept intentionally lenient):
    - non-empty `query` string
    - optional `time_window` / `site` / `url` fields are non-empty strings
    """
    if not params:
        return False

    query = params.get("query")
    if not isinstance(query, str) or not query.strip():
        return False

    for key in ("time_window", "site", "url"):
        value = params.get(key)
        if value is not None and not (isinstance(value, str) and value.strip()):
            return False

    return True


def _validate_event_data_puller_params(params: Dict[str, Any]) -> bool:
    """Validate params for conceptual event_data_puller_agent in GEPA.

    We expect at least one event/time-related field to be present and
    non-empty (for example, event_type, count_or_range, fields, or any
    key mentioning 'event', 'date', or 'time').
    """
    if not params:
        return False

    primary_keys = ("event_type", "count_or_range", "fields")
    has_primary = any(
        isinstance(params.get(k), (str, list, dict)) and params.get(k)
        for k in primary_keys
    )

    has_eventy_key = False
    for key, value in params.items():
        if any(tok in key.lower() for tok in ("event", "date", "time")):
            if isinstance(value, (str, list, dict)) and value:
                has_eventy_key = True
                break

    return has_primary or has_eventy_key


def validate_agent_params(task: Dict[str, Any]) -> bool:
    """Return True if a task's agent parameters look well-formed.

    This is a cheap, deterministic check that inspects the parameters
    produced for each agent and ensures they are consistent with that
    agent's expected inputs. It is intentionally conservative: failures
    indicate likely mismatches between planning and execution.
    """
    try:
        agent_name, params = _extract_agent_and_params(task)
        if not agent_name:
            # No agent assigned → nothing to validate.
            return True

        description = str(task.get("description", ""))

        if agent_name == "market_data_agent":
            return _validate_market_data_agent_params(params)
        if agent_name == "polymarket_agent":
            return _validate_polymarket_agent_params(description, params)
        if agent_name == "web_search_agent":
            return _validate_web_search_agent_params(params)
        if agent_name in {"event_data_puller_agent", "event_data_puller"}:
            return _validate_event_data_puller_params(params)

        # For unknown agents, be lenient so new agents don't get penalised
        # until explicit validation rules are added.
        return True
    except Exception:
        # Any unexpected error here is treated as a validation failure.
        return False


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
            agent_params_ok=True,
        )

    # Basic schema validity
    valid_count = sum(1 for t in subtasks if _validate_task_schema(t))
    schema_validity_rate = valid_count / max(len(subtasks), 1)

    # Agent-params validity: only consider tasks that have an agent assigned.
    agent_param_checks: List[bool] = []
    for t in subtasks:
        agent_name, _ = _extract_agent_and_params(t)
        if agent_name:
            agent_param_checks.append(validate_agent_params(t))
    agent_params_ok = all(agent_param_checks) if agent_param_checks else True

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
        agent_params_ok=agent_params_ok,
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
        penalty += (1 - struct.schema_validity_rate) * 0.2

    # Penalise badly-formed agent parameters.
    if not struct.agent_params_ok:
        penalty += 0.15

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
    "validate_agent_params",
]


