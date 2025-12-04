"""RunnerAgent MCP tools for final consolidation.

This module exposes two tools:

1. generate_structured_output
   - Deterministic, non-reasoning tool
   - Takes worker_outputs and optional planning_table
   - Returns a simple, structured summary plus raw inputs

2. build_runner_answer
   - Reasoning-enabled tool backed by RunnerAgent
   - Uses the same model configuration as the standalone agent
   - Returns a final answer with reasoning metadata
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.mcp.discovery import register_tool
from src.agents.reasoning_agent.run import RunnerAgent

logger = logging.getLogger(__name__)


@register_tool(
    name="generate_structured_output",
    description=(
        "Generate a deterministic, structured summary of worker outputs without "
        "calling any LLM. Useful when you want a consolidated view of what "
        "each worker produced, but do not need a narrative answer."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "worker_outputs": {
                "type": "array",
                "items": {"type": "object"},
            },
            "planning_table": {
                "type": "array",
                "items": {"type": "object"},
            },
            "run_id": {"type": ["string", "null"]},
        },
        "required": ["query", "worker_outputs"],
    },
)
def generate_structured_output(
    query: str,
    worker_outputs: List[Dict[str, Any]],
    planning_table: Optional[List[Dict[str, Any]]] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a simple, structured view of worker outputs (no reasoning).

    This tool performs light, deterministic aggregation only:
    - counts tasks
    - groups tasks by agent_name
    - preserves full worker_outputs and planning_table
    """
    logger.info(
        "generate_structured_output called (run_id=%s, num_worker_outputs=%s)",
        run_id,
        len(worker_outputs),
    )

    agents_used = sorted(
        {w.get("agent_name", "") for w in worker_outputs if w.get("agent_name")}
    )

    tasks_per_agent: Dict[str, int] = {}
    for w in worker_outputs:
        agent = w.get("agent_name", "unknown")
        tasks_per_agent[agent] = tasks_per_agent.get(agent, 0) + 1

    summary: Dict[str, Any] = {
        "query": query,
        "run_id": run_id,
        "total_tasks": len(worker_outputs),
        "agents_used": agents_used,
        "tasks_per_agent": tasks_per_agent,
    }

    return {
        "summary": summary,
        "worker_outputs": worker_outputs,
        "planning_table": planning_table or [],
    }


@register_tool(
    name="build_runner_answer",
    description=(
        "Use the RunnerAgent reasoning model to build a final user-facing answer "
        "given worker_outputs and an optional planning_table."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "worker_outputs": {
                "type": "array",
                "items": {"type": "object"},
            },
            "planning_table": {
                "type": "array",
                "items": {"type": "object"},
            },
            "run_id": {"type": ["string", "null"]},
        },
        "required": ["query", "worker_outputs"],
    },
)
def build_runner_answer(
    query: str,
    worker_outputs: List[Dict[str, Any]],
    planning_table: Optional[List[Dict[str, Any]]] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a final answer using the RunnerAgent's reasoning capabilities.

    This tool mirrors the standalone RunnerAgent behaviour, but returns its
    payload directly instead of writing to the file bus.
    """
    logger.info(
        "build_runner_answer called (run_id=%s, num_worker_outputs=%s)",
        run_id,
        len(worker_outputs),
    )

    runner = RunnerAgent()
    reasoning_input: Dict[str, Any] = {
        "query": query,
        "worker_outputs": worker_outputs,
        "planning_table": planning_table or [],
    }

    # Re-use the agent's internal model call, including fallback behaviour.
    answer_text, reasoning_metadata = runner._call_model(reasoning_input)  # type: ignore[attr-defined]

    agents_used = sorted(
        {w.get("agent_name", "") for w in worker_outputs if w.get("agent_name")}
    )

    return {
        "query": query,
        "run_id": run_id,
        "final_answer": answer_text,
        "reasoning_metadata": reasoning_metadata,
        "worker_outputs": worker_outputs,
        "planning_table": planning_table or [],
        "summary": {
            "total_tasks": len(worker_outputs),
            "agents_used": agents_used,
        },
    }


