"""Offline DSPy/GEPA optimisation harness for Planner 2 (Tool Selection).

This script optimizes the Planner 2 prompt which handles:
- Tool selection for each task
- Parameter mapping from agent_params to tool_params
- Tool execution ordering

It uses planner2_metrics.py for evaluation:
- Schema validity (task_id, agent, tools, tool_params structure)
- Tool selection validity (tools in AGENT_TOOL_MAP)
- Tool parameter validity (per-tool validation)
- LLM-as-judge scoring (coverage, selection accuracy, ordering)

Usage:
    python scripts/helpers/planner2_gepa_opt.py --wandb-project planner2-gepa
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
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_api_key
from src.agents.orchestrator_agent.tool_loader import ToolLoader
from src.agents.orchestrator_agent.planner2_metrics import (
    Planner2StructuralMetrics,
    compute_planner2_structural_metrics,
    call_planner2_llm_evaluator,
    combine_planner2_scores,
)
from src.mcp.taskmaster_client import PLANNER2_PROMPT_CORE

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


def _ensure_wandb_dir() -> None:
    """Ensure WANDB_DIR points to a local dir."""
    if os.environ.get("WANDB_DIR"):
        return
    try:
        home_dir = Path.home()
        local_dir = home_dir / "wandb_planner2_gepa_logs"
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
    
    resolved_project = project or os.environ.get("WANDB_PROJECT", "planner2_gepa_opt")
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
                "planner_stage": 2,
            },
        )
        logger.info("Initialised W&B run (project=%s, name=%s)", resolved_project, resolved_name)
        return run
    except Exception as exc:
        logger.warning("Failed to init wandb: %s", exc)
        return None
    finally:
        _os_path.relpath = _orig_relpath


# ---------------------------------------------------------------------------
# Planner 2 Training Examples
# ---------------------------------------------------------------------------


def _build_available_tools() -> Dict[str, List[str]]:
    """Build available tools dict from AGENT_TOOL_MAP."""
    return dict(ToolLoader.AGENT_TOOL_MAP)


def _generate_planner1_task(
    task_id: str,
    description: str,
    agent: str,
    agent_params: Dict[str, Any],
    dependencies: List[str] = None,
) -> Dict[str, Any]:
    """Generate a synthetic Planner 1 task."""
    return {
        "task_id": task_id,
        "description": description,
        "agent": agent,
        "agent_params": agent_params or {},
        "dependencies": dependencies or [],
    }


def _default_planner2_trainset() -> List[Dict[str, Any]]:
    """Default training examples for Planner 2 optimization.
    
    Each example has:
    - original_query: The user's original query
    - planner1_tasks: Tasks from Planner 1 with agent assignments
    - available_tools: Tools available per agent
    """
    available_tools = _build_available_tools()
    
    examples = [
    # Example 1: Simple market data query
        {
            "original_query": "Get ZN treasury futures prices for January 15, 2024",
        "planner1_tasks": [
            _generate_planner1_task(
                "task_1",
                    "Query market_data for ZN treasury futures on 2024-01-15",
                "market_data_agent",
                {"template": "by_symbol_and_date", "params": {"symbol_pattern": "%ZN%", "file_date": "2024-01-15"}}
            )
        ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["run_query"],
                        "tool_params": {
                            "run_query": {
                                "template": "by_symbol_and_date",
                                "symbol_pattern": "%ZN%",
                                "file_date": "2024-01-15"
                            }
                        },
                        "reasoning": "run_query is the appropriate tool for querying market data with symbol and date filters"
                    }
                ]
            },
        },
        # Example 2: Polymarket search
        {
            "original_query": "What are the current Bitcoin prediction markets?",
        "planner1_tasks": [
            _generate_planner1_task(
                "task_1",
                    "Search Polymarket for Bitcoin prediction markets",
                "polymarket_agent",
                    {"query": "Bitcoin predictions", "limit": 10}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["search_polymarket"],
                        "tool_params": {
                            "search_polymarket": {
                                "query": "Bitcoin predictions",
                                "limit": 10
                            }
                        },
                        "reasoning": "search_polymarket is used to search for prediction markets matching a query"
                    }
                ]
            },
        },
        # Example 3: Event data query
        {
            "original_query": "Get historical Non-Farm Payrolls data for the US",
        "planner1_tasks": [
            _generate_planner1_task(
                "task_1",
                    "Query historical NFP events for US",
                "eventdata_puller_agent",
                {"event_name": "Non-Farm Payrolls", "country": "US", "lookback_days": 365}
            )
        ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["query_event_history"],
                        "tool_params": {
                            "query_event_history": {
                                "event_name": "Non-Farm Payrolls",
                                "country": "US",
                                "lookback_days": 365
                            }
                        },
                        "reasoning": "query_event_history retrieves historical economic event data"
                    }
                ]
            },
        },
        # Example 4: Multi-tool event query
        {
            "original_query": "Find CPI data and related events around CPI releases",
        "planner1_tasks": [
            _generate_planner1_task(
                "task_1",
                    "Get CPI historical data and find correlated events",
                "eventdata_puller_agent",
                    {"event_name": "CPI", "country": "US", "window_hours": 6}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["query_event_history", "get_correlated_events"],
                        "tool_params": {
                            "query_event_history": {
                                "event_name": "CPI",
                                "country": "US"
                            },
                            "get_correlated_events": {
                                "event_name": "CPI",
                                "country": "US",
                                "window_hours": 6
                            }
                        },
                        "reasoning": "Two tools needed: query_event_history for CPI data and get_correlated_events for related events"
                    }
                ]
            },
        },
        # Example 5: Complex multi-agent query
        {
            "original_query": "Compare Bitcoin prediction market sentiment with treasury futures prices",
        "planner1_tasks": [
            _generate_planner1_task(
                "task_1",
                    "Search Bitcoin prediction markets",
                    "polymarket_agent",
                    {"query": "Bitcoin price predictions", "limit": 5}
                ),
                _generate_planner1_task(
                    "task_2",
                    "Query treasury futures market data",
                    "market_data_agent",
                    {"template": "by_symbol", "params": {"symbol_pattern": "%ZN%"}},
                    dependencies=[]
                ),
                _generate_planner1_task(
                    "task_3",
                    "Consolidate and compare the data",
                "runner_agent",
                {},
                    dependencies=["task_1", "task_2"]
                ),
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["search_polymarket_with_history"],
                        "tool_params": {
                            "search_polymarket_with_history": {
                                "query": "Bitcoin price predictions",
                                "limit": 5
                            }
                        },
                        "reasoning": "search_polymarket_with_history for sentiment analysis on prediction markets"
                    },
                    {
                        "task_id": "task_2",
                        "tools": ["run_query"],
                        "tool_params": {
                            "run_query": {
                                "template": "by_symbol",
                                "symbol_pattern": "%ZN%"
                            }
                        },
                        "reasoning": "run_query for treasury futures market data"
                    },
                    {
                        "task_id": "task_3",
                        "tools": ["build_runner_answer"],
                        "tool_params": {
                            "build_runner_answer": {}
                        },
                        "reasoning": "build_runner_answer to consolidate results from task_1 and task_2"
                    }
                ]
            },
        },
        # Example 6: Date-based market data
        {
            "original_query": "Show me all options prices from July 21, 2025",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Query market_data for all prices on 2025-07-21",
                    "market_data_agent",
                    {"template": "by_date", "params": {"file_date": "2025-07-21"}}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["run_query"],
                        "tool_params": {
                            "run_query": {
                                "template": "by_date",
                                "file_date": "2025-07-21"
                            }
                        },
                        "reasoning": "run_query with by_date template for all prices on a specific date"
                    }
                ]
            },
        },
        # Example 7: Polymarket with historical comparison
        {
            "original_query": "How have election prediction markets changed in the last week?",
        "planner1_tasks": [
            _generate_planner1_task(
                "task_1",
                    "Search election markets with historical comparison",
                "polymarket_agent",
                    {"query": "US election 2024", "limit": 10, "days_back": 7}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["search_polymarket_with_history"],
                        "tool_params": {
                            "search_polymarket_with_history": {
                                "query": "US election 2024",
                                "limit": 10,
                                "days_back": 7
                            }
                        },
                        "reasoning": "search_polymarket_with_history for historical comparison of prediction markets"
                    }
                ]
            },
        },
        # Example 8: Event search
        {
            "original_query": "Find all high importance US economic events",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Search for high importance US economic events",
                    "eventdata_puller_agent",
                    {"country": "US", "importance": "high"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["search_events"],
                        "tool_params": {
                            "search_events": {
                                "country": "US",
                                "importance": "high"
                            }
                        },
                        "reasoning": "search_events for finding events by country and importance level"
                    }
                ]
            },
        },
        # Example 9: Symbol pattern query
        {
            "original_query": "Get all call options data",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Query market_data for call options",
                    "market_data_agent",
                    {"template": "by_symbol", "params": {"symbol_pattern": "%.C"}}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["run_query"],
                        "tool_params": {
                            "run_query": {
                                "template": "by_symbol",
                                "symbol_pattern": "%.C"
                            }
                        },
                        "reasoning": "run_query with symbol pattern to filter call options"
                    }
                ]
            },
        },
        # Example 10: Runner consolidation
        {
            "original_query": "Summarize NFP impact on treasury markets",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Get NFP historical data",
                    "eventdata_puller_agent",
                    {"event_name": "Non-Farm Payrolls", "country": "US", "lookback_days": 180}
            ),
            _generate_planner1_task(
                "task_2",
                    "Get treasury futures data",
                "market_data_agent",
                {"template": "by_symbol", "params": {"symbol_pattern": "%ZN%"}}
            ),
            _generate_planner1_task(
                "task_3",
                    "Build final answer summarizing NFP impact",
                    "runner_agent",
                    {},
                    dependencies=["task_1", "task_2"]
                ),
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["query_event_history"],
                        "tool_params": {
                            "query_event_history": {
                                "event_name": "Non-Farm Payrolls",
                                "country": "US",
                                "lookback_days": 180
                            }
                        },
                        "reasoning": "query_event_history for NFP historical data"
                    },
                    {
                        "task_id": "task_2",
                        "tools": ["run_query"],
                        "tool_params": {
                            "run_query": {
                                "template": "by_symbol",
                                "symbol_pattern": "%ZN%"
                            }
                        },
                        "reasoning": "run_query for treasury futures data"
                    },
                    {
                        "task_id": "task_3",
                        "tools": ["build_runner_answer"],
                        "tool_params": {
                            "build_runner_answer": {}
                        },
                        "reasoning": "build_runner_answer to summarize results"
                    }
                ]
            },
        },
        # =====================================================================
        # EVENTDATA_PULLER_AGENT EXAMPLES (NEW)
        # =====================================================================
        # Example 11: Fetch economic calendar
        {
            "original_query": "Get all economic events for this week",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Fetch economic calendar for current week",
                    "eventdata_puller_agent",
                    {"start_date": "2024-12-01", "end_date": "2024-12-07", "country": "US"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["fetch_economic_calendar"],
                        "tool_params": {
                            "fetch_economic_calendar": {
                                "start_date": "2024-12-01",
                                "end_date": "2024-12-07",
                                "country": "US"
                            }
                        },
                        "reasoning": "fetch_economic_calendar retrieves scheduled economic events for a date range"
                    }
                ]
            },
        },
        # Example 12: Search events by importance
        {
            "original_query": "Find all medium importance European events",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Search for medium importance events in Europe",
                    "eventdata_puller_agent",
                    {"country": "EU", "importance": "medium"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["search_events"],
                        "tool_params": {
                            "search_events": {
                                "country": "EU",
                                "importance": "medium"
                            }
                        },
                        "reasoning": "search_events filters events by country and importance level"
                    }
                ]
            },
        },
        # Example 14: Query GDP history
        {
            "original_query": "Show me US GDP releases from the last 2 years",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Query GDP event history for US",
                    "eventdata_puller_agent",
                    {"event_name": "GDP", "country": "US", "lookback_days": 730}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["query_event_history"],
                        "tool_params": {
                            "query_event_history": {
                                "event_name": "GDP",
                                "country": "US",
                                "lookback_days": 730
                            }
                        },
                        "reasoning": "query_event_history retrieves historical releases of a specific event"
                    }
                ]
            },
        },
        # Example 15: Find all instances of an event
        {
            "original_query": "Find all historical CPI releases with their actual vs expected values",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Get all CPI instances with actual and expected values",
                    "eventdata_puller_agent",
                    {"event_name": "CPI", "country": "US", "include_values": True}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["find_events_for_all_instances"],
                        "tool_params": {
                            "find_events_for_all_instances": {
                                "event_name": "CPI",
                                "country": "US",
                                "include_values": True
                            }
                        },
                        "reasoning": "find_events_for_all_instances retrieves complete history of an event type"
                    }
                ]
            },
        },
        # Example 16: Start live event streaming
        {
            "original_query": "Start monitoring live economic events",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Initialize live event stream",
                    "eventdata_puller_agent",
                    {"stream_id": "live_events_1", "filter_importance": "high"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["start_event_stream"],
                        "tool_params": {
                            "start_event_stream": {
                                "stream_id": "live_events_1",
                                "filter_importance": "high"
                            }
                        },
                        "reasoning": "start_event_stream initializes a WebSocket stream for live economic events"
                    }
                ]
            },
        },
        # Example 17: Get live events
        {
            "original_query": "What economic events are happening right now?",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Fetch current live events from active stream",
                    "eventdata_puller_agent",
                    {"stream_id": "live_events_1"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["get_live_events"],
                        "tool_params": {
                            "get_live_events": {
                                "stream_id": "live_events_1"
                            }
                        },
                        "reasoning": "get_live_events retrieves buffered events from an active stream"
                    }
                ]
            },
        },
        # =====================================================================
        # ANALYTICS_AGENT EXAMPLES (NEW)
        # =====================================================================
        # Example 19: Compute statistics
        {
            "original_query": "Calculate statistics on treasury futures returns",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Compute descriptive statistics on price data",
                    "analytics_agent",
                    {"data_source": "treasury_futures", "metrics": ["mean", "std", "min", "max", "skew", "kurtosis"]}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["compute_statistics"],
                        "tool_params": {
                            "compute_statistics": {
                                "data_source": "treasury_futures",
                                "metrics": ["mean", "std", "min", "max", "skew", "kurtosis"]
                            }
                        },
                        "reasoning": "compute_statistics calculates descriptive statistics on numerical data"
                    }
                ]
            },
        },
        # Example 20: Compute percentile rank
        {
            "original_query": "Where does today's VIX stand compared to historical values?",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Calculate percentile rank of current VIX",
                    "analytics_agent",
                    {"current_value": 18.5, "historical_data": "vix_history", "lookback_days": 252}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["compute_percentile_rank"],
                        "tool_params": {
                            "compute_percentile_rank": {
                                "current_value": 18.5,
                                "historical_data": "vix_history",
                                "lookback_days": 252
                            }
                        },
                        "reasoning": "compute_percentile_rank determines where a value falls in a distribution"
                    }
                ]
            },
        },
        # Example 21: Compare distributions
        {
            "original_query": "Compare return distributions between stocks and bonds",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Compare two return distributions",
                    "analytics_agent",
                    {"distribution_a": "stock_returns", "distribution_b": "bond_returns", "test": "ks_test"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["compare_distributions"],
                        "tool_params": {
                            "compare_distributions": {
                                "distribution_a": "stock_returns",
                                "distribution_b": "bond_returns",
                                "test": "ks_test"
                            }
                        },
                        "reasoning": "compare_distributions performs statistical tests between two distributions"
                    }
                ]
            },
        },
        # Example 22: Compute correlation
        {
            "original_query": "What is the correlation between gold and treasury yields?",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Calculate correlation between gold prices and treasury yields",
                    "analytics_agent",
                    {"series_a": "gold_prices", "series_b": "treasury_10y_yield", "method": "pearson"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["compute_correlation"],
                        "tool_params": {
                            "compute_correlation": {
                                "series_a": "gold_prices",
                                "series_b": "treasury_10y_yield",
                                "method": "pearson"
                            }
                        },
                        "reasoning": "compute_correlation calculates the correlation coefficient between two series"
                    }
                ]
            },
        },
        # Example 23: Generate histogram
        {
            "original_query": "Show me a histogram of daily S&P 500 returns",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Generate histogram of daily returns",
                    "analytics_agent",
                    {"data": "sp500_returns", "bins": 50, "title": "S&P 500 Daily Returns Distribution"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["generate_histogram"],
                        "tool_params": {
                            "generate_histogram": {
                                "data": "sp500_returns",
                                "bins": 50,
                                "title": "S&P 500 Daily Returns Distribution"
                            }
                        },
                        "reasoning": "generate_histogram creates a histogram visualization of the data"
                    }
                ]
            },
        },
        # Example 24: Generate line chart
        {
            "original_query": "Plot the 10-year treasury yield over time",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Create time series line chart of treasury yield",
                    "analytics_agent",
                    {"x_data": "dates", "y_data": "treasury_10y_yield", "title": "10-Year Treasury Yield"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["generate_line_chart"],
                        "tool_params": {
                            "generate_line_chart": {
                                "x_data": "dates",
                                "y_data": "treasury_10y_yield",
                                "title": "10-Year Treasury Yield"
                            }
                        },
                        "reasoning": "generate_line_chart creates a time series visualization"
                    }
                ]
            },
        },
        # Example 25: Generate scatter plot
        {
            "original_query": "Create a scatter plot of inflation vs unemployment",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Generate scatter plot showing Phillips curve relationship",
                    "analytics_agent",
                    {"x_data": "unemployment_rate", "y_data": "inflation_rate", "title": "Phillips Curve"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["generate_scatter_plot"],
                        "tool_params": {
                            "generate_scatter_plot": {
                                "x_data": "unemployment_rate",
                                "y_data": "inflation_rate",
                                "title": "Phillips Curve"
                            }
                        },
                        "reasoning": "generate_scatter_plot visualizes the relationship between two variables"
                    }
                ]
            },
        },
        # Example 26: Compute returns distribution
        {
            "original_query": "Analyze the return distribution of bitcoin",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Compute returns distribution for Bitcoin",
                    "analytics_agent",
                    {"asset": "bitcoin", "period": "daily", "lookback_days": 365}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["compute_returns_distribution"],
                        "tool_params": {
                            "compute_returns_distribution": {
                                "asset": "bitcoin",
                                "period": "daily",
                                "lookback_days": 365
                            }
                        },
                        "reasoning": "compute_returns_distribution analyzes the distribution of asset returns"
                    }
                ]
            },
        },
        # Example 27: Compute volatility distribution
        {
            "original_query": "What is the volatility distribution of treasury futures?",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Calculate volatility distribution",
                    "analytics_agent",
                    {"asset": "treasury_futures", "window": 20, "lookback_days": 252}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["compute_volatility_distribution"],
                        "tool_params": {
                            "compute_volatility_distribution": {
                                "asset": "treasury_futures",
                                "window": 20,
                                "lookback_days": 252
                            }
                        },
                        "reasoning": "compute_volatility_distribution analyzes rolling volatility patterns"
                    }
                ]
            },
        },
        # Example 28: Compute probability matrix
        {
            "original_query": "Create a probability matrix for rate hike scenarios",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Generate probability matrix for Fed rate decisions",
                    "analytics_agent",
                    {"scenarios": ["hike", "hold", "cut"], "data_source": "fed_funds_futures"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["compute_probability_matrix"],
                        "tool_params": {
                            "compute_probability_matrix": {
                                "scenarios": ["hike", "hold", "cut"],
                                "data_source": "fed_funds_futures"
                            }
                        },
                        "reasoning": "compute_probability_matrix creates a matrix of scenario probabilities"
                    }
                ]
            },
        },
        # Example 29: Compute event distribution
        {
            "original_query": "Analyze the distribution of NFP surprise values",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Compute distribution of NFP actual vs expected differences",
                    "analytics_agent",
                    {"event_name": "Non-Farm Payrolls", "country": "US", "metric": "surprise"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["compute_event_distribution"],
                        "tool_params": {
                            "compute_event_distribution": {
                                "event_name": "Non-Farm Payrolls",
                                "country": "US",
                                "metric": "surprise"
                            }
                        },
                        "reasoning": "compute_event_distribution analyzes the distribution of economic event outcomes"
                    }
                ]
            },
        },
        # Example 30: Multi-step analytics pipeline
        {
            "original_query": "Analyze NFP impact on treasury futures with visualization",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Get NFP historical data",
                "eventdata_puller_agent",
                {"event_name": "Non-Farm Payrolls", "country": "US", "lookback_days": 365}
            ),
                _generate_planner1_task(
                    "task_2",
                    "Get treasury futures prices",
                    "market_data_agent",
                    {"template": "by_symbol", "params": {"symbol_pattern": "%ZN%"}},
                    dependencies=[]
                ),
                _generate_planner1_task(
                    "task_3",
                    "Calculate correlation between NFP and treasury prices",
                    "analytics_agent",
                    {"series_a": "nfp_surprise", "series_b": "zn_returns", "method": "pearson"},
                    dependencies=["task_1", "task_2"]
                ),
            _generate_planner1_task(
                "task_4",
                    "Generate scatter plot",
                    "analytics_agent",
                    {"x_data": "nfp_surprise", "y_data": "zn_returns", "title": "NFP Impact on Treasuries"},
                    dependencies=["task_3"]
                ),
                _generate_planner1_task(
                    "task_5",
                    "Summarize findings",
                "runner_agent",
                {},
                    dependencies=["task_3", "task_4"]
                ),
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["query_event_history"],
                        "tool_params": {
                            "query_event_history": {
                                "event_name": "Non-Farm Payrolls",
                                "country": "US",
                                "lookback_days": 365
                            }
                        },
                        "reasoning": "query_event_history for NFP data"
                    },
                    {
                        "task_id": "task_2",
                        "tools": ["run_query"],
                        "tool_params": {
                            "run_query": {
                                "template": "by_symbol",
                                "symbol_pattern": "%ZN%"
                            }
                        },
                        "reasoning": "run_query for treasury futures data"
                    },
                    {
                        "task_id": "task_3",
                        "tools": ["compute_correlation"],
                        "tool_params": {
                            "compute_correlation": {
                                "series_a": "nfp_surprise",
                                "series_b": "zn_returns",
                                "method": "pearson"
                            }
                        },
                        "reasoning": "compute_correlation to measure relationship"
                    },
                    {
                        "task_id": "task_4",
                        "tools": ["generate_scatter_plot"],
                        "tool_params": {
                            "generate_scatter_plot": {
                                "x_data": "nfp_surprise",
                                "y_data": "zn_returns",
                                "title": "NFP Impact on Treasuries"
                            }
                        },
                        "reasoning": "generate_scatter_plot for visualization"
                    },
                    {
                        "task_id": "task_5",
                        "tools": ["build_runner_answer"],
                        "tool_params": {
                            "build_runner_answer": {}
                        },
                        "reasoning": "build_runner_answer to summarize all findings"
                    }
                ]
            },
        },
        # Example 31: Bar chart comparison
        {
            "original_query": "Compare average returns by day of week",
        "planner1_tasks": [
            _generate_planner1_task(
                "task_1",
                    "Generate bar chart of returns by day",
                    "analytics_agent",
                    {"categories": ["Mon", "Tue", "Wed", "Thu", "Fri"], "values": "avg_returns_by_day", "title": "Returns by Day of Week"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["generate_bar_chart"],
                        "tool_params": {
                            "generate_bar_chart": {
                                "categories": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                                "values": "avg_returns_by_day",
                                "title": "Returns by Day of Week"
                            }
                        },
                        "reasoning": "generate_bar_chart for categorical comparisons"
                    }
                ]
            },
        },
        # Example 32: Event stream management
        {
            "original_query": "Check if the event stream is still active and get its status",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Get status of event stream",
                "eventdata_puller_agent",
                    {"stream_id": "live_events_1"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["get_stream_status"],
                        "tool_params": {
                            "get_stream_status": {
                                "stream_id": "live_events_1"
                            }
                        },
                        "reasoning": "get_stream_status checks if a stream is connected and returns metrics"
                    }
                ]
            },
        },
        # Example 33: Stop event stream
        {
            "original_query": "Stop monitoring live events",
        "planner1_tasks": [
            _generate_planner1_task(
                "task_1",
                    "Stop the active event stream",
                    "eventdata_puller_agent",
                    {"stream_id": "live_events_1"}
                )
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["stop_event_stream"],
                        "tool_params": {
                            "stop_event_stream": {
                                "stream_id": "live_events_1"
                            }
                        },
                        "reasoning": "stop_event_stream disconnects from a live WebSocket stream"
                    }
                ]
            },
        },
        # Example 34: Full economic calendar + analytics
        {
            "original_query": "Get next month's economic calendar and analyze event density",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Fetch economic calendar for next month",
                    "eventdata_puller_agent",
                    {"start_date": "2025-01-01", "end_date": "2025-01-31", "country": "US"}
                ),
                _generate_planner1_task(
                    "task_2",
                    "Compute event distribution by day",
                    "analytics_agent",
                    {"event_data": "calendar_events", "group_by": "day"},
                    dependencies=["task_1"]
                ),
                _generate_planner1_task(
                    "task_3",
                    "Generate bar chart of events per day",
                    "analytics_agent",
                    {"data": "events_per_day", "title": "January 2025 Event Density"},
                    dependencies=["task_2"]
                ),
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["fetch_economic_calendar"],
                        "tool_params": {
                            "fetch_economic_calendar": {
                                "start_date": "2025-01-01",
                                "end_date": "2025-01-31",
                                "country": "US"
                            }
                        },
                        "reasoning": "fetch_economic_calendar for scheduled events"
                    },
                    {
                        "task_id": "task_2",
                        "tools": ["compute_event_distribution"],
                        "tool_params": {
                            "compute_event_distribution": {
                                "event_data": "calendar_events",
                                "group_by": "day"
                            }
                        },
                        "reasoning": "compute_event_distribution to analyze event frequency"
                    },
                    {
                        "task_id": "task_3",
                        "tools": ["generate_bar_chart"],
                        "tool_params": {
                            "generate_bar_chart": {
                                "data": "events_per_day",
                                "title": "January 2025 Event Density"
                            }
                        },
                        "reasoning": "generate_bar_chart to visualize event density"
                    }
                ]
            },
        },
        # Example 35: Complex multi-agent with analytics
        {
            "original_query": "Analyze how prediction market sentiment correlates with economic data",
            "planner1_tasks": [
                _generate_planner1_task(
                    "task_1",
                    "Search for economic sentiment prediction markets",
                    "polymarket_agent",
                    {"query": "economic recession inflation", "limit": 10}
                ),
                _generate_planner1_task(
                    "task_2",
                    "Get recent CPI and inflation data",
                    "eventdata_puller_agent",
                    {"event_name": "CPI", "country": "US", "lookback_days": 180},
                    dependencies=[]
                ),
                _generate_planner1_task(
                    "task_3",
                    "Compute correlation between market sentiment and CPI",
                    "analytics_agent",
                    {"series_a": "market_sentiment", "series_b": "cpi_values", "method": "spearman"},
                    dependencies=["task_1", "task_2"]
                ),
                _generate_planner1_task(
                    "task_4",
                    "Generate visualization",
                    "analytics_agent",
                    {"x_data": "dates", "y_data": ["sentiment", "cpi"], "title": "Sentiment vs CPI"},
                    dependencies=["task_3"]
                ),
                _generate_planner1_task(
                    "task_5",
                    "Build comprehensive analysis",
                    "runner_agent",
                    {},
                    dependencies=["task_3", "task_4"]
                ),
            ],
            "available_tools": available_tools,
            "expected_output": {
                "tool_selections": [
                    {
                        "task_id": "task_1",
                        "tools": ["search_polymarket_with_history"],
                        "tool_params": {
                            "search_polymarket_with_history": {
                                "query": "economic recession inflation",
                                "limit": 10
                            }
                        },
                        "reasoning": "search_polymarket_with_history for market sentiment data"
                    },
                    {
                        "task_id": "task_2",
                        "tools": ["query_event_history"],
                        "tool_params": {
                            "query_event_history": {
                                "event_name": "CPI",
                                "country": "US",
                                "lookback_days": 180
                            }
                        },
                        "reasoning": "query_event_history for CPI data"
                    },
                    {
                        "task_id": "task_3",
                        "tools": ["compute_correlation"],
                        "tool_params": {
                            "compute_correlation": {
                                "series_a": "market_sentiment",
                                "series_b": "cpi_values",
                                "method": "spearman"
                            }
                        },
                        "reasoning": "compute_correlation for statistical relationship"
                    },
                    {
                        "task_id": "task_4",
                        "tools": ["generate_line_chart"],
                        "tool_params": {
                            "generate_line_chart": {
                                "x_data": "dates",
                                "y_data": ["sentiment", "cpi"],
                                "title": "Sentiment vs CPI"
                            }
                        },
                        "reasoning": "generate_line_chart for time series comparison"
                    },
                    {
                        "task_id": "task_5",
                        "tools": ["build_runner_answer"],
                        "tool_params": {
                            "build_runner_answer": {}
                        },
                        "reasoning": "build_runner_answer to synthesize findings"
                    }
                ]
            },
        },
    ]
    
    return examples


# ---------------------------------------------------------------------------
# Planner 2 Evaluation
# ---------------------------------------------------------------------------


def _normalize_planner2_output(
    output: Dict[str, Any],
    planner1_tasks: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Normalize various Planner 2 output formats to a standard structure.
    
    Handles these formats:
    1. {"tool_selections": [{"task_id": "task_1", "tools": [...], "tool_params": {...}}]}
    2. {"tool_selections": {"task_1": {"selected_tools": [...], "tool_params": {...}}}}
    3. {"task_1": {"selected_tool": "...", ...}} (flat dict by task_id)
    4. {"task_1": {"tools": [...], "tool_params": {...}}}
    5. [{"task_id": "task_1", ...}] (raw list at top level)
    6. {"selections": [...]} or {"results": [...]} (alternative wrapper keys)
    
    Returns:
        Dict[task_id -> {tools: [...], tool_params: {...}, reasoning: str}]
    """
    normalized: Dict[str, Dict[str, Any]] = {}
    
    def _extract_from_selection(sel: Dict[str, Any], task_id: str = None) -> Optional[Dict[str, Any]]:
        """Extract tools and params from a selection dict."""
        if not isinstance(sel, dict):
            return None
        
        # Get task_id from selection or use provided one
        tid = sel.get("task_id", task_id)
        if not tid:
            return None
        
        # Try multiple possible key names for tools
        tools = (
            sel.get("tools") or 
            sel.get("selected_tools") or 
            sel.get("tool") or
            sel.get("selected_tool")
        )
        
        # Convert to list if single tool
        if isinstance(tools, str):
            tools = [tools]
        elif not isinstance(tools, list):
            tools = []
        
        # Get tool params
        tool_params = sel.get("tool_params", sel.get("params", sel.get("parameters", {})))
        
        # Get reasoning
        reasoning = sel.get("reasoning", sel.get("reason", sel.get("explanation", "")))
        
        return {
            "task_id": tid,
            "tools": tools,
            "tool_params": tool_params if isinstance(tool_params, dict) else {},
            "reasoning": reasoning if isinstance(reasoning, str) else "",
        }
    
    # Handle case where output itself is a list (raw list at top level)
    if isinstance(output, list):
        logger.debug("Normalizing: output is a list with %d items", len(output))
        for sel in output:
            result = _extract_from_selection(sel)
            if result and result.get("task_id"):
                normalized[result["task_id"]] = {
                    "tools": result["tools"],
                    "tool_params": result["tool_params"],
                    "reasoning": result["reasoning"],
                }
        if normalized:
            logger.debug("Normalized %d tasks from list format", len(normalized))
        return normalized
    
    # Try multiple possible wrapper keys
    wrapper_keys = ["tool_selections", "selections", "results", "tasks", "output"]
    tool_selections = None
    used_key = None
    
    for key in wrapper_keys:
        if key in output:
            tool_selections = output[key]
            used_key = key
            logger.debug("Found wrapper key '%s' with type %s", key, type(tool_selections).__name__)
            break
    
    if tool_selections is not None:
        # Case 1: tool_selections is a list
        if isinstance(tool_selections, list):
            logger.debug("Normalizing: %s is a list with %d items", used_key, len(tool_selections))
            for sel in tool_selections:
                result = _extract_from_selection(sel)
                if result and result.get("task_id"):
                    normalized[result["task_id"]] = {
                        "tools": result["tools"],
                        "tool_params": result["tool_params"],
                        "reasoning": result["reasoning"],
                    }
        # Case 2: tool_selections is a dict keyed by task_id
        elif isinstance(tool_selections, dict):
            logger.debug("Normalizing: %s is a dict with keys %s", used_key, list(tool_selections.keys())[:5])
            for task_id, sel in tool_selections.items():
                result = _extract_from_selection(sel, task_id)
                if result:
                    normalized[task_id] = {
                        "tools": result["tools"],
                        "tool_params": result["tool_params"],
                        "reasoning": result["reasoning"],
                    }
    else:
        # Case 3 & 4: Flat dict keyed by task_id (no wrapper)
        # First check if any key looks like a task_id
        task_ids_in_output = [k for k in output.keys() if k.startswith("task_") or k.startswith("task")]
        if task_ids_in_output:
            logger.debug("Normalizing: flat dict with task keys: %s", task_ids_in_output)
        
        for task in planner1_tasks:
            task_id = task.get("task_id")
            if task_id and task_id in output:
                result = _extract_from_selection(output[task_id], task_id)
                if result:
                    normalized[task_id] = {
                        "tools": result["tools"],
                        "tool_params": result["tool_params"],
                        "reasoning": result["reasoning"],
                    }
        
        # Also try without task_ prefix matching
        if not normalized:
            for key, val in output.items():
                if isinstance(val, dict) and ("tool" in val or "tools" in val or "selected" in str(val)):
                    result = _extract_from_selection(val, key)
                    if result:
                        normalized[key] = {
                            "tools": result["tools"],
                            "tool_params": result["tool_params"],
                            "reasoning": result["reasoning"],
                        }
    
    # If we got tools but no tool_params, try to infer params from planner1_tasks
    task_params_map = {t.get("task_id"): t.get("agent_params", {}) for t in planner1_tasks}
    for task_id, sel in normalized.items():
        if not sel.get("tool_params") and sel.get("tools"):
            # Use agent_params from planner1 as tool_params for each tool
            agent_params = task_params_map.get(task_id, {})
            if agent_params:
                sel["tool_params"] = {tool: dict(agent_params) for tool in sel["tools"]}
            else:
                sel["tool_params"] = {tool: {} for tool in sel["tools"]}
    
    if normalized:
        logger.debug(
            "Successfully normalized %d tasks with tools: %s",
            len(normalized),
            {k: len(v.get("tools", [])) for k, v in normalized.items()}
        )
    else:
        logger.debug(
            "Normalization found no tasks. Output keys: %s",
            list(output.keys()) if isinstance(output, dict) else "not a dict"
        )
    
    return normalized


