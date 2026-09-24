"""Offline DSPy/GEPA optimisation harness for Planner 1 (Task Decomposition).

This script optimizes the Planner 1 prompt which handles:
- User query decomposition into discrete tasks
- Agent assignment for each task
- Dependency graph construction (DAG)
- Parameter extraction for each agent

It uses planner_metrics.py for evaluation:
- DAG validity (no cycles, proper structure)
- Schema validity (id, description, dependencies)
- Agent params validity (per-agent validation)
- LLM-as-judge scoring (coverage, granularity, faithfulness)

Usage:
    python scripts/helpers/planner1_gepa_opt.py --wandb-project planner1-gepa
    python scripts/helpers/planner1_gepa_opt.py --max-full-evals 30 --model gpt-4-turbo
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_api_key
from src.agents.orchestrator_agent.planner_metrics import (
    StructuralMetrics,
    compute_structural_metrics,
    combine_structural_and_llm_scores,
    validate_agent_params,
)
from src.agents.orchestrator_agent.config import (
    get_enabled_agent_names,
    get_planner_agent_registry,
    AGENT_CAPABILITIES,
)
from src.mcp.taskmaster_client import PLANNER1_PROMPT_CORE

# Import dspy at module level
try:
    import dspy  # type: ignore[import-not-found]
except ImportError:
    dspy = None  # type: ignore

# Optional Weights & Biases
try:
    import wandb  # type: ignore[import-not-found]
except Exception:
    wandb = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_WANDB_RUN: Any | None = None
_WANDB_STEP: int = 0

# LLM usage tracking
_LLM_USAGE_START_TIME: float | None = None
_LLM_TOTAL_INPUT_TOKENS: int = 0
_LLM_TOTAL_OUTPUT_TOKENS: int = 0
_LLM_TOTAL_REQUESTS: int = 0


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------


def _ensure_wandb_dir() -> None:
    """Ensure WANDB_DIR points to a local dir."""
    if os.environ.get("WANDB_DIR"):
        return
    try:
        home_dir = Path.home()
        local_dir = home_dir / "wandb_planner1_gepa_logs"
        local_dir.mkdir(parents=True, exist_ok=True)
        os.environ["WANDB_DIR"] = str(local_dir)
        logger.info("WANDB_DIR defaulting to %s", local_dir)
    except Exception as exc:
        logger.warning("Could not configure WANDB_DIR: %s", exc)


def _ensure_openai_api_key() -> None:
    """Ensure OPENAI_API_KEY is available."""
    if os.environ.get("OPENAI_API_KEY"):
        return
    try:
        api_key = get_api_key("OPENAI_API_KEY")
    except Exception as exc:
        raise RuntimeError(
            "OPENAI_API_KEY not found. Set env var or add to config/keys.env"
        ) from exc
    os.environ["OPENAI_API_KEY"] = api_key


def _ensure_wandb_api_key() -> None:
    """Ensure WANDB_API_KEY is available for W&B logging."""
    if os.environ.get("WANDB_API_KEY"):
        return
    try:
        api_key = get_api_key("WANDB_API_KEY")
        if api_key:
            os.environ["WANDB_API_KEY"] = api_key
            logger.info("Loaded WANDB_API_KEY from config/keys.env")
    except Exception:
        logger.info("WANDB_API_KEY not found in config/keys.env - wandb may not work")


def _set_wandb_run(run: Any | None) -> None:
    """Set global wandb run handle."""
    global _WANDB_RUN, _WANDB_STEP
    _WANDB_RUN = run
    _WANDB_STEP = 0


def _record_llm_usage(*, input_tokens: int | None, output_tokens: int | None) -> None:
    """Update global LLM usage counters."""
    global _LLM_USAGE_START_TIME, _LLM_TOTAL_INPUT_TOKENS, _LLM_TOTAL_OUTPUT_TOKENS, _LLM_TOTAL_REQUESTS
    
    now = time.time()
    if _LLM_USAGE_START_TIME is None:
        _LLM_USAGE_START_TIME = now
    
    if isinstance(input_tokens, int) and input_tokens > 0:
        _LLM_TOTAL_INPUT_TOKENS += input_tokens
    if isinstance(output_tokens, int) and output_tokens > 0:
        _LLM_TOTAL_OUTPUT_TOKENS += output_tokens
    _LLM_TOTAL_REQUESTS += 1


def _get_llm_usage_stats() -> Dict[str, float]:
    """Return aggregate LLM usage stats."""
    if _LLM_USAGE_START_TIME is None:
        return {}
    
    elapsed_seconds = max(time.time() - _LLM_USAGE_START_TIME, 1e-6)
    elapsed_minutes = elapsed_seconds / 60.0
    
    total_input = float(_LLM_TOTAL_INPUT_TOKENS)
    total_output = float(_LLM_TOTAL_OUTPUT_TOKENS)
    total_tokens = total_input + total_output
    total_requests = float(_LLM_TOTAL_REQUESTS)
    
    return {
        "llm/tokens_input_total": total_input,
        "llm/tokens_output_total": total_output,
        "llm/tokens_total": total_tokens,
        "llm/requests_total": total_requests,
        "llm/tokens_per_minute": total_tokens / elapsed_minutes,
        "llm/requests_per_minute": total_requests / elapsed_minutes,
        "llm/window_minutes": elapsed_minutes,
    }


def _init_wandb(
    *,
    project: str | None,
    run_name: str | None,
    model_name: str,
    max_full_evals: int,
    num_threads: int,
    log_dir: str | None,
    random_seed: int | None,
) -> Any | None:
    """Initialise optional Weights & Biases run."""
    _ensure_wandb_dir()
    _ensure_wandb_api_key()
    
    if wandb is None:
        logger.info("wandb not installed; skipping W&B logging.")
        return None
    
    resolved_project = project or os.environ.get("WANDB_PROJECT", "planner1_gepa_opt")
    resolved_name = run_name or os.environ.get("WANDB_RUN_NAME")
    
    # Patch os.path.relpath to handle Windows cross-drive paths
    import os.path as _os_path
    _orig_relpath = _os_path.relpath
    
    def _safe_relpath(path: Any, start: Any | None = None) -> str:
        try:
            return _orig_relpath(path, start)
        except ValueError as exc:
            if "path is on mount" in str(exc):
                return os.fspath(path)
            raise
    
    _os_path.relpath = _safe_relpath
    
    try:
        run = wandb.init(
            project=resolved_project,
            name=resolved_name,
            config={
                "model_name": model_name,
                "max_full_evals": max_full_evals,
                "num_threads": max(2, num_threads),
                "random_seed": random_seed,
                "gepa_log_dir": log_dir,
                "planner_stage": 1,
            },
        )
        logger.info("Initialised W&B run (project=%s, name=%s)", resolved_project, resolved_name)
        return run
    except Exception as exc:
        logger.warning("Failed to init wandb: %s", exc)
        return None
    finally:
        _os_path.relpath = _orig_relpath


def _log_wandb_step(metrics: Dict[str, Any]) -> None:
    """Log metrics to wandb at current step."""
    global _WANDB_STEP
    if _WANDB_RUN is None or wandb is None:
        return
    try:
        usage_stats = _get_llm_usage_stats()
        combined = {**metrics, **usage_stats}
        wandb.log(combined, step=_WANDB_STEP)
        _WANDB_STEP += 1
    except Exception as exc:
        logger.warning("wandb.log failed: %s", exc)


# ---------------------------------------------------------------------------
# Planner 1 Training Examples
# ---------------------------------------------------------------------------


@dataclass
class Planner1Example:
    """Training example for Planner 1 optimization."""
    query: str
    category: str
    expected_agents: List[str]
    expected_task_count_range: Tuple[int, int]  # (min, max)
    key_requirements: List[str]
    description: str


def _default_planner1_trainset() -> List[Planner1Example]:
    """Default training examples for Planner 1 optimization.
    
    These examples focus on:
    - Candlestick chart generation
    - Correlation analysis
    - Timeseries visualization
    - Price distribution
    - Event distribution
    """
    examples = [
        # ===== CANDLESTICK CHART QUERIES =====
        Planner1Example(
            query="Plot a candlestick chart for TY futures on December 6, 2024",
            category="candlestick",
            expected_agents=["market_data_agent", "analytics_agent", "runner_agent"],
            expected_task_count_range=(3, 5),
            key_requirements=[
                "Must fetch OHLC data (open, high, low, close)",
                "Must use analytics_agent for chart generation",
                "Task description must say 'candlestick chart' not 'line chart'",
            ],
            description="Single-day candlestick chart"
        ),
        Planner1Example(
            query="Show me the TY price chart for the most recent NFP release day",
            category="candlestick",
            expected_agents=["eventdata_puller_agent", "market_data_agent", "analytics_agent", "runner_agent"],
            expected_task_count_range=(4, 6),
            key_requirements=[
                "Must fetch NFP event date first",
                "Must fetch TY market data for that date",
                "Must generate candlestick chart (default for price charts)",
                "Proper task dependencies: event -> market data -> chart",
            ],
            description="Event-day candlestick (requires event + market data)"
        ),
        Planner1Example(
            query="Generate candlestick charts for each December NFP event over the last 5 years",
            category="candlestick",
            expected_agents=["eventdata_puller_agent", "market_data_agent", "analytics_agent", "runner_agent"],
            expected_task_count_range=(4, 6),
            key_requirements=[
                "Must use month_filter=12 for December events",
                "Must use run_multi_date_query for multiple dates",
                "Must use generate_multi_candlestick_charts for multiple charts",
                "Must NOT create N separate chart tasks (unknown count at planning time)",
            ],
            description="Multi-date candlesticks using list-aware tools"
        ),
        # ===== CORRELATION ANALYSIS QUERIES =====
        Planner1Example(
            query="What is the correlation between JOLTS surprise and TY price change in the hour after?",
            category="correlation",
            expected_agents=["eventdata_puller_agent", "analytics_agent", "runner_agent"],
            expected_task_count_range=(3, 5),
            key_requirements=[
                "Must fetch JOLTS events with historical data (limit >= 50)",
                "Must use compute_surprise_price_distribution or compute_correlation",
                "Must specify delta_hrs for time window",
            ],
            description="Surprise-price correlation analysis"
        ),
        Planner1Example(
            query="Analyze the relationship between CPI surprise percentile and treasury futures reaction",
            category="correlation",
            expected_agents=["eventdata_puller_agent", "analytics_agent", "runner_agent"],
            expected_task_count_range=(3, 5),
            key_requirements=[
                "Must fetch CPI events",
                "Must compute surprise percentiles",
                "Must analyze price reaction correlation",
            ],
            description="Percentile-based correlation"
        ),
        # ===== TIMESERIES VISUALIZATION QUERIES =====
        Planner1Example(
            query="Show me the TY price timeseries for November 2024",
            category="timeseries",
            expected_agents=["market_data_agent", "analytics_agent", "runner_agent"],
            expected_task_count_range=(3, 5),
            key_requirements=[
                "Must fetch TY OHLC data for November 2024",
                "Must use candlestick chart as default for timeseries",
                "Must include timestamp column for x-axis",
            ],
            description="Monthly timeseries"
        ),
        Planner1Example(
            query="Plot TY price movement from 1 hour before to 2 hours after the most recent CPI release",
            category="timeseries",
            expected_agents=["eventdata_puller_agent", "market_data_agent", "analytics_agent", "runner_agent"],
            expected_task_count_range=(4, 6),
            key_requirements=[
                "Must fetch CPI event with limit=1 for most recent",
                "Must fetch market data around event time",
                "Must generate timeseries chart",
            ],
            description="Event-anchored timeseries window"
        ),
        # ===== PRICE DISTRIBUTION QUERIES =====
        Planner1Example(
            query="Show me the distribution of TY price returns during US Open session",
            category="price_distribution",
            expected_agents=["analytics_agent", "runner_agent"],
            expected_task_count_range=(2, 4),
            key_requirements=[
                "Must use compute_returns_distribution",
                "Must specify session filter for US Open",
            ],
            description="Session-based returns distribution"
        ),
        Planner1Example(
            query="What is the probability of TY moving 5 bps within 1 hour?",
            category="price_distribution",
            expected_agents=["analytics_agent", "runner_agent"],
            expected_task_count_range=(2, 4),
            key_requirements=[
                "Must use compute_probability_matrix",
                "Must specify target_bps=5 and target_hrs=1",
            ],
            description="Probability matrix query"
        ),
        Planner1Example(
            query="Plot the volatility distribution for TY during London session vs US session",
            category="price_distribution",
            expected_agents=["analytics_agent", "runner_agent"],
            expected_task_count_range=(2, 5),
            key_requirements=[
                "Must use compute_volatility_distribution",
                "Must analyze multiple sessions",
            ],
            description="Comparative session volatility"
        ),
        # ===== EVENT DISTRIBUTION QUERIES =====
        Planner1Example(
            query="What is the distribution of TY price reactions to JOLTS releases by surprise bucket?",
            category="event_distribution",
            expected_agents=["analytics_agent", "runner_agent"],
            expected_task_count_range=(2, 4),
            key_requirements=[
                "Must use compute_surprise_price_distribution",
                "Must specify JOLTS event and TY symbol",
                "Should bucket by surprise magnitude",
            ],
            description="Surprise-bucketed event distribution"
        ),
        Planner1Example(
            query="Plot the price distribution for NFP beat vs miss events",
            category="event_distribution",
            expected_agents=["eventdata_puller_agent", "analytics_agent", "runner_agent"],
            expected_task_count_range=(3, 5),
            key_requirements=[
                "Must fetch NFP events with surprise data",
                "Must categorize as beat/miss",
                "Must plot distribution by category",
            ],
            description="Beat vs miss distribution"
        ),
        Planner1Example(
            query="Analyze how TY reacts to CPI surprises - show the distribution of 1-hour returns by surprise percentile",
            category="event_distribution",
            expected_agents=["eventdata_puller_agent", "analytics_agent", "runner_agent"],
            expected_task_count_range=(3, 5),
            key_requirements=[
                "Must fetch CPI events with limit >= 50",
                "Must compute percentile buckets",
                "Must use delta_hrs=1 for 1-hour window",
            ],
            description="Percentile-bucketed returns distribution"
        ),
        # ===== COMPLEX MULTI-STEP QUERIES =====
        Planner1Example(
            query="Get the most recent June CPI, compute its surprise percentile, and plot TY candlesticks for that day",
            category="complex",
            expected_agents=["eventdata_puller_agent", "market_data_agent", "analytics_agent", "runner_agent"],
            expected_task_count_range=(4, 7),
            key_requirements=[
                "Must use month_filter=6 for June",
                "Must compute percentile rank",
                "Must generate candlestick chart",
                "Proper dependency chain",
            ],
            description="Multi-step: event + percentile + candlestick"
        ),
        Planner1Example(
            query="For each JOLTS release in 2024, show the surprise value and plot the corresponding TY price chart",
            category="complex",
            expected_agents=["eventdata_puller_agent", "market_data_agent", "analytics_agent", "runner_agent"],
            expected_task_count_range=(4, 6),
            key_requirements=[
                "Must use list-aware tools (run_multi_date_query, generate_multi_candlestick_charts)",
                "Must NOT create separate tasks for each event",
                "Iteration happens inside tools, not in task graph",
            ],
            description="Iterative multi-chart with list-aware tools"
        ),
        Planner1Example(
            query="Compare TY price distributions before and after NFP releases to identify if there's a significant effect",
            category="complex",
            expected_agents=["eventdata_puller_agent", "analytics_agent", "runner_agent"],
            expected_task_count_range=(3, 6),
            key_requirements=[
                "Must fetch NFP events",
                "Must use compare_distributions",
                "Must compute effect size (Cohen's d)",
            ],
            description="Before/after distribution comparison"
        ),
    ]
    
    return examples


# ---------------------------------------------------------------------------
# LLM Evaluator for Planner 1
# ---------------------------------------------------------------------------


def call_planner1_llm_evaluator(
    query: str,
    subtasks: List[Dict[str, Any]],
    expected_agents: List[str],
    key_requirements: List[str],
    *,
    model: str = "gpt-4-turbo",
) -> Dict[str, Any]:
    """Call LLM to evaluate Planner 1 output quality.
    
    Args:
        query: Original user query
        subtasks: Generated task graph
        expected_agents: Agents expected to be used
        key_requirements: Key requirements that must be met
        model: OpenAI model to use
        
    Returns:
        LLM evaluation scores
    """
    _ensure_openai_api_key()
    
    try:
        from openai import OpenAI
        client = OpenAI()
    except ImportError:
        logger.warning("openai not installed; returning default scores")
        return _default_llm_scores()
    
    # Build evaluation prompt
    agent_registry = get_planner_agent_registry()
    agent_info = json.dumps(agent_registry, indent=2)
    
    eval_prompt = f"""You are an expert evaluator for AI planning systems. Evaluate the following task decomposition.

