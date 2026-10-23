"""Planner 2 Metrics - Tool Selection and Parameter Refinement Evaluation.

This module provides metrics for evaluating Planner 2 outputs during GEPA
prompt optimization. Planner 2 is responsible for:
- Selecting which tools to use for each task
- Refining tool-level parameters from coarse agent_params
- Determining tool execution order within a task

These metrics complement the Planner 1 metrics in planner_metrics.py, focusing
specifically on tool-level quality rather than task decomposition quality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .tool_loader import ToolLoader


@dataclass
class Planner2StructuralMetrics:
    """Deterministic metrics for Planner 2 tool selection and parameter quality."""

    # Schema validity (Planner 2 output format)
    schema_valid: bool
    schema_validity_rate: float
    schema_errors: List[str]

    # Tool selection validity
    tool_selection_valid: bool
    invalid_tool_count: int
    tools_not_in_agent_map: List[str]

    # Tool parameter validity
    tool_params_valid: bool
    invalid_param_count: int
    param_validation_errors: List[str]

    # Coverage metrics
    total_tools_selected: int
    unique_tools_selected: int
    tasks_with_no_tools: int
    tasks_with_tools: int

    # Execution structure
    has_tool_dependencies: bool
    multi_tool_tasks: int
    single_tool_tasks: int

    # Overall validity rate
    tool_validity_rate: float
    param_validity_rate: float


def _get_valid_tools_for_agent(agent_name: str) -> Set[str]:
    """Get the set of valid tools for an agent from AGENT_TOOL_MAP."""
    if not agent_name:
        return set()
    normalized = agent_name.lower().replace("-", "_")
    return set(ToolLoader.AGENT_TOOL_MAP.get(normalized, []))


def _validate_planner2_task_schema(task: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate Planner 2's output schema for a single task.

    Planner 2 receives a task from Planner 1 and must output:
    - task_id: str (required, from Planner 1)
    - agent: str (required, from Planner 1)
    - tools: list[str] (required, Planner 2 selects these)
    - tool_params: dict[str, dict] (required, Planner 2 refines these)
    - dependencies: list[str] (optional, from Planner 1)
    - description: str (optional, from Planner 1)
    - ai_reasoning: str (optional, Planner 2's explanation)

    Args:
        task: Task dictionary from Planner 2's execution_plan

    Returns:
        (is_valid, error_message) tuple
    """
    # Required: task_id
    task_id = task.get("task_id")
    if not task_id or not isinstance(task_id, str):
        return False, "Missing or invalid 'task_id' (must be non-empty string)"

    # Required: agent
    agent = task.get("agent") or task.get("assigned_agent")
    if not agent or not isinstance(agent, str):
        return False, f"Task {task_id}: Missing or invalid 'agent' (must be non-empty string)"

    # Required: tools (Planner 2's primary output)
    tools = task.get("tools")
    if tools is None:
        return False, f"Task {task_id}: Missing 'tools' field"
    if not isinstance(tools, list):
        return False, f"Task {task_id}: 'tools' must be a list, got {type(tools).__name__}"
    for i, tool in enumerate(tools):
        if not isinstance(tool, str) or not tool.strip():
            return False, f"Task {task_id}: tools[{i}] must be non-empty string"

    # Required: tool_params (Planner 2's parameter refinement)
    tool_params = task.get("tool_params")
    if tool_params is None:
        return False, f"Task {task_id}: Missing 'tool_params' field"
    if not isinstance(tool_params, dict):
        return False, f"Task {task_id}: 'tool_params' must be a dict, got {type(tool_params).__name__}"

    # Validate tool_params structure: each key should be a tool name, value should be dict
    for tool_name, params in tool_params.items():
        if not isinstance(tool_name, str):
            return False, f"Task {task_id}: tool_params key must be string, got {type(tool_name).__name__}"
        if not isinstance(params, dict):
            return False, f"Task {task_id}: tool_params['{tool_name}'] must be dict, got {type(params).__name__}"

    # Validate consistency: tools in 'tools' list should have entries in 'tool_params'
    # (This is a soft check - tools might use default params)
    for tool in tools:
        if tool not in tool_params:
            # This is acceptable - tool might use defaults, just note it
            pass

    # Optional: dependencies (from Planner 1)
    deps = task.get("dependencies")
    if deps is not None and not isinstance(deps, list):
        return False, f"Task {task_id}: 'dependencies' must be a list if present"

    return True, None