def eval_planner2_gepa_output(
    original_query: str,
    planner1_tasks: List[Dict[str, Any]],
    planner2_output: str,
    *,
    model: str = "gpt-4o",
) -> float:
    """Evaluate Planner 2 output for GEPA optimization.
    
    Args:
        original_query: User's original query
        planner1_tasks: Tasks from Planner 1
        planner2_output: Raw string output from Planner 2 (JSON)
        model: LLM model for semantic evaluation
    
    Returns:
        Combined score in [0, 1]
    """
    try:
        # Parse Planner 2 output (may be double-encoded JSON)
        output = json.loads(planner2_output)
        
        # Handle double-encoded JSON string
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError:
                logger.warning("Planner 2 output is a non-JSON string")
                return 0.0
        
        if not isinstance(output, dict):
            logger.warning("Planner 2 output is not a dict (got %s)", type(output).__name__)
            return 0.0
        
        # Log the raw output for debugging
        logger.debug(
            "Planner 2 raw output type=%s, keys=%s, first100=%s",
            type(output).__name__,
            list(output.keys()) if isinstance(output, dict) else "N/A",
            str(output)[:100]
        )
        
        # Normalize various output formats to a consistent structure
        tool_selections = _normalize_planner2_output(output, planner1_tasks)
        if not tool_selections:
            logger.warning(
                "No tool_selections in Planner 2 output. "
                "Output type=%s, keys=%s. "
                "Expected format: {tool_selections: [...]} or {task_id: {tools: [...], tool_params: {...}}}",
                type(output).__name__,
                list(output.keys()) if isinstance(output, dict) else str(output)[:50]
            )
            return 0.0
        
        # Convert tool_selections to execution_plan format for metrics
        execution_plan = []
        for task in planner1_tasks:
            task_id = task.get("task_id")
            selection = tool_selections.get(task_id, {})
            
            execution_plan.append({
                "task_id": task_id,
                "agent": task.get("agent"),
                "description": task.get("description", ""),
                "tools": selection.get("tools", selection.get("selected_tools", [])),
                "tool_params": selection.get("tool_params", {}),
                "dependencies": task.get("dependencies", []),
                "ai_reasoning": selection.get("reasoning", ""),
            })
        
        # Compute structural metrics
        struct = compute_planner2_structural_metrics(execution_plan)
        
        # Log execution plan summary for debugging
        tools_per_task = [len(t.get("tools", [])) for t in execution_plan]
        total_tools = sum(tools_per_task)
        if total_tools == 0:
            logger.warning(
                "Execution plan has NO tools selected - LLM eval will fail. "
                "Tasks: %d, tools_per_task: %s",
                len(execution_plan),
                tools_per_task
            )
        else:
            logger.debug(
                "Execution plan: %d tasks, %d total tools, tools_per_task: %s",
                len(execution_plan),
                total_tools,
                tools_per_task
            )
        
        # Get LLM evaluation
        llm_scores = call_planner2_llm_evaluator(
            original_query=original_query,
            planner1_tasks=planner1_tasks,
            execution_plan=execution_plan,
            model=model,
        )
        
        # Log if we got default scores (indicates LLM eval failure)
        if llm_scores.get("tool_selection_accuracy", 0) == 0.0 and \
           llm_scores.get("silent_drop_count", 0) == 10:
            logger.warning(
                "LLM evaluator returned default scores - API may be failing. "
                "Scores: %s",
                {k: llm_scores.get(k) for k in ["tool_selection_accuracy", "tool_coverage_score", "param_completeness_score"]}
            )
        
        # Combine scores
        score = combine_planner2_scores(struct, llm_scores)
        
        # Log to wandb if available
        if _WANDB_RUN is not None and wandb is not None:
            try:
                global _WANDB_STEP
                _WANDB_STEP += 1
                struct_dict = {
                    "schema_valid": struct.schema_valid,
                    "schema_validity_rate": struct.schema_validity_rate,
                    "tool_selection_valid": struct.tool_selection_valid,
                    "tool_params_valid": struct.tool_params_valid,
                    "tool_validity_rate": struct.tool_validity_rate,
                    "param_validity_rate": struct.param_validity_rate,
                    "total_tools_selected": struct.total_tools_selected,
                    "tasks_with_tools": struct.tasks_with_tools,
                    "tasks_with_no_tools": struct.tasks_with_no_tools,
                }
                log_payload = {"score/combined": score}
                for key, value in struct_dict.items():
                    log_payload[f"struct/{key}"] = value
                for key, value in llm_scores.items():
                    log_payload[f"llm/{key}"] = value
                
                usage_stats = _get_llm_usage_stats()
                log_payload.update(usage_stats)
                
                wandb.log(log_payload, step=_WANDB_STEP)
            except Exception as log_exc:
                logger.warning("Failed to log to wandb: %s", log_exc)
        
        return score
        
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse Planner 2 output as JSON: %s", exc)
        return 0.0
    except Exception as exc:
        logger.warning("eval_planner2_gepa_output failed: %s", exc)
        return 0.0