## Original Query
{query}

## Generated Task Graph
{json.dumps(subtasks, indent=2)}

## Available Agents
{agent_info}

## Expected Agents
{json.dumps(expected_agents)}

## Key Requirements
{json.dumps(key_requirements)}

Evaluate the task graph on these dimensions (0.0 to 1.0):

1. **coverage_score**: Does the plan cover all aspects of the query? (0=misses key parts, 1=complete)
2. **granularity_score**: Is the decomposition appropriately granular? (0=too coarse or too fine, 1=optimal)
3. **dependency_recall**: Are all necessary dependencies captured? (0=missing deps, 1=complete)
4. **dependency_precision**: Are dependencies accurate (no unnecessary deps)? (0=many false deps, 1=precise)
5. **task_sufficiency_score**: Are there enough tasks to fulfill the query? (0=insufficient, 1=sufficient)
6. **task_precision_score**: No redundant or unnecessary tasks? (0=many redundant, 1=precise)
7. **faithfulness_score**: Does the plan faithfully represent the query intent? (0=misinterprets, 1=faithful)
8. **agent_mapping_accuracy**: Are tasks assigned to the correct agents? (0=wrong agents, 1=correct)
9. **candlestick_default_compliance**: When user asks for "chart/plot/timeseries" without specifying line, does task description say "candlestick"? (0=uses line, 1=uses candlestick)
10. **list_aware_tool_compliance**: For "for each X" queries, does plan use list-aware tools instead of multiple tasks? (0=multiple tasks, 1=uses list-aware)