def _validate_run_query_params(params: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate parameters for the run_query tool (market_data_agent)."""
    if not params:
        return False, "run_query requires parameters"

    template = params.get("template")
    if not template:
        return False, "run_query requires 'template' parameter"

    valid_templates = {"by_symbol", "by_date", "by_symbol_and_date", "custom", "all_valid"}
    if template not in valid_templates:
        return False, f"Invalid template '{template}', must be one of {valid_templates}"

    inner_params = params.get("params", {})

    if template == "by_symbol":
        if not inner_params.get("symbol_pattern"):
            return False, "by_symbol template requires params.symbol_pattern"
    elif template == "by_date":
        if not inner_params.get("date"):
            return False, "by_date template requires params.date"
    elif template == "by_symbol_and_date":
        if not inner_params.get("symbol_pattern") or not inner_params.get("date"):
            return False, "by_symbol_and_date requires params.symbol_pattern and params.date"
    elif template == "custom":
        if not inner_params.get("conditions"):
            return False, "custom template requires params.conditions"

    # Validate optional limit
    limit = params.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit <= 0):
        return False, f"Invalid limit '{limit}', must be positive integer"

    return True, None


def _validate_search_polymarket_params(params: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate parameters for Polymarket search tools."""
    if not params:
        return False, "search tool requires parameters"

    query = params.get("query")
    if not query or not isinstance(query, str) or not query.strip():
        return False, "search tool requires non-empty 'query' string"

    if len(query) > 500:
        return False, f"query too long ({len(query)} chars), max 500"

    limit = params.get("limit")
    if limit is not None and (not isinstance(limit, int) or not (1 <= limit <= 100)):
        return False, f"Invalid limit '{limit}', must be 1-100"

    return True, None


def _validate_price_history_params(params: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate parameters for price history tools."""
    if not params:
        return False, "price history tool requires parameters"

    # Either market_id or token_id is typically needed
    market_id = params.get("market_id")
    token_id = params.get("token_id")
    if not market_id and not token_id:
        return False, "price history requires 'market_id' or 'token_id'"

    # Date validation
    date = params.get("date")
    start_date = params.get("start_date")
    end_date = params.get("end_date")

    if not date and not (start_date or end_date):
        # Allow if no date specified - might use defaults
        pass

    return True, None


def _validate_event_tool_params(params: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate parameters for eventdata_puller_agent tools."""
    if not params:
        return False, "event tool requires parameters"

    # Most event tools need at least one filter
    event_name = params.get("event_name")
    country = params.get("country")
    keyword = params.get("keyword")

    if not event_name and not country and not keyword:
        return False, "event tool requires at least one of: event_name, country, keyword"

    return True, None


def _validate_runner_tool_params(params: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate parameters for runner_agent tools."""
    if not params:
        return False, "runner tool requires parameters"

    query = params.get("query")
    if not query or not isinstance(query, str):
        return False, "runner tool requires 'query' parameter"

    return True, None


def _validate_analytics_tool_params(params: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate parameters for analytics_agent tools."""
    if not params:
        return False, "analytics tool requires parameters"

    # Most analytics tools need data or a data reference
    data = params.get("data")
    symbol = params.get("symbol")
    values = params.get("values")

    if not data and not symbol and not values:
        return False, "analytics tool requires data, symbol, or values parameter"

    return True, None


# Tool name -> validation function mapping
TOOL_VALIDATORS = {
    "run_query": _validate_run_query_params,
    "search_polymarket_markets": _validate_search_polymarket_params,
    "search_polymarket_with_history": _validate_search_polymarket_params,
    "get_polymarket_history": _validate_search_polymarket_params,
    "get_market_price_history": _validate_price_history_params,
    "get_market_price_range": _validate_price_history_params,
    "query_event_history": _validate_event_tool_params,
    "search_events": _validate_event_tool_params,
    "find_events_for_all_instances": _validate_event_tool_params,
    "fetch_economic_calendar": _validate_event_tool_params,
    "generate_structured_output": _validate_runner_tool_params,
    "build_runner_answer": _validate_runner_tool_params,
    "compute_statistics": _validate_analytics_tool_params,
    "compute_percentile_rank": _validate_analytics_tool_params,
    "compare_distributions": _validate_analytics_tool_params,
    "compute_correlation": _validate_analytics_tool_params,
    "generate_histogram": _validate_analytics_tool_params,
    "generate_line_chart": _validate_analytics_tool_params,
    "generate_scatter_plot": _validate_analytics_tool_params,
    "generate_bar_chart": _validate_analytics_tool_params,
    "compute_returns_distribution": _validate_analytics_tool_params,
    "compute_volatility_distribution": _validate_analytics_tool_params,
    "compute_probability_matrix": _validate_analytics_tool_params,
    "compute_event_distribution": _validate_analytics_tool_params,
}


def validate_tool_params(
    tool_name: str,
    params: Dict[str, Any],
) -> Tuple[bool, Optional[str]]:
    """Validate parameters for a specific tool.

    Args:
        tool_name: Name of the tool
        params: Parameters to validate

    Returns:
        (is_valid, error_message) tuple
    """
    validator = TOOL_VALIDATORS.get(tool_name)
    if validator:
        return validator(params)

    # For unknown tools, be lenient - just check params exist
    if not params:
        return False, f"Tool '{tool_name}' has no parameters"
    return True, None


def _extract_planner2_output(task: Dict[str, Any]) -> Tuple[str, List[str], Dict[str, Dict]]:
    """Extract Planner 2 outputs from a task dict.

    Args:
        task: Task dictionary (from execution_plan)

    Returns:
        (agent_name, tools_list, tool_params_dict)
    """
    agent = task.get("agent") or task.get("assigned_agent") or ""
    if isinstance(agent, str):
        agent = agent.lower().replace("-", "_")
    else:
        agent = ""

    tools = task.get("tools", [])
    if not isinstance(tools, list):
        tools = []

    tool_params = task.get("tool_params", {})
    if not isinstance(tool_params, dict):
        tool_params = {}

    return agent, tools, tool_params


def compute_planner2_structural_metrics(
    execution_plan: List[Dict[str, Any]],
) -> Planner2StructuralMetrics:
    """Compute deterministic structural metrics for Planner 2 output.

    Args:
        execution_plan: List of task execution plans from Planner 2.
            Each task should have: task_id, agent, tools, tool_params, dependencies

    Returns:
        Planner2StructuralMetrics dataclass
    """
    if not execution_plan:
        return Planner2StructuralMetrics(
            schema_valid=True,
            schema_validity_rate=1.0,
            schema_errors=[],
            tool_selection_valid=True,
            invalid_tool_count=0,
            tools_not_in_agent_map=[],
            tool_params_valid=True,
            invalid_param_count=0,
            param_validation_errors=[],
            total_tools_selected=0,
            unique_tools_selected=0,
            tasks_with_no_tools=0,
            tasks_with_tools=0,
            has_tool_dependencies=False,
            multi_tool_tasks=0,
            single_tool_tasks=0,
            tool_validity_rate=1.0,
            param_validity_rate=1.0,
        )

    # Schema validation counters
    schema_errors: List[str] = []
    schema_valid_count = 0

    # Tool selection/param counters
    invalid_tools: List[str] = []
    param_errors: List[str] = []
    all_tools: List[str] = []
    unique_tools: Set[str] = set()
    tasks_no_tools = 0
    tasks_with_tools = 0
    multi_tool = 0
    single_tool = 0
    has_deps = False
    total_param_checks = 0
    valid_param_checks = 0

    for task in execution_plan:
        # Step 1: Validate Planner 2 output schema
        schema_ok, schema_error = _validate_planner2_task_schema(task)
        if schema_ok:
            schema_valid_count += 1
        else:
            schema_errors.append(schema_error or "Unknown schema error")
            # Continue with other checks even if schema is invalid

        # Step 2: Extract and validate tool selection
        agent, tools, tool_params = _extract_planner2_output(task)
        valid_tools_for_agent = _get_valid_tools_for_agent(agent)

        # Check tool selection validity
        if not tools:
            tasks_no_tools += 1
        else:
            tasks_with_tools += 1
            all_tools.extend(tools)
            unique_tools.update(tools)

            if len(tools) > 1:
                multi_tool += 1
            else:
                single_tool += 1

            # Validate each tool is valid for this agent
            for tool_name in tools:
                if valid_tools_for_agent and tool_name not in valid_tools_for_agent:
                    invalid_tools.append(f"{tool_name} (not valid for {agent})")

                # Validate tool parameters
                params = tool_params.get(tool_name, {})
                total_param_checks += 1
                is_valid, error = validate_tool_params(tool_name, params)
                if is_valid:
                    valid_param_checks += 1
                else:
                    param_errors.append(f"{tool_name}: {error}")

        # Check for dependencies (indicates tool ordering)
        deps = task.get("dependencies", [])
        if deps:
            has_deps = True

    # Compute rates
    total_tasks = len(execution_plan)
    schema_validity_rate = schema_valid_count / total_tasks if total_tasks > 0 else 1.0

    total_tools = len(all_tools)
    tool_validity_rate = (
        (total_tools - len(invalid_tools)) / total_tools if total_tools > 0 else 1.0
    )
    param_validity_rate = (
        valid_param_checks / total_param_checks if total_param_checks > 0 else 1.0
    )

    return Planner2StructuralMetrics(
        schema_valid=len(schema_errors) == 0,
        schema_validity_rate=schema_validity_rate,
        schema_errors=schema_errors,
        tool_selection_valid=len(invalid_tools) == 0,
        invalid_tool_count=len(invalid_tools),
        tools_not_in_agent_map=invalid_tools,
        tool_params_valid=len(param_errors) == 0,
        invalid_param_count=len(param_errors),
        param_validation_errors=param_errors,
        total_tools_selected=total_tools,
        unique_tools_selected=len(unique_tools),
        tasks_with_no_tools=tasks_no_tools,
        tasks_with_tools=tasks_with_tools,
        has_tool_dependencies=has_deps,
        multi_tool_tasks=multi_tool,
        single_tool_tasks=single_tool,
        tool_validity_rate=tool_validity_rate,
        param_validity_rate=param_validity_rate,
    )


def _build_planner2_llm_eval_prompt(
    original_query: str,
    planner1_tasks: List[Dict[str, Any]],
    execution_plan: List[Dict[str, Any]],
    available_tools: Dict[str, List[str]],
) -> str:
    """Create evaluation prompt for LLM-as-judge scoring of Planner 2 output.

    Args:
        original_query: The user's original query
        planner1_tasks: Tasks from Planner 1 (with agent assignments)
        execution_plan: Planner 2's tool selections and parameters
        available_tools: Dict mapping agent -> list of available tools
    """
    import json

    planner1_summary = json.dumps(planner1_tasks, indent=2)
    planner2_output = json.dumps(execution_plan, indent=2)
    tools_summary = json.dumps(available_tools, indent=2)

    return f"""You are evaluating a Planner 2 system that selects tools and refines parameters.

**Context:**
- Planner 1 has already decomposed the query into tasks and assigned agents
- Planner 2 must select specific tools for each task and provide tool parameters
- Each agent has a specific set of available tools

**Original Query:**
{original_query}

**Planner 1 Output (tasks with agent assignments):**
{planner1_summary}

**Available Tools Per Agent:**
{tools_summary}

**Planner 2 Output (tool selections and parameters):**
{planner2_output}

**Evaluate Planner 2's output and return a JSON object with these scores:**

{{
  "tool_selection_accuracy": float,    // 0-1: Did Planner 2 select appropriate tools for each task?
  "tool_coverage_score": float,        // 0-1: Do selected tools cover all aspects of each task?
  "param_completeness_score": float,   // 0-1: Are tool parameters complete and correctly specified?
  "param_precision_score": float,      // 0-1: Are parameters precise (no unnecessary fields)?
  "tool_ordering_score": float,        // 0-1: Is tool execution order logical (data fetch before analysis)?
  "intent_preservation_score": float,  // 0-1: Do tool selections preserve the original query intent?
  "silent_drop_count": int,            // Number of task aspects not mapped to any tool
  "over_selection_count": int,         // Number of unnecessary tools selected
  "misselection_severity": str         // "none" | "minor" | "moderate" | "severe"
}}

Guidelines:
- tool_selection_accuracy: Penalize if tools are selected that can't do what the task requires
- tool_coverage_score: Penalize if parts of a task aren't covered by any tool call
- param_completeness_score: Penalize missing required parameters
- param_precision_score: Penalize irrelevant or overly broad parameters
- tool_ordering_score: Penalize if analysis tools are called before data-fetching tools
- intent_preservation_score: Penalize if tool selections distort the original query intent
- silent_drop_count: Count task requirements that have no corresponding tool call
- over_selection_count: Count tools that are unnecessary for the task
- misselection_severity: Rate overall tool selection mistakes

Return ONLY the JSON object, no commentary."""


def call_planner2_llm_evaluator(
    original_query: str,
    planner1_tasks: List[Dict[str, Any]],
    execution_plan: List[Dict[str, Any]],
    available_tools: Optional[Dict[str, List[str]]] = None,
    *,
    model: str = "gpt-4o",
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Call LLM to score Planner 2 output quality.

    Args:
        original_query: User's original query
        planner1_tasks: Tasks from Planner 1
        execution_plan: Planner 2's tool selections
        available_tools: Optional dict of agent -> tools (auto-built if None)
        model: LLM model to use
        max_retries: Number of retry attempts

    Returns:
        Dict with LLM scores (defaults on failure)
    """
    import json
    import time
    import logging
    
    eval_logger = logging.getLogger(__name__)

    # Build available_tools from AGENT_TOOL_MAP if not provided
    if available_tools is None:
        available_tools = dict(ToolLoader.AGENT_TOOL_MAP)

    prompt = _build_planner2_llm_eval_prompt(
        original_query, planner1_tasks, execution_plan, available_tools
    )

    default_scores = {
        "tool_selection_accuracy": 0.0,
        "tool_coverage_score": 0.0,
        "param_completeness_score": 0.0,
        "param_precision_score": 0.0,
        "tool_ordering_score": 0.0,
        "intent_preservation_score": 0.0,
        "silent_drop_count": 10,
        "over_selection_count": 10,
        "misselection_severity": "severe",
    }

    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError:
        eval_logger.warning("OpenAI not installed - returning default LLM scores")
        return default_scores

    # Check if we have any tools in the execution plan
    total_tools = sum(len(t.get("tools", [])) for t in execution_plan)
    if total_tools == 0:
        eval_logger.warning(
            "No tools in execution_plan - LLM eval will score poorly. "
            "Tasks: %d, query: %s...",
            len(execution_plan),
            original_query[:50]
        )

    client = OpenAI()
    attempt = 0
    delay = 1.0
    last_error = None

    while attempt < max_retries:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=500,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content or ""
            text = content.strip()

            if text.startswith("```"):
                parts = text.split("```")
                if len(parts) >= 2:
                    text = parts[1]
                    if text.lstrip().startswith("json"):
                        text = text.lstrip()[4:]
                text = text.strip()

            scores = json.loads(text)
            if isinstance(scores, dict):
                # Merge with defaults
                for key, default_val in default_scores.items():
                    if key not in scores:
                        scores[key] = default_val
                eval_logger.debug(
                    "LLM eval success: tool_sel=%.2f, coverage=%.2f, param_comp=%.2f",
                    scores.get("tool_selection_accuracy", 0),
                    scores.get("tool_coverage_score", 0),
                    scores.get("param_completeness_score", 0),
                )
                return scores
            else:
                eval_logger.warning("LLM eval returned non-dict: %s", type(scores))

        except json.JSONDecodeError as exc:
            last_error = f"JSON parse error: {exc}"
            eval_logger.warning("LLM eval JSON parse failed (attempt %d): %s", attempt + 1, exc)
        except Exception as exc:
            last_error = str(exc)
            eval_logger.warning("LLM eval failed (attempt %d): %s", attempt + 1, exc)
        
        attempt += 1
        if attempt < max_retries:
            time.sleep(delay)
            delay *= 2

    eval_logger.warning(
        "LLM eval exhausted retries (%d), returning defaults. Last error: %s",
        max_retries,
        last_error
    )
    return default_scores


def combine_planner2_scores(
    struct: Planner2StructuralMetrics,
    llm_scores: Dict[str, Any],
    *,
    clamp: bool = True,
) -> float:
    """Combine structural and LLM scores into a single Planner 2 reward.

    Args:
        struct: Structural metrics from compute_planner2_structural_metrics
        llm_scores: LLM evaluation scores
        clamp: Whether to clamp result to [0, 1]

    Returns:
        Combined score in [0, 1] (if clamped)
    """

    def _get(name: str, default: float) -> float:
        value = llm_scores.get(name, default)
        if isinstance(value, (int, float)):
            return float(value)
        return default

    # Extract LLM scores
    tool_selection = _get("tool_selection_accuracy", 0.0)
    tool_coverage = _get("tool_coverage_score", 0.0)
    param_complete = _get("param_completeness_score", 0.0)
    param_precision = _get("param_precision_score", 0.0)
    tool_ordering = _get("tool_ordering_score", 0.0)
    intent_preserve = _get("intent_preservation_score", 0.0)
    silent_drops = int(llm_scores.get("silent_drop_count", 0) or 0)
    over_selection = int(llm_scores.get("over_selection_count", 0) or 0)
    misselection = str(llm_scores.get("misselection_severity", "none") or "none").lower()

    # Base score: weighted sum of semantic metrics
    # Weights emphasize tool selection and parameter quality
    base = (
        0.25 * tool_selection
        + 0.20 * tool_coverage
        + 0.20 * param_complete
        + 0.10 * param_precision
        + 0.10 * tool_ordering
        + 0.15 * intent_preserve
    )

    # Penalties from structural metrics
    penalty = 0.0

    # Schema validity penalty (Planner 2 output format)
    if not struct.schema_valid:
        penalty += 0.25  # Major penalty - malformed output can't be used
    elif struct.schema_validity_rate < 1.0:
        penalty += (1 - struct.schema_validity_rate) * 0.20

    # Tool selection validity penalty
    if not struct.tool_selection_valid:
        penalty += 0.15

    # Tool parameter validity penalty
    if not struct.tool_params_valid:
        penalty += 0.20

    # Tasks with no tools penalty (unless acceptable)
    if struct.tasks_with_no_tools > 0:
        penalty += 0.05 * struct.tasks_with_no_tools

    # Low validity rates
    if struct.tool_validity_rate < 0.8:
        penalty += (1 - struct.tool_validity_rate) * 0.15

    if struct.param_validity_rate < 0.8:
        penalty += (1 - struct.param_validity_rate) * 0.20

    # LLM-identified issues
    penalty += 0.03 * max(silent_drops, 0)
    penalty += 0.02 * max(over_selection, 0)

    if misselection == "minor":
        penalty += 0.05
    elif misselection == "moderate":
        penalty += 0.15
    elif misselection == "severe":
        penalty += 0.30

    score = base - penalty

    if clamp:
        return max(0.0, min(1.0, score))

    return score


def eval_planner2_output(
    original_query: str,
    planner1_tasks: List[Dict[str, Any]],
    execution_plan: List[Dict[str, Any]],
    *,
    model: str = "gpt-4o",
) -> float:
    """End-to-end Planner 2 evaluation for GEPA optimization.

    Args:
        original_query: User's original query
        planner1_tasks: Tasks from Planner 1 (with agent assignments)
        execution_plan: Planner 2's output (tool selections and parameters)
        model: LLM model for semantic evaluation

    Returns:
        Combined score in [0, 1]
    """
    try:
        # Compute structural metrics
        struct = compute_planner2_structural_metrics(execution_plan)

        # Get LLM evaluation
        llm_scores = call_planner2_llm_evaluator(
            original_query=original_query,
            planner1_tasks=planner1_tasks,
            execution_plan=execution_plan,
            model=model,
        )

        # Combine into final score
        return combine_planner2_scores(struct, llm_scores)

    except Exception:
        return 0.0


__all__ = [
    "Planner2StructuralMetrics",
    "compute_planner2_structural_metrics",
    "validate_tool_params",
    "call_planner2_llm_evaluator",
    "combine_planner2_scores",
    "eval_planner2_output",
]