# ---------------------------------------------------------------------------
# DSPy / GEPA wiring
# ---------------------------------------------------------------------------


class Planner2Signature(dspy.Signature if dspy else object):
    """DSPy signature for Planner 2 tool selection."""
    
    path_id: str = dspy.InputField(desc="Dependency path identifier") if dspy else ""
    tasks_json: str = dspy.InputField(
        desc="JSON array of tasks from Planner 1. Each task has task_id, agent, description, and agent_params with initial parameters."
    ) if dspy else ""
    available_tools_json: str = dspy.InputField(
        desc="JSON dict mapping agent names to their available tool names"
    ) if dspy else ""
    
    tool_selections_json: str = dspy.OutputField(
        desc='''Strict JSON output with this exact structure:
{
  "tool_selections": [
    {
      "task_id": "task_1",
      "tools": ["tool_name"],
      "tool_params": {
        "tool_name": {
          "param1": "value_from_agent_params",
          "param2": "inferred_value"
        }
      },
      "reasoning": "Why this tool and these parameters"
    }
  ]
}
CRITICAL: tool_params MUST include all parameters needed by the tool, extracted from agent_params in the task.'''
    ) if dspy else ""


class Planner2Program(dspy.Module if dspy else object):
    """DSPy module for Planner 2 tool selection."""
    
    def __init__(self):
        super().__init__()
        if dspy is not None:
            self.plan = dspy.Predict(Planner2Signature)
    
    def forward(
        self,
        path_id: str,
        tasks_json: str,
        available_tools_json: str,
    ) -> str:
        """Generate tool selections for the given tasks."""
        result = self.plan(
            path_id=path_id,
            tasks_json=tasks_json,
            available_tools_json=available_tools_json,
        )
        return result.tool_selections_json


