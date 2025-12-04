"""Unit tests for planner_metrics structural validation."""

from typing import Any, Dict, List

from src.agents.orchestrator_agent.planner_metrics import (
    StructuralMetrics,
    compute_structural_metrics,
    validate_agent_params,
)


def _make_task(
    *,
    task_id: str = "task_1",
    description: str = "Test task",
    assigned_agent: str | None = None,
    agent_params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "id": task_id,
        "description": description,
        "assigned_agent": assigned_agent,
        "dependencies": [],
        "agent_params": agent_params or {},
    }


def test_validate_agent_params_market_data_ok() -> None:
    task = _make_task(
        assigned_agent="market_data_agent",
        agent_params={
            "template": "by_symbol_and_date",
            "params": {
                "symbol_pattern": "%ZN%",
                "file_date": "2025-01-01",
            },
            "columns": ["symbol", "price"],
            "limit": 10,
            "order_by_column": "file_date",
            "order_by_direction": "DESC",
        },
    )

    assert validate_agent_params(task) is True


def test_validate_agent_params_market_data_bad_template_fails() -> None:
    task = _make_task(
        assigned_agent="market_data_agent",
        agent_params={
            "template": "unknown_template",
            "params": {},
        },
    )

    assert validate_agent_params(task) is False


def test_validate_agent_params_polymarket_ok() -> None:
    task = _make_task(
        assigned_agent="polymarket_agent",
        agent_params={
            "query": "US recession probability in next 12 months",
            "limit": 5,
            "session_id": "session-123",
        },
    )

    assert validate_agent_params(task) is True


def test_compute_structural_metrics_sets_agent_params_ok_flag() -> None:
    good = _make_task(
        task_id="task_1",
        assigned_agent="market_data_agent",
        agent_params={
            "template": "by_symbol",
            "params": {"symbol_pattern": "%ZN%"},
        },
    )
    bad = _make_task(
        task_id="task_2",
        assigned_agent="market_data_agent",
        agent_params={
            "template": "unknown_template",
            "params": {},
        },
    )

    metrics_good = compute_structural_metrics([good])
    assert isinstance(metrics_good, StructuralMetrics)
    assert metrics_good.agent_params_ok is True

    metrics_mixed = compute_structural_metrics([good, bad])
    assert metrics_mixed.agent_params_ok is False