Also identify:
- **silent_drop_count**: Number of query requirements silently dropped (integer)
- **misallocation_severity**: "none", "minor", "moderate", or "severe"
- **requirements_met**: List of which key requirements are met
- **requirements_missed**: List of which key requirements are missed

Return ONLY a JSON object with these fields. No explanations."""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": eval_prompt}],
            temperature=0.1,
            max_tokens=1000,
        )
        
        content = response.choices[0].message.content.strip()
        
        # Track usage
        if response.usage:
            _record_llm_usage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )
        
        # Parse JSON
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        
        result = json.loads(content)
        return result
        
    except Exception as exc:
        logger.warning("LLM evaluator failed: %s", exc)
        return _default_llm_scores()


def _default_llm_scores() -> Dict[str, Any]:
    """Return default/neutral LLM scores."""
    return {
        "coverage_score": 0.5,
        "granularity_score": 0.5,
        "dependency_recall": 0.5,
        "dependency_precision": 0.5,
        "task_sufficiency_score": 0.5,
        "task_precision_score": 0.5,
        "faithfulness_score": 0.5,
        "agent_mapping_accuracy": 0.5,
        "candlestick_default_compliance": 0.5,
        "list_aware_tool_compliance": 0.5,
        "silent_drop_count": 0,
        "misallocation_severity": "none",
        "requirements_met": [],
        "requirements_missed": [],
    }


def combine_planner1_scores(
    struct: StructuralMetrics,
    llm_scores: Dict[str, Any],
    *,
    clamp: bool = True,
) -> float:
    """Combine structural and LLM scores for Planner 1.
    
    Args:
        struct: Structural metrics from compute_structural_metrics
        llm_scores: LLM evaluation scores
        clamp: Whether to clamp to [0, 1]
        
    Returns:
        Combined score (0 to 1)
    """
    # Use base combination from planner_metrics
    base_score = combine_structural_and_llm_scores(struct, llm_scores, clamp=False)
    
    # Add Planner 1 specific adjustments
    def _get(name: str, default: float) -> float:
        value = llm_scores.get(name, default)
        if isinstance(value, (int, float)):
            return float(value)
        return default
    
    # Bonus for candlestick compliance (important for chart queries)
    candlestick_compliance = _get("candlestick_default_compliance", 0.5)
    list_aware_compliance = _get("list_aware_tool_compliance", 0.5)
    
    # Adjust score
    adjustment = 0.05 * (candlestick_compliance - 0.5) + 0.05 * (list_aware_compliance - 0.5)
    score = base_score + adjustment
    
    if clamp:
        return max(0.0, min(1.0, score))
    return score


# ---------------------------------------------------------------------------
# GEPA Evaluation Function
# ---------------------------------------------------------------------------


def eval_planner1_output(
    query: str,
    subtasks: List[Dict[str, Any]],
    example: Planner1Example,
    *,
    model: str = "gpt-4-turbo",
) -> float:
    """Evaluate Planner 1 output for GEPA optimization.
    
    Args:
        query: Original query
        subtasks: Generated subtasks
        example: Training example with expected values
        model: LLM model for evaluation
        
    Returns:
        Score from 0.0 to 1.0
    """
    try:
        # Compute structural metrics
        struct = compute_structural_metrics(subtasks)
        
        # Check basic validity
        if not struct.dag_ok:
            logger.debug("DAG invalid, returning 0.1")
            return 0.1
        
        # Check task count
        task_count = len(subtasks)
        min_count, max_count = example.expected_task_count_range
        if task_count < min_count:
            count_penalty = 0.2
        elif task_count > max_count:
            count_penalty = 0.1
        else:
            count_penalty = 0.0
        
        # Check expected agents
        used_agents = set()
        for task in subtasks:
            agent = task.get("agent") or task.get("assigned_agent") or task.get("mapped_agent")
            if agent:
                used_agents.add(agent.lower().replace("-", "_"))
        
        expected_set = set(a.lower().replace("-", "_") for a in example.expected_agents)
        agent_coverage = len(used_agents & expected_set) / max(len(expected_set), 1)
        
        # Call LLM evaluator
        llm_scores = call_planner1_llm_evaluator(
            query=query,
            subtasks=subtasks,
            expected_agents=example.expected_agents,
            key_requirements=example.key_requirements,
            model=model,
        )
        
        # Combine scores
        combined = combine_planner1_scores(struct, llm_scores)
        
        # Apply penalties
        final_score = combined - count_penalty + 0.1 * agent_coverage
        
        # Log metrics
        metrics = {
            "eval/query": query[:50],
            "eval/category": example.category,
            "eval/task_count": task_count,
            "eval/agent_coverage": agent_coverage,
            "eval/structural_valid": struct.dag_ok and struct.schema_validity_rate > 0.8,
            "eval/combined_score": combined,
            "eval/final_score": max(0.0, min(1.0, final_score)),
        }
        _log_wandb_step(metrics)
        
        return max(0.0, min(1.0, final_score))
        
    except Exception as exc:
        logger.warning("eval_planner1_output failed: %s", exc)
        return 0.1


# ---------------------------------------------------------------------------
# DSPy Module for Planner 1
# ---------------------------------------------------------------------------


class Planner1Signature(dspy.Signature if dspy else object):
    """DSPy signature for Planner 1."""
    
    query: str = dspy.InputField(desc="User query to decompose") if dspy else ""
    agent_registry: str = dspy.InputField(desc="JSON string of available agents") if dspy else ""
    
    task_graph: str = dspy.OutputField(desc="JSON array of tasks with id, description, agent, params, dependencies") if dspy else ""


class Planner1Module(dspy.Module if dspy else object):
    """DSPy module wrapping Planner 1."""
    
    def __init__(self, prompt_template: str = PLANNER1_PROMPT_CORE):
        if dspy:
            super().__init__()
        self.prompt_template = prompt_template
        self.predictor = dspy.Predict(Planner1Signature) if dspy else None
    
    def forward(self, query: str, agent_registry: str) -> Any:
        """Generate task graph for query."""
        if self.predictor is None:
            raise RuntimeError("DSPy not available")
        
        # Build full prompt
        full_prompt = f"{self.prompt_template}\n\nUser query: {query}\n\nAgent registry:\n{agent_registry}"
        
        # Call predictor
        result = self.predictor(query=full_prompt, agent_registry=agent_registry)
        
        return result


# ---------------------------------------------------------------------------
# GEPA Optimization
# ---------------------------------------------------------------------------


def run_planner1_gepa_optimisation(
    *,
    model_name: str = "gpt-4-turbo",
    max_full_evals: int = 50,
    num_threads: int = 4,
    random_seed: int | None = None,
    log_dir: str | None = None,
    wandb_project: str | None = None,
    wandb_run_name: str | None = None,
) -> Dict[str, Any]:
    """Run GEPA optimization for Planner 1 prompt.
    
    Args:
        model_name: OpenAI model to use
        max_full_evals: Maximum GEPA evaluations
        num_threads: Parallel threads
        random_seed: Random seed for reproducibility
        log_dir: Directory for GEPA logs
        wandb_project: W&B project name
        wandb_run_name: W&B run name
        
    Returns:
        Optimization results with best prompt and scores
    """
    if dspy is None:
        raise RuntimeError("DSPy not installed. Run: pip install dspy-ai")
    
    _ensure_openai_api_key()
    
    # Set random seed
    if random_seed is not None:
        random.seed(random_seed)
    
    # Configure log directory
    log_dir_path = Path(log_dir) if log_dir else PROJECT_ROOT / "scripts" / "gepa_logs_planner1"
    log_dir_path.mkdir(parents=True, exist_ok=True)
    
    # Initialize wandb
    run = _init_wandb(
        project=wandb_project,
        run_name=wandb_run_name,
        model_name=model_name,
        max_full_evals=max_full_evals,
        num_threads=num_threads,
        log_dir=str(log_dir_path),
        random_seed=random_seed,
    )
    _set_wandb_run(run)
    
    # Configure DSPy
    lm = dspy.LM(model=f"openai/{model_name}", temperature=0.2, max_tokens=2000)
    dspy.configure(lm=lm)
    
    # Load training examples
    trainset = _default_planner1_trainset()
    logger.info("Loaded %d training examples", len(trainset))
    
    # Get agent registry
    agent_registry_json = json.dumps(get_planner_agent_registry(), indent=2)
    
    # Build DSPy examples
    dspy_examples = []
    for ex in trainset:
        dspy_examples.append(
            dspy.Example(
                query=ex.query,
                agent_registry=agent_registry_json,
                _example=ex,  # Store for evaluation
            ).with_inputs("query", "agent_registry")
        )
    
    # Define GEPA metric
    def gepa_metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> float:
        """GEPA metric for Planner 1 evaluation."""
        try:
            # Extract example
            example = getattr(gold, "_example", None)
            if example is None:
                return 0.1
            
            # Parse prediction
            task_graph_str = getattr(pred, "task_graph", "[]")
            try:
                subtasks = json.loads(task_graph_str)
                if not isinstance(subtasks, list):
                    subtasks = subtasks.get("subtasks", [])
            except json.JSONDecodeError:
                return 0.1
            
            # Evaluate
            return eval_planner1_output(
                query=example.query,
                subtasks=subtasks,
                example=example,
                model=model_name,
            )
            
        except Exception as exc:
            logger.warning("gepa_metric failed: %s", exc)
            return 0.1
    
    # Create GEPA teleprompter
    # Use a smaller model for reflection to reduce costs
    reflection_lm = dspy.LM(model=f"openai/{model_name}", temperature=0.3, max_tokens=1500)
    
    try:
        teleprompter = dspy.GEPA(
            metric=gepa_metric,
            reflection_lm=reflection_lm,
            max_full_evals=max_full_evals,
            num_threads=max(2, num_threads),
            log_dir=str(log_dir_path),
        )
    except AttributeError:
        logger.error(
            "Your DSPy version does not expose dspy.GEPA; "
            "ensure you have dspy-ai >= 2.5"
        )
        raise
    
    logger.info("Starting GEPA compilation for Planner 1 (max_full_evals=%d)...", max_full_evals)
    
    # Create module
    module = Planner1Module(prompt_template=PLANNER1_PROMPT_CORE)
    
    # Run GEPA optimization
    try:
        optimized = teleprompter.compile(
            student=module,
            trainset=dspy_examples,
        )
    except Exception as exc:
        if "state" in str(exc).lower():
            logger.warning("GEPA state mismatch, clearing and retrying: %s", exc)
            # Clear DSPy state and retry
            dspy.configure(lm=lm)
            teleprompter = dspy.GEPA(
                metric=gepa_metric,
                reflection_lm=reflection_lm,
                max_full_evals=max_full_evals,
                num_threads=max(2, num_threads),
                log_dir=str(log_dir_path),
            )
            optimized = teleprompter.compile(
                student=module,
                trainset=dspy_examples,
            )
        else:
            raise
    
    logger.info("GEPA compilation complete!")
    
    # Evaluate optimized module
    eval_scores = []
    for i, ex in enumerate(trainset):
        try:
            result = optimized(query=ex.query, agent_registry=agent_registry_json)
            subtasks = json.loads(getattr(result, "task_graph", "[]"))
            if not isinstance(subtasks, list):
                subtasks = subtasks.get("subtasks", [])
            score = eval_planner1_output(ex.query, subtasks, ex, model=model_name)
            eval_scores.append(score)
            logger.info("Example %d (%s): score=%.3f", i, ex.category, score)
        except Exception as exc:
            logger.warning("Eval failed for example %d: %s", i, exc)
            eval_scores.append(0.0)
    
    avg_score = sum(eval_scores) / max(len(eval_scores), 1)
    
    # Log final metrics
    final_metrics = {
        "final/avg_score": avg_score,
        "final/num_examples": len(trainset),
        "final/successful_evals": sum(1 for s in eval_scores if s > 0.5),
    }
    _log_wandb_step(final_metrics)
    
    # Save optimized prompt
    output_path = log_dir_path / "optimized_planner1_prompt.txt"
    try:
        optimized_prompt = getattr(optimized, "prompt_template", PLANNER1_PROMPT_CORE)
        output_path.write_text(optimized_prompt)
        logger.info("Saved optimized prompt to %s", output_path)
    except Exception as exc:
        logger.warning("Could not save prompt: %s", exc)
    
    # Finish wandb
    if run is not None:
        try:
            run.finish()
        except Exception:
            pass
    
    return {
        "avg_score": avg_score,
        "eval_scores": eval_scores,
        "num_examples": len(trainset),
        "log_dir": str(log_dir_path),
        "optimized_prompt_path": str(output_path) if output_path.exists() else None,
    }


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for Planner 1 GEPA optimization."""
    parser = argparse.ArgumentParser(
        description="GEPA optimization for Planner 1 (task decomposition and agent assignment)."
    )
    
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4-turbo",
        help="OpenAI model to use (default: gpt-4-turbo).",
    )
    parser.add_argument(
        "--max-full-evals",
        type=int,
        default=50,
        help="Max GEPA evaluations (default: 50).",
    )
    parser.add_argument(
        "--num-threads",
        type=int,
        default=4,
        help="Parallel threads (default: 4).",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default=None,
        help="GEPA log directory (default: scripts/gepa_logs_planner1).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility.",
    )
    parser.add_argument(
        "--wandb-project",
        type=str,
        default="planner1-gepa-opt",
        help="W&B project name (default: planner1-gepa-opt).",
    )
    parser.add_argument(
        "--wandb-run-name",
        type=str,
        default=None,
        help="W&B run name (auto-generated if not provided).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    # Run optimization
    result = run_planner1_gepa_optimisation(
        model_name=args.model,
        max_full_evals=args.max_full_evals,
        num_threads=args.num_threads,
        random_seed=args.seed,
        log_dir=args.log_dir,
        wandb_project=args.wandb_project,
        wandb_run_name=args.wandb_run_name,
    )
    
    print("\n" + "=" * 70)
    print("PLANNER 1 GEPA OPTIMIZATION COMPLETE")
    print("=" * 70)
    print(f"Average Score: {result['avg_score']:.3f}")
    print(f"Examples Evaluated: {result['num_examples']}")
    print(f"Successful (>0.5): {sum(1 for s in result['eval_scores'] if s > 0.5)}")
    print(f"Log Directory: {result['log_dir']}")
    if result.get("optimized_prompt_path"):
        print(f"Optimized Prompt: {result['optimized_prompt_path']}")
    print("=" * 70)


if __name__ == "__main__":
    main()