def run_planner2_gepa_optimisation(
    *,
    model_name: str = "gpt-4o",
    max_full_evals: int = 50,
    num_threads: int = 4,
    lm_temperature: float = 0.7,
    lm_max_tokens: int = 2000,
    log_dir: str | None = None,
    random_seed: int | None = 42,
    wandb_project: str | None = None,
    wandb_run_name: str | None = None,
) -> None:
    """Run GEPA optimization for Planner 2 prompt.
    
    Args:
        model_name: LLM model for optimization
        max_full_evals: Maximum GEPA evaluations
        num_threads: Parallel evaluation threads
        lm_temperature: LLM temperature
        lm_max_tokens: Max tokens for LLM
        log_dir: Directory for GEPA logs
        random_seed: Random seed for reproducibility
        wandb_project: W&B project name
        wandb_run_name: W&B run name
    """
    if dspy is None:
        raise RuntimeError("dspy not installed. Run: pip install dspy-ai")
    
    _ensure_openai_api_key()
    
    # Set random seed
    if random_seed is not None:
        random.seed(random_seed)
    
    # Setup log directory
    log_dir_path = Path(log_dir) if log_dir else PROJECT_ROOT / "scripts" / "gepa_logs_planner2"
    log_dir_path.mkdir(parents=True, exist_ok=True)
    
    # Initialize wandb
    wandb_run = _init_wandb(
        project=wandb_project,
        run_name=wandb_run_name,
        model_name=model_name,
        max_full_evals=max_full_evals,
        num_threads=num_threads,
        log_dir=str(log_dir_path),
        random_seed=random_seed,
    )
    _set_wandb_run(wandb_run)
    
    try:
        # Configure DSPy LLM
        lm = dspy.LM(
            model=model_name,
            temperature=lm_temperature,
            max_tokens=lm_max_tokens,
        )
        dspy.configure(lm=lm)
        
        logger.info("Configured DSPy with model=%s", model_name)
        
        # Load or warm-start prompt
        best_prompt_file = log_dir_path / "best_prompt.txt"
        initial_prompt = PLANNER2_PROMPT_CORE
        
        if best_prompt_file.exists():
            try:
                saved_prompt = best_prompt_file.read_text(encoding="utf-8").strip()
                if saved_prompt:
                    initial_prompt = saved_prompt
                    logger.info("Warm-starting from saved prompt in %s", best_prompt_file)
            except Exception as exc:
                logger.warning("Could not load saved prompt: %s", exc)
        
        # Create Planner 2 program
        program = Planner2Program()
        
        # Build training set
        examples = _default_planner2_trainset()
        
        # Convert to DSPy Examples
        trainset = []
        for ex in examples:
            # Use expected_output if provided for few-shot learning
            expected_output = ex.get("expected_output", {})
            tool_selections_json = json.dumps(expected_output) if expected_output else ""
            
            dspy_ex = dspy.Example(
                path_id="path_1",
                tasks_json=json.dumps(ex["planner1_tasks"]),
                available_tools_json=json.dumps(ex["available_tools"]),
                tool_selections_json=tool_selections_json,
            ).with_inputs("path_id", "tasks_json", "available_tools_json")
            # Store original data for evaluation
            dspy_ex._original_query = ex["original_query"]
            dspy_ex._planner1_tasks = ex["planner1_tasks"]
            trainset.append(dspy_ex)
        
        # Split into train/val
        random.shuffle(trainset)
        split_idx = max(1, int(len(trainset) * 0.7))
        train_examples = trainset[:split_idx]
        val_examples = trainset[split_idx:]
        
        logger.info("Training set: %d examples, Validation set: %d examples",
                   len(train_examples), len(val_examples))
        
        # Define GEPA metric (must accept 5 args: gold, pred, trace, pred_name, pred_trace)
        def gepa_metric(
            gold: dspy.Example,
            pred: Any,
            trace: Any = None,
            pred_name: str = None,
            pred_trace: Any = None,
        ) -> float:
            """GEPA metric for Planner 2 evaluation."""
            try:
                # Get raw output
                if hasattr(pred, "tool_selections_json"):
                    raw = pred.tool_selections_json
                elif isinstance(pred, dict):
                    raw = pred.get("tool_selections_json", json.dumps(pred))
                else:
                    raw = str(pred)
                
                # Wrap in expected format if needed
                if not raw or not raw.strip().startswith("{"):
                    raw = json.dumps({"tool_selections": {}})
                
                parsed = json.loads(raw)
                if "tool_selections" not in parsed:
                    parsed = {"tool_selections": parsed}
                raw = json.dumps(parsed)
                
                # Get original data
                original_query = getattr(gold, "_original_query", "")
                planner1_tasks = getattr(gold, "_planner1_tasks", [])
                
                return eval_planner2_gepa_output(
                    original_query=original_query,
                    planner1_tasks=planner1_tasks,
                    planner2_output=raw,
                    model=model_name,
                )
            except Exception as exc:
                logger.warning("gepa_metric failed: %s", exc)
                return 0.0
        
        # Create GEPA teleprompter
        try:
            reflection_lm = dspy.LM(
                model=model_name,
                temperature=lm_temperature,
                max_tokens=lm_max_tokens,
            )
        
            teleprompter = dspy.GEPA(
                metric=gepa_metric,
                reflection_lm=reflection_lm,
                max_full_evals=max_full_evals,
                num_threads=max(2, num_threads),
                log_dir=str(log_dir_path),
            )
        except AttributeError as exc:
            raise RuntimeError(
                "Your DSPy version does not expose dspy.GEPA; "
                "please update dspy-ai"
            ) from exc
        
        logger.info("Starting GEPA compilation for Planner 2 (max_full_evals=%d)...", max_full_evals)
        
        # Run GEPA optimization
        try:
            best_program = teleprompter.compile(
                student=program,
                trainset=train_examples,
                valset=val_examples,
            )
        except AssertionError as exc:
            logger.warning("GEPA state mismatch, clearing and retrying: %s", exc)
            # Clear state except best_prompt.txt
            for path in log_dir_path.iterdir():
                if path.is_file() and path.name == "best_prompt.txt":
                    continue
                if path.is_file():
                    try:
                        path.unlink()
                    except Exception:
                        pass
                elif path.is_dir():
                    try:
                        shutil.rmtree(path)
                    except Exception:
                        pass
            
            teleprompter = dspy.GEPA(
                metric=gepa_metric,
                reflection_lm=reflection_lm,
                max_full_evals=max_full_evals,
                num_threads=max(2, num_threads),
                log_dir=str(log_dir_path),
            )
            best_program = teleprompter.compile(
                student=program,
                trainset=train_examples,
                valset=val_examples,
            )
        
        logger.info("GEPA compilation complete!")
        
        # Save best prompt
        try:
            best_prompt_text = None
            plan = getattr(best_program, "plan", None)
            if plan is not None:
                for attr in ("prompt", "instruction", "template"):
                    candidate = getattr(plan, attr, None)
                    if isinstance(candidate, str) and candidate.strip():
                        best_prompt_text = candidate.strip()
                        break
            
            if not best_prompt_text:
                best_prompt_text = repr(best_program)
            
            best_prompt_file.write_text(best_prompt_text, encoding="utf-8")
            logger.info("Saved best Planner 2 prompt to %s", best_prompt_file)
        except Exception as exc:
            logger.error("Failed to save best prompt: %s", exc)
        
        # Demo: evaluate best program on training set
        logger.info("\n=== Final Evaluation on Training Set ===")
        for example in train_examples[:3]:
            raw = best_program(
                path_id=example.path_id,
                tasks_json=example.tasks_json,
                available_tools_json=example.available_tools_json,
            )
            score = eval_planner2_gepa_output(
                original_query=example._original_query,
                planner1_tasks=example._planner1_tasks,
                planner2_output=raw,
                model=model_name,
            )
            logger.info("Query: %s -> Score: %.3f", example._original_query[:50], score)
        
    finally:
        # Log final usage stats
        usage_stats = _get_llm_usage_stats()
        if usage_stats:
            logger.info(
                "LLM usage: tokens=%.0f (in=%.0f, out=%.0f), requests=%.0f, "
                "window=%.2f min, tokens/min=%.1f",
                usage_stats.get("llm/tokens_total", 0),
                usage_stats.get("llm/tokens_input_total", 0),
                usage_stats.get("llm/tokens_output_total", 0),
                usage_stats.get("llm/requests_total", 0),
                usage_stats.get("llm/window_minutes", 0),
                usage_stats.get("llm/tokens_per_minute", 0),
            )
        
        if wandb_run is not None and wandb is not None:
            try:
                wandb_run.finish()
            except Exception as exc:
                logger.warning("Failed to finish wandb run: %s", exc)
        _set_wandb_run(None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GEPA optimization for Planner 2 (tool selection and parameter mapping)."
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default="gpt-4o",
        help="LLM model for optimization (default: gpt-4o).",
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
        help="GEPA log directory (default: scripts/gepa_logs_planner2).",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Random seed (default: 42).",
    )
    parser.add_argument(
        "--wandb-project",
        type=str,
        default="planner2-gepa-opt",
        help="W&B project name (default: planner2-gepa-opt).",
    )
    parser.add_argument(
        "--wandb-run-name",
        type=str,
        default=None,
        help="W&B run name (auto-generated if not set).",
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    
    run_planner2_gepa_optimisation(
        model_name=args.model_name,
        max_full_evals=args.max_full_evals,
        num_threads=args.num_threads,
        log_dir=args.log_dir,
        random_seed=args.random_seed,
        wandb_project=args.wandb_project,
        wandb_run_name=args.wandb_run_name,
    )
