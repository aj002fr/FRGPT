"""Offline DSPy/GEPA optimisation harness for the Planner.

This script is **optional tooling** and is not used by the runtime
or tests. It assumes that:

1. You have installed non-stdlib dependencies in your own environment:
   - `dspy` (or `dspy-ai`, depending on release naming)
   - `openai` (or another LLM client you adapt this to)
2. You want to optimise a *prompt-based* planner that maps:
      (query, agent_catalog) -> task graph (JSON table of tasks)
   using the metrics defined in `planner_metrics`.

The core idea:
    - A DSPy module (PlannerProgram) generates a task graph for a query.
    - `eval_planner_output` computes:
        * structural metrics (pure Python, no network)
        * semantic metrics via LLM-as-judge
      and combines them into a scalar reward in [0, 1].
    - GEPA search then tunes the PlannerProgram to maximise this reward.

This file is deliberately self-contained and *does not* import any
orchestrator runtime components except the metrics helper and the
agent capability config.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
from pathlib import Path
from typing import Any, Dict, List
import shutil
import sys
import time

# Ensure project root (containing `src/`) is on sys.path so that
# `from src...` imports work even when this script is executed directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_api_key
from src.agents.orchestrator_agent.config import AGENT_CAPABILITIES
from src.agents.orchestrator_agent.planner_metrics import (
    StructuralMetrics,
    combine_structural_and_llm_scores,
    compute_structural_metrics,
)

# Import dspy at module level for Example class
try:
    import dspy  # type: ignore[import-not-found]
except ImportError:
    dspy = None  # type: ignore

# Optional Weights & Biases logging for GEPA runs.
try:
    import wandb  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    wandb = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_WANDB_RUN: Any | None = None
_WANDB_STEP: int = 0

# Global LLM usage counters for token + request tracking.
_LLM_USAGE_START_TIME: float | None = None
_LLM_TOTAL_INPUT_TOKENS: int = 0
_LLM_TOTAL_OUTPUT_TOKENS: int = 0
_LLM_TOTAL_REQUESTS: int = 0


def _ensure_wandb_dir() -> None:
    """Ensure WANDB_DIR points to a local dir (avoids mixed-mount issues on Windows).

    When this script is run from a mapped or network drive (e.g. Z: pointing to a
    UNC path), wandb can fail with errors like:
        "path is on mount 'Z:', start on mount '\\\\server\\share'".

    To make IDE Run/Debug work without extra setup, we default WANDB_DIR to a
    directory inside the user's home folder if it is not already set.
    """
    if os.environ.get("WANDB_DIR"):
        return

    try:
        home_dir = Path.home()
        local_dir = home_dir / "wandb_gepa_logs"
        local_dir.mkdir(parents=True, exist_ok=True)
        os.environ["WANDB_DIR"] = str(local_dir)
        logger.info("WANDB_DIR not set; defaulting to %s", local_dir)
    except Exception as exc:  # pragma: no cover - best-effort convenience
        logger.warning("Could not configure WANDB_DIR automatically: %s", exc)


def _ensure_openai_api_key() -> None:
    """Ensure OPENAI_API_KEY is available for DSPy / litellm.

    Priority:
    1. Respect existing OPENAI_API_KEY in the environment.
    2. Otherwise, load it from config/keys.env via config.settings.get_api_key
       and set os.environ["OPENAI_API_KEY"].
    """
    if os.environ.get("OPENAI_API_KEY"):
        return
    try:
        api_key = get_api_key("OPENAI_API_KEY")
    except Exception as exc:  # pragma: no cover - optional convenience
        raise RuntimeError(
            "OPENAI_API_KEY not found. Set env var or add OPENAI_API_KEY=... to config/keys.env"
        ) from exc
    os.environ["OPENAI_API_KEY"] = api_key


def _set_wandb_run(run: Any | None) -> None:
    """Set global wandb run handle for metric logging (or disable when None)."""
    global _WANDB_RUN, _WANDB_STEP
    _WANDB_RUN = run
    _WANDB_STEP = 0


def _record_llm_usage(*, input_tokens: int | None, output_tokens: int | None) -> None:
    """Update global counters for LLM token usage and request counts.

    This is best-effort: if usage metadata is missing, we still count the request.
    """
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
    """Return aggregate and per-minute LLM usage stats for logging / wandb.

    Uses the global counters maintained by `_record_llm_usage`.
    """
    if _LLM_USAGE_START_TIME is None:
        return {}

    elapsed_seconds = max(time.time() - _LLM_USAGE_START_TIME, 1e-6)
    elapsed_minutes = elapsed_seconds / 60.0

    total_input = float(_LLM_TOTAL_INPUT_TOKENS)
    total_output = float(_LLM_TOTAL_OUTPUT_TOKENS)
    total_tokens = total_input + total_output
    total_requests = float(_LLM_TOTAL_REQUESTS)

    tokens_per_minute = total_tokens / elapsed_minutes
    requests_per_minute = total_requests / elapsed_minutes

    return {
        "llm/tokens_input_total": total_input,
        "llm/tokens_output_total": total_output,
        "llm/tokens_total": total_tokens,
        "llm/requests_total": total_requests,
        "llm/tokens_per_minute": tokens_per_minute,
        "llm/requests_per_minute": requests_per_minute,
        "llm/window_minutes": elapsed_minutes,
    }


def _init_wandb(
    *,
    project: str | None,
    run_name: str | None,
    model_name: str,
    max_full_evals: int,
    num_threads: int,
    lm_temperature: float,
    lm_max_tokens: int,
    log_dir: str | None,
    random_seed: int | None,
) -> Any | None:
    """Initialise an optional Weights & Biases run for GEPA optimisation.

    If wandb is not installed or initialisation fails, this returns None and the
    rest of the script continues without external logging.
    """
    # Make sure WANDB_DIR points to a stable local directory before initialising,
    # so that running from mapped / network drives in IDE Run & Debug still works.
    _ensure_wandb_dir()

    if wandb is None:  # type: ignore[truthy-function]
        logger.info("wandb not installed; skipping Weights & Biases logging.")
        return None

    resolved_project = project or os.environ.get("WANDB_PROJECT", "planner_gepa_opt")
    resolved_name = run_name or os.environ.get("WANDB_RUN_NAME")

    # Work around Windows mixed-mount issues inside wandb by temporarily
    # patching os.path.relpath so that cross-drive comparisons fall back
    # to absolute paths instead of raising ValueError.
    import os.path as _os_path

    _orig_relpath = _os_path.relpath

    def _safe_relpath(path: Any, start: Any | None = None) -> str:  # type: ignore[override]
        try:
            return _orig_relpath(path, start)  # type: ignore[arg-type]
        except ValueError as exc:
            if "path is on mount" in str(exc):
                return os.fspath(path)
            raise

    _os_path.relpath = _safe_relpath  # type: ignore[assignment]

    try:
        run = wandb.init(  # type: ignore[call-arg]
            project=resolved_project,
            name=resolved_name,
            config={
                "model_name": model_name,
                "max_full_evals": max_full_evals,
                "num_threads": max(2, num_threads),
                "lm_temperature": lm_temperature,
                "lm_max_tokens": lm_max_tokens,
                "random_seed": random_seed,
                "gepa_log_dir": log_dir,
            },
        )
        logger.info(
            "Initialised Weights & Biases run (project=%s, name=%s)",
            resolved_project,
            resolved_name or getattr(run, "name", None),
        )
        return run
    except Exception as exc:  # pragma: no cover - optional telemetry
        logger.warning("Failed to initialise wandb; continuing without logging: %s", exc)
        return None
    finally:
        _os_path.relpath = _orig_relpath  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# LLM-as-judge helper
# ---------------------------------------------------------------------------


def _build_llm_eval_prompt(
    query: str,
    agent_capabilities: Dict[str, Any],
    subtasks: List[Dict[str, Any]],
) -> str:
    """Create a compact evaluation prompt for the LLM-as-judge."""
    capabilities_summary = json.dumps(agent_capabilities, indent=2)
    task_table = json.dumps(subtasks, indent=2)

    return f"""You are an expert evaluator of task planners for a multi-agent
system. Your job is to assess how good a given task graph is at answering
the user's query, using the available agents.

User query:
{query}

Agent capabilities (JSON):
{capabilities_summary}

Proposed task graph (list of task dicts):
{task_table}

You must return a JSON object with **only** these keys:
{{
  "coverage_score": float,           // 0-1, how completely the tasks cover the query
  "granularity_score": float,        // 0-1, single-agent atomic tasks, no over/under split
  "dependency_recall": float,        // 0-1, % of required dependencies present
  "dependency_precision": float,     // 0-1, % of listed dependencies that are correct
  "task_sufficiency_score": float,   // 0-1, do tasks specify enough info for their agents
  "task_precision_score": float,     // 0-1, no irrelevant/noisy details
  "faithfulness_score": float,       // 0-1, no hallucinated requirements, intent preserved
  "agent_mapping_accuracy": float,   // 0-1, correct agent chosen for each task
  "silent_drop_count": int,          // # of query aspects not represented in any task
  "misallocation_severity": str      // "none" | "minor" | "moderate" | "severe"
}}

Guidelines:
- Be strict on coverage: if any important part of the query is missing
  from the tasks, reduce coverage_score and increase silent_drop_count.
- Be strict on agent_mapping_accuracy: do not give credit if a task is
  routed to an agent that cannot actually do that job.
- Do not include any commentary, just the JSON object."""


def call_llm_evaluator(
    query: str,
    agent_capabilities: Dict[str, Any],
    subtasks: List[Dict[str, Any]],
    *,
    model: str = "gpt-5",
    max_retries: int = 5,
    initial_backoff_seconds: float = 1.0,
    backoff_multiplier: float = 2.0,
) -> Dict[str, Any]:
    """Call an external LLM to score planner output.

    This function intentionally lives in the scripts/ namespace so that
    runtime code does not depend on external libraries.

    You must have the `openai` (or compatible) package installed and
    `OPENAI_API_KEY` set in your environment for this to work.

    Returns a dict with all expected score keys. If parsing fails, returns
    default low scores so GEPA can continue rather than crashing.
    """
    try:
        from openai import OpenAI  # type: ignore[import-not-found]
        try:
            # Newer openai versions expose dedicated error classes; we use them
            # opportunistically but always fall back to string matching so this
            # stays compatible across client versions.
            from openai import RateLimitError  # type: ignore[import-not-found]
        except Exception:  # pragma: no cover - best-effort import
            RateLimitError = Exception  # type: ignore[assignment]
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.error("openai library not available: %s", exc)
        raise

    prompt = _build_llm_eval_prompt(query, agent_capabilities, subtasks)
    client = OpenAI()

    # DSPy often uses provider-qualified model names (e.g. "openai/gpt-4.1-mini").
    # The official OpenAI client expects bare model IDs ("gpt-4.1-mini"), so we
    # normalise here for compatibility.
    if "/" in model:
        provider, bare_model = model.split("/", 1)
        openai_model = bare_model or model
    else:
        openai_model = model

    attempt = 0
    delay = initial_backoff_seconds
    scores: Dict[str, Any] = {}

    while True:
        try:
            # For newer OpenAI / reasoning models, `max_tokens` is not supported
            # on the chat.completions API; use `max_completion_tokens` instead.
            # Reasoning models also require the default temperature (1.0).
            # We also request structured JSON via response_format when supported.
            response = client.chat.completions.create(
                model=openai_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=1.0,
                max_completion_tokens=800,
                response_format={"type": "json_object"},
            )

            # Best-effort extraction of token usage metadata for monitoring.
            input_tokens: int | None = None
            output_tokens: int | None = None
            try:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    # New-style client objects expose attributes like input_tokens/output_tokens.
                    input_tokens = getattr(usage, "input_tokens", None) or getattr(
                        usage, "prompt_tokens", None
                    )
                    output_tokens = getattr(usage, "output_tokens", None) or getattr(
                        usage, "completion_tokens", None
                    )
                else:
                    # Fallback for dict-like responses.
                    usage_dict = None
                    try:
                        usage_dict = response["usage"]  # type: ignore[index]
                    except Exception:
                        usage_dict = None
                    if isinstance(usage_dict, dict):
                        input_tokens = usage_dict.get("input_tokens") or usage_dict.get(
                            "prompt_tokens"
                        )
                        output_tokens = usage_dict.get("output_tokens") or usage_dict.get(
                            "completion_tokens"
                        )
            except Exception:
                input_tokens = output_tokens = None

            _record_llm_usage(
                input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            )
            content = response.choices[0].message.content or ""
            text = content.strip()

            if not text:
                logger.warning(
                    "LLM evaluator returned empty content for model %s; using default scores",
                    openai_model,
                )
                scores = {}
                break

            if text.startswith("```"):
                # Handle ```json ... ``` wrappers.
                parts = text.split("```")
                if len(parts) >= 2:
                    text = parts[1]
                    if text.lstrip().startswith("json"):
                        text = text.lstrip()[4:]
                text = text.strip()

            scores = json.loads(text)
            if not isinstance(scores, dict):
                logger.warning("LLM evaluator returned non-dict, using default scores")
                scores = {}
            # Successful call or parse – exit retry loop
            break
        except Exception as exc:  # pragma: no cover - robustness path
            msg = str(exc)
            is_rate_limit = isinstance(exc, RateLimitError) or "rate limit" in msg.lower()
            is_insufficient_quota = "insufficient_quota" in msg or "insufficient quota" in msg.lower()

            if is_insufficient_quota:
                # Quota errors are not transient: retrying will not help and only
                # wastes time / tokens. We log once and fall back to default scores.
                logger.error(
                    "OpenAI reported insufficient_quota while scoring planner output; "
                    "skipping further retries and using default low scores. Raw error: %s",
                    msg,
                )
                scores = {}
                break

            if is_rate_limit and attempt < max_retries:
                attempt += 1
                logger.warning(
                    "Rate limit encountered while calling LLM evaluator (attempt %d/%d). "
                    "Backing off for %.1f seconds before retrying. Error: %s",
                    attempt,
                    max_retries,
                    delay,
                    msg,
                )
                # Exponential backoff with a simple sleep; this is offline tooling
                # so blocking here is acceptable.
                import time

                time.sleep(delay)
                delay *= backoff_multiplier
                continue

            # Any other error (or exhausted retries): log and fall back to defaults.
            logger.warning(
                "Failed to obtain or parse LLM evaluator output after %d attempt(s): %s. "
                "Using default low scores.",
                attempt + 1,
                msg,
            )
            scores = {}
            break

    # Ensure all required keys exist with default values
    default_scores = {
        "coverage_score": 0.0,
        "granularity_score": 0.0,
        "dependency_recall": 0.0,
        "dependency_precision": 0.0,
        "task_sufficiency_score": 0.0,
        "task_precision_score": 0.0,
        "faithfulness_score": 0.0,
        "agent_mapping_accuracy": 0.0,
        "silent_drop_count": 10,
        "misallocation_severity": "severe",
    }
    
    # Merge with defaults (any missing keys get default values)
    for key, default_val in default_scores.items():
        if key not in scores:
            scores[key] = default_val

    return scores


# ---------------------------------------------------------------------------
# Metric wrapper used by GEPA (structural + LLM)
# ---------------------------------------------------------------------------


def eval_planner_output(
    query: str,
    subtasks: List[Dict[str, Any]],
    agent_capabilities: Dict[str, Any],
    *,
    model: str = "gpt-5",
) -> float:
    """End-to-end metric function suitable for optimisation.

    - Computes structural metrics using :mod:`planner_metrics`.
    - Calls an LLM-as-judge to get semantic scores.
    - Combines both into a scalar reward in [0, 1].
    
    Returns 0.0 if any evaluation step fails, so GEPA can continue.
    """
    try:
        struct: StructuralMetrics = compute_structural_metrics(subtasks)

        # Debugging aid: surface problematic planner behaviours during GEPA runs.
        # Log the query when the planner produces too many tasks or invalid schema.
        num_tasks = len(subtasks)
        if num_tasks > 6 or struct.schema_validity_rate == 0.0:
            logger.warning(
                "[GEPA debug] query=%r | num_tasks=%d | schema_validity_rate=%.3f",
                query,
                num_tasks,
                struct.schema_validity_rate,
            )
        llm_scores = call_llm_evaluator(
            query=query,
            agent_capabilities=agent_capabilities,
            subtasks=subtasks,
            model=model,
        )
        score = combine_structural_and_llm_scores(struct, llm_scores)

        if _WANDB_RUN is not None and wandb is not None:
            # Best-effort logging to Weights & Biases; failures should not break optimisation.
            try:
                global _WANDB_STEP
                _WANDB_STEP += 1
                struct_dict = getattr(struct, "__dict__", {})
                log_payload: Dict[str, Any] = {
                    "score/combined": score,
                    "meta/model_name": model,
                }
                for key, value in struct_dict.items():
                    log_payload[f"struct/{key}"] = value
                for key, value in llm_scores.items():
                    log_payload[f"llm/{key}"] = value

                # Attach token and request usage stats so you can see API
                # throughput and approximate spend in the wandb dashboard.
                usage_stats = _get_llm_usage_stats()
                log_payload.update(usage_stats)

                wandb.log(log_payload, step=_WANDB_STEP)  # type: ignore[call-arg]
            except Exception as log_exc:  # pragma: no cover - telemetry path
                logger.warning("Failed to log metrics to wandb: %s", log_exc)

        return score
    except Exception as exc:
        logger.warning("eval_planner_output failed: %s. Returning 0.0", exc)
        return 0.0


# ---------------------------------------------------------------------------
# DSPy / GEPA wiring
# ---------------------------------------------------------------------------


# Use dspy.Example directly instead of custom wrapper class
# This avoids inheritance/copy issues
if dspy is not None:
    PlannerExample = dspy.Example  # type: ignore
else:
    # Fallback if dspy not installed (shouldn't be reached in normal use)
    class PlannerExample:  # type: ignore
        def __init__(self, query: str, agent_catalog_json: str):
            self.query = query
            self.agent_catalog_json = agent_catalog_json


def _build_agent_catalog_json() -> str:
    """Serialise an extended agent capability catalog to JSON.

    We start from the runtime AGENT_CAPABILITIES (currently focused on
    market data + Polymarket) and add richer, planner-facing entries
    for conceptual agents used in queries (web search, messages, events).
    This catalog is **only** used for offline planner evaluation and
    does not affect the live orchestrator.
    """
    catalog = dict(AGENT_CAPABILITIES)

    # Enrich existing agents with clearer planner-facing descriptions.
    market = catalog.get("market_data_agent", {})
    market.setdefault(
        "description",
        "SQL-based MarketData agent for treasury futures/options.",
    )
    market.setdefault(
        "capabilities",
        [
            "Query historical and live prices for treasury futures/options",
            "Filter by symbol, expiry, and date ranges",
            "Compute simple aggregates (e.g., averages, max/min) via SQL",
            "Support intraday vs daily sampling via templates",
        ],
    )
    market.setdefault(
        "inputs",
        {
            "instrument": "treasury future/option symbol or pattern",
            "fields": "price, volume, bid/ask, etc.",
            "date_range": "start/end date or relative window",
            "frequency": "tick, intraday, daily",
        },
    )
    catalog["market_data_agent"] = market

    poly = catalog.get("polymarket_agent", {})
    poly.setdefault(
        "description",
        (
            "Predictive Markets agent for Polymarket: discovers markets, "
            "returns prices/volumes, and can fetch historical market data."
        ),
    )
    poly.setdefault(
        "capabilities",
        [
            "Search Polymarket markets by natural-language query",
            "Return current prices, probabilities, volume, and liquidity",
            "Fetch historical price time series for specific markets",
            "Focus on macro-relevant topics (CPI, NFP, recession, rates)",
        ],
    )
    poly.setdefault(
        "inputs",
        {
            "query": "natural-language description of topic/event",
            "time_frame": "current vs historical window",
            "fields": "prices, volumes, liquidity, etc.",
        },
    )
    catalog["polymarket_agent"] = poly

    # Conceptual agents used for planning / evaluation only.
    catalog.setdefault(
        "web_search_agent",
        {
            "description": (
                "Web Search agent that finds macroeconomic news, "
                "announcements, and commentary related to the query."
            ),
            "capabilities": [
                "Search the web for macro-relevant news and articles",
                "Focus results on economic impact for rates and inflation",
                "Filter by recent vs historical time windows",
            ],
            "inputs": {
                "query": "macro topic + entities (10Y, CPI, NFP, etc.)",
                "time_window": "e.g., past day, week, month, or around an event",
            },
        },
    )

    catalog.setdefault(
        "message_puller_agent",
        {
            "description": (
                "Message Puller agent that retrieves trader messages from "
                "Bloomberg Chat, Telegram, and similar channels."
            ),
            "capabilities": [
                "Filter chats by channels/groups and keyword patterns",
                "Restrict messages to specific time windows around events",
                "Surface qualitative trader sentiment and reactions",
            ],
            "inputs": {
                "channels": "chat rooms or groups to query",
                "keywords": "e.g., CPI, NFP, 10Y, curve, recession",
                "time_window": "start/end timestamps, possibly event-anchored",
            },
        },
    )

    catalog.setdefault(
        "event_data_puller_agent",
        {
            "description": (
                "Event Data Puller agent that provides structured macro "
                "event data (CPI, NFP, FOMC, etc.), including expectations "
                "vs actuals and surprise classification."
            ),
            "capabilities": [
                "List past and upcoming macro events (CPI, NFP, FOMC, etc.)",
                "Return event dates, expected values, and actual values",
                "Compute or label positive/negative surprises",
            ],
            "inputs": {
                "event_type": "CPI, NFP, FOMC, etc.",
                "count_or_range": "e.g., last 3 events, last year",
                "fields": "expected, actual, surprise, etc.",
            },
        },
    )

    return json.dumps(catalog, sort_keys=True)


def _load_queries_from_txt(path: Path) -> List[str]:
    """Load planner queries from a numbered text file, if available.

    Expected format (one per line, but we are forgiving):
        1. Clean query text ...
        1a. noisy     variant ...

    Returns an empty list if the file does not exist or has no valid lines.
    """
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").splitlines()
    queries: List[str] = []
    pattern = re.compile(r"^\s*\d+[a-zA-Z]*\.\s*(.*\S)\s*$")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        m = pattern.match(stripped)
        if m:
            text = m.group(1).strip()
        else:
            text = stripped
        if text:
            queries.append(text)

    return queries


def _default_trainset():
    """Default trainset of diverse planner queries for GEPA optimisation.

    Priority order:
    1. If ``workspace/planner_queries_noisy.txt`` exists, load all (clean + noisy)
       queries from there.
    2. Else, if ``workspace/planner_queries.txt`` exists, load queries from there.
    3. Else, fall back to the small, hard-coded trainset below.

    The queries (from any source) are expected to cover:
    - Single-agent MarketData / PredictiveMarkets / WebSearch / Messages / Events
    - Multi-agent, dependency-heavy queries
    - Queries with components that no current agent can fully execute
    """
    catalog_json = _build_agent_catalog_json()

    # 1) Prefer noisy trainset if available
    base_dir = Path(__file__).resolve().parent.parent
    noisy_path = base_dir / "workspace" / "planner_queries_noisy.txt"
    clean_path = base_dir / "workspace" / "planner_queries.txt"

    queries = _load_queries_from_txt(noisy_path)
    if queries:
        logger.info(
            "Loaded %d planner queries from %s (noisy trainset)",
            len(queries),
            noisy_path,
        )
    else:
        # 2) Fallback to clean planner_queries.txt if present
        queries = _load_queries_from_txt(clean_path)
        if queries:
            logger.info(
                "Loaded %d planner queries from %s (clean trainset)",
                len(queries),
                clean_path,
            )

    if queries:
        # Helper to create DSPy examples with inputs properly set
        def ex(q: str):
            return dspy.Example(
                query=q,
                agent_catalog_json=catalog_json,
            ).with_inputs("query", "agent_catalog_json")

        return [ex(q) for q in queries]

    # 3) Final fallback: small, hard-coded trainset
    logger.info(
        "No external planner_queries*.txt found; using built-in fallback trainset.",
    )

    def ex(q: str):
        return dspy.Example(
            query=q,
            agent_catalog_json=catalog_json,
        ).with_inputs("query", "agent_catalog_json")

    return [
        ex(
            "Show me the intraday 10Y Treasury futures price and volume around the last FOMC meeting (from 2 days before to 3 days after)."
        ),
        ex(
            "Give me the daily closing prices of 2Y, 5Y, and 10Y Treasury futures over the past year and identify the largest 5 drawdowns."
        ),
        ex(
            "On which dates in the past 3 months did 10Y Treasury futures have the highest intraday volatility?"
        ),
        ex(
            "What is Polymarket currently implying about the probability of a US recession in the next 12 months?"
        ),
        ex(
            "How has Polymarket pricing of 'Fed cuts in 2025' changed over the last 6 months?"
        ),
        ex(
            "Find the most liquid Polymarket markets related to US CPI inflation and return their current prices and 7-day volume."
        ),
        ex(
            "Summarize the main macroeconomic news affecting US rates markets over the past week."
        ),
        ex(
            "Find any recent announcements about changes to US Treasury issuance plans and explain their potential macro impact."
        ),
        ex(
            "What were traders on Bloomberg Chat and Telegram saying about today's CPI print between 8am and 3pm New York time?"
        ),
        ex(
            "Pull all messages mentioning '10s–2s curve' from our key Telegram groups over the past week."
        ),
        ex(
            "List the last six US CPI releases with their expected and actual values and whether the surprise was positive or negative."
        ),
        ex(
            "For the past year of NFP releases, show the distribution of surprises (actual minus consensus)."
        ),
        ex(
            "How did the last three CPI releases affect 10Y Treasury futures prices and public opinion on inflation?"
        ),
        ex(
            "Compare how traders reacted on Telegram and in the futures market to yesterday's unexpected NFP surprise, and explain whether news headlines amplified the move."
        ),
        ex(
            "For the last FOMC meeting, compare the move in 2Y and 10Y futures, Polymarket rates markets, and trader chat sentiment."
        ),
        ex(
            "Give me current market pricing and prediction-market sentiment about a US recession in the next 12 months."
        ),
        ex(
            "For US CPI, NFP, and FOMC decisions over the last year, summarize typical futures market reactions and typical Polymarket reactions, separately."
        ),
        ex(
            "Fit a regression model explaining daily changes in 10Y Treasury futures using changes in CPI and NFP over the last 5 years."
        ),
        ex(
            "Backtest a simple strategy that buys 10Y futures when Polymarket assigns more than 70% chance of a US recession."
        ),
        ex(
            "Explain whether 10Y futures are currently cheap or rich versus a fair-value model that you construct."
        ),
    ]


def run_gepa_optimisation(
    *,
    model_name: str = "gpt-5",
    max_full_evals: int = 5,
    num_threads: int = 4,
    log_dir: str | None = None,
    random_seed: int | None = 42,
    wandb_project: str | None = None,
    wandb_run_name: str | None = None,
) -> None:
    """Run a GEPA-style optimisation loop over a small trainset.

    This assumes you have `dspy` (or `dspy-ai`) installed. The exact
    teleprompter name / API may differ slightly between releases; adapt
    as needed.
    """
    try:
        import dspy  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.error("dspy library not available: %s", exc)
        raise

    # Ensure we have an OpenAI API key available for DSPy / litellm.
    _ensure_openai_api_key()

    # Optional seeding so train/validation query split is reproducible by default.
    if random_seed is not None:
        random.seed(random_seed)

    # Configure DSPy to use the specified model as the default LM
    logger.info("=" * 60)
    logger.info("GEPA Optimization Configuration:")
    logger.info("  Model: %s", model_name)
    logger.info("  Max Full Evaluations: %d", max_full_evals)
    logger.info("  Num Threads: %d", max(2, num_threads))
    logger.info("  Log Directory: %s", log_dir or "gepa_logs (auto)")
    logger.info("  Random Seed: %s", random_seed if random_seed is not None else "system default")
    logger.info("=" * 60)

    # Configure LM parameters; OpenAI reasoning models (e.g. gpt-5) require
    # temperature=1.0 or None and max_tokens >= 16000 or None.
    if "gpt-5" in model_name or "openai/gpt-5" in model_name:
        lm_temperature = 1.0
        lm_max_tokens = 16000
    else:
        lm_temperature = 0.7
        lm_max_tokens = 4000

    wandb_run = _init_wandb(
        project=wandb_project,
        run_name=wandb_run_name,
        model_name=model_name,
        max_full_evals=max_full_evals,
        num_threads=num_threads,
        lm_temperature=lm_temperature,
        lm_max_tokens=lm_max_tokens,
        log_dir=log_dir,
        random_seed=random_seed,
    )
    _set_wandb_run(wandb_run)

    try:
        lm = dspy.LM(model=model_name, temperature=lm_temperature, max_tokens=lm_max_tokens)
        dspy.configure(lm=lm)

        # Determine log directory once so it can be reused for warm-start and GEPA.
        log_dir_path = (
            Path(log_dir)
            if log_dir is not None
            else Path(__file__).resolve().parent / "gepa_logs"
        )
        log_dir_path.mkdir(parents=True, exist_ok=True)

        class PlannerSignature(dspy.Signature):
            """You are tasked with generating a series of structured tasks in JSON format
            to answer user queries related to financial markets, focusing on
            macroeconomic events, market data, trader sentiment, and predictive markets.

            1. Query types and domain context:
               - Queries may involve macroeconomic event data (for example CPI, NFP, FOMC),
                 treasury futures and options (for example TY, 2Y, 10Y futures),
                 trader sentiment from chat messages, predictive market probabilities
                 (for example Polymarket), and related news.
               - Users can request historical and current data, comparative analyses,
                 surprises versus expectations, event-driven price moves, sentiment
                 summaries, distributions, and probability estimations.
               - Events such as CPI, NFP, and FOMC are critical anchors for data
                 retrieval and analysis.
               - Instruments like treasury futures (TY), treasury notes (2Y, 10Y),
                 options skew, and swaptions are common data subjects.

            2. Agent capabilities and usage (from `agent_catalog_json`):
               - `event_data_puller_agent`: Retrieves structured macro event data
                 including event dates, expected versus actual values, and surprise
                 labels (positive or negative). Often used to get event dates and
                 metrics spanning user-specified historical ranges.
               - `market_data_agent`: Executes parameterized SQL queries on market data
                 tables. Used to retrieve prices, volumes, bid or ask, and related
                 fields for specified instruments and date windows. Supports filtering
                 by symbol, date ranges, and sampling frequency (tick, intraday, daily).
               - `message_puller_agent`: Retrieves trader chat messages filtered by
                 keyword patterns, channels or groups, and time windows (which can be
                 anchored around events). Used for sentiment and qualitative analysis.
               - `polymarket_agent`: Queries Polymarket prediction markets by natural
                 language, returning probabilities, volumes, liquidity, and historical
                 price series. Useful for fetching current prediction market states and
                 trends.
               - `web_search_agent`: Searches macro-relevant news and articles, filtered
                 by time windows, to provide contextual or recent analysis affecting
                 markets.
               - `calender_checker_agent`: Specialized for finding historical prices and
                 event-related data tied to scheduled macro events.
               - `distribution_wizard_agent`: Performs statistical analysis and plotting
                 of historical data distributions, especially event-driven return
                 distributions.

            3. Task decomposition and dependencies:
               - Break down the user query into discrete, actionable subtasks aligned
                 with specific agent capabilities from `agent_catalog_json`.
               - When the query involves event-driven analysis, use
                 `event_data_puller_agent` first to identify relevant macro event
                 dates and metrics.
               - Use event dates as anchors for querying market data or chat messages
                 within relative windows (for example 3 days before and after each CPI
                 event).
               - Retrieve market data for specified instruments and time windows
                 relevant to those events.
               - For sentiment analysis, query message streams filtered by keywords and
                 time windows anchored around events or recent periods.
               - For predictive market queries, use `polymarket_agent` directly with
                 the natural language query and optionally request historical data for
                 trend analysis.
               - Use `distribution_wizard_agent` after obtaining raw price data when
                 the query asks for distributions, histograms, or event-conditioned
                 statistics.

            4. Task output format:
               - Represent each task as a JSON object with at least these keys:
                 - `id`: A unique identifier for the task (string, for example "task_1", "task_2").
                 - `description`: A clear description of what the task is supposed to
                   accomplish (string).
                 - `dependencies`: An array (list) of task IDs (strings) that this task
                   depends on to execute (can be empty if no dependencies).
               - You should also include, when relevant:
                 - `agent`: The specified agent to execute the task (string, must be
                   one of the agents listed in the catalog).
                 - `params`: The parameters needed for the agent to execute the task,
                   formatted according to the agent's requirements listed in the
                   catalog.

            5. Best practices:
               - When relative date ranges are needed (for example three days before
                 each CPI event), represent them as structured parameters, not informal
                 prose.
               - If no explicit channels are specified for `message_puller_agent`, you
                 may leave the channels list empty to indicate a broad search.
               - Summaries or derived analytics (for example surprise in standard
                 deviations, yield moves) should be implied as downstream analysis of
                 earlier data-collection tasks, not folded into raw data retrieval
                 tasks.
               - Respect dependencies explicitly: later tasks should reference the
                 outputs or IDs of earlier tasks through their `dependencies` field.

            6. Domain-specific mappings:
               - "NFP" means Nonfarm Payroll releases.
               - "CPI" means Consumer Price Index releases.
               - "FOMC" means Federal Open Market Committee meetings.
               - Treasury instruments: TY (10-year treasury futures), 2Y and 10Y
                 Treasury Notes.
               - "Surprise" usually means the deviation of the actual macroeconomic
                 release from consensus expectations, sometimes expressed in standard
                 deviations.
               - "Yield moves" refer to price or yield changes in treasury securities
                 around event dates.

            Input structure:
            - `query`: A natural language question or request about financial markets,
              such as estimating probabilities, analyzing price trends, or assessing
              sentiment.
            - `agent_catalog_json`: A JSON object that describes available agents,
              their capabilities, and input requirements.

            Output contract:
            - You must return ONLY a JSON array (list) of task objects following the
              schema above. Do not include any explanation, commentary, or surrounding
              text.

            DSPy signatures use InputField and OutputField attributes rather than
            standard Python function annotations, so we define them explicitly here
            instead of using the `->` syntax.
            """

            query = dspy.InputField(desc="User's natural language query about market data or predictions")
            agent_catalog_json = dspy.InputField(desc="JSON catalog of available agents with their capabilities, inputs, and tools")
            task_graph_json = dspy.OutputField(
                desc=(
                    "JSON array of task objects. Each task must have at least: "
                    "'id' (string, for example \"task_1\"), "
                    "'description' (string, what to do), "
                    "'dependencies' (list of task ID strings). "
                    "Tasks should also include 'agent' (string from catalog) and "
                    "'params' (dict with agent-specific parameters) where applicable. "
                    "Return ONLY the JSON array, no other text."
                )
            )

        class PlannerProgram(dspy.Module):
            """DSPy module wrapping a prompt-based planner with initial instruction.

            This program is compatible with GEPA optimisation. If a previous best
            prompt is available on disk (``best_prompt.txt`` in the GEPA log
            directory), ``run_gepa_optimisation`` will warm-start this module from
            that prompt, effectively providing a simple checkpoint / resume
            mechanism between runs.
            """

            def __init__(self) -> None:
                super().__init__()
                # Provide an initial instruction to guide DSPy optimization. The
                # actual prompt text can later be overridden by GEPA or by a
                # warm-start from a saved best prompt on disk.
                self.plan = dspy.ChainOfThought(PlannerSignature)

            def forward(self, query: str, agent_catalog_json: str) -> str:
                out = self.plan(
                    query=query,
                    agent_catalog_json=agent_catalog_json,
                )
                return out.task_graph_json

        all_examples = _default_trainset()

        if not all_examples:
            logger.warning("No training examples available for GEPA optimisation; exiting early.")
            return

        # Randomly split examples into train and validation (test) sets.
        # We keep at least one example in each split when possible.
        total = len(all_examples)
        train_fraction = 0.8
        indices = list(range(total))
        random.shuffle(indices)

        if total == 1:
            train_indices = indices
            val_indices: List[int] = []
        else:
            split_idx = max(1, min(total - 1, int(total * train_fraction)))
            train_indices = indices[:split_idx]
            val_indices = indices[split_idx:]

        trainset = [all_examples[i] for i in train_indices]
        valset = [all_examples[i] for i in val_indices] if val_indices else trainset

        logger.info(
            "Loaded %d total examples; using %d for training and %d for validation",
            total,
            len(trainset),
            len(valset),
        )

        def _metric_for_example(example: PlannerExample, program: PlannerProgram) -> float:
            """Core metric: evaluate a PlannerProgram on a single example.

            Returns 0.0 if parsing or evaluation fails, allowing GEPA to continue.
            """
            try:
                raw = program(
                    query=example.query,
                    agent_catalog_json=example.agent_catalog_json,
                )

                # Handle various output formats
                subtasks = json.loads(raw)
                if isinstance(subtasks, dict):
                    subtasks = subtasks.get("subtasks", subtasks.get("tasks", []))
                if not isinstance(subtasks, list):
                    logger.warning("Planner output is not a list, returning 0.0")
                    return 0.0

                catalog = json.loads(example.agent_catalog_json)
                return eval_planner_output(
                    query=example.query,
                    subtasks=subtasks,
                    agent_capabilities=catalog,
                    model=model_name,
                )
            except Exception as exc:
                logger.warning("_metric_for_example failed for query '%s': %s", example.query[:50], exc)
                return 0.0

        program = PlannerProgram()

        # ------------------------------------------------------------------
        # Checkpoint / warm-start support
        # ------------------------------------------------------------------
        #
        # If a previous GEPA run has already produced a best prompt, we use it
        # to initialise the planner's instruction. This does not resume the
        # internal GEPA search state, but it *does* allow subsequent runs to
        # start from the last known best prompt instead of from scratch.
        #
        # The same best prompt will be updated at the end of each successful
        # optimisation run.
        best_prompt_path = log_dir_path / "best_prompt.txt"
        if best_prompt_path.exists():
            try:
                existing_prompt = best_prompt_path.read_text(encoding="utf-8").strip()
                if existing_prompt:
                    logger.info("Found existing best_prompt.txt – warm-starting planner from saved prompt.")
                    plan = getattr(program, "plan", None)
                    if plan is not None:
                        for attr in ("prompt", "instruction", "template"):
                            if hasattr(plan, attr):
                                setattr(plan, attr, existing_prompt)
                                logger.info("Applied warm-start prompt to PlannerProgram.%s", attr)
                                break
            except Exception as exc:  # pragma: no cover - best-effort only
                logger.warning("Failed to load existing best prompt for warm start: %s", exc)

        def gepa_metric(gold, pred, trace=None, pred_name=None, pred_trace=None) -> float:
            """Metric function for DSPy GEPA evaluation.

            Flexible signature that accepts 2-5 arguments to support different DSPy calling conventions.
            Can be called as: metric(example, pred) OR metric(gold, pred, trace, pred_name, pred_trace)

            Args:
                gold: The gold/input example (contains query and agent_catalog_json)
                pred: The model's prediction (task graph JSON)
                trace: Execution trace (optional, unused)
                pred_name: Prediction field name (optional, unused)
                pred_trace: Prediction trace (optional, unused)

            Returns:
                Score in [0, 1] range
            """
            try:
                # Extract the task graph from prediction
                if hasattr(pred, "task_graph_json"):
                    raw = pred.task_graph_json
                elif isinstance(pred, dict) and "task_graph_json" in pred:
                    raw = pred["task_graph_json"]
                else:
                    # pred might be the raw string itself
                    raw = str(pred)

                # Parse the task graph
                subtasks = json.loads(raw)
                if isinstance(subtasks, dict):
                    subtasks = subtasks.get("subtasks", subtasks.get("tasks", []))
                if not isinstance(subtasks, list):
                    logger.warning("Prediction is not a list, returning 0.0")
                    return 0.0

                # Get the agent catalog from the gold example
                catalog = json.loads(gold.agent_catalog_json)

                # Evaluate using our comprehensive metric
                return eval_planner_output(
                    query=gold.query,
                    subtasks=subtasks,
                    agent_capabilities=catalog,
                    model=model_name,
                )
            except Exception as exc:
                logger.warning("gepa_metric failed: %s", exc)
                return 0.0

        # GEPA teleprompter: use the newer DSPy API (GEPA(...).compile(...)).
        #
        # In recent DSPy versions, GEPA is constructed with a metric function and
        # optional search configuration, and optimisation is performed via the
        # `compile` method rather than `fit`.
        try:
            # GEPA now requires a reflection language model (or custom
            # instruction proposer). We reuse the same base model name and
            # parameter settings used for the main LM above.
            reflection_lm = dspy.LM(
                model=model_name,
                temperature=lm_temperature,
                max_tokens=lm_max_tokens,
            )

            # Heavier-budget GEPA configuration:
            # - max_full_evals controls the total number of full evaluations.
            # - num_threads>1 enables multi-threaded evaluation.
            # - log_dir ensures that GEPA writes out logs (including the best prompt).
            teleprompter = dspy.GEPA(
                metric=gepa_metric,
                reflection_lm=reflection_lm,
                max_full_evals=max_full_evals,
                num_threads=max(2, num_threads),
                log_dir=str(log_dir_path),
            )
        except AttributeError as exc:  # pragma: no cover - version mismatch
            raise RuntimeError(
                "Your DSPy version does not expose dspy.GEPA; "
                "please adapt `run_gepa_optimisation` to the available API."
            ) from exc

        logger.info("Starting GEPA compilation (this may take a while)...")
        logger.info("GEPA will evaluate up to %d candidate programs", max_full_evals)

        # GEPA can automatically reload previous optimisation state from the same
        # log directory. If the validation set size has changed between runs, that
        # resume can fail with an AssertionError. In that case we clear the GEPA
        # state in the log dir (but keep any saved best_prompt.txt) and retry once
        # from a clean state, still warm-starting the planner from best_prompt.txt.
        try:
            best_program = teleprompter.compile(
                student=program,
                trainset=trainset,
                valset=valset,
            )
        except AssertionError as exc:
            logger.warning(
                "GEPA state mismatch detected (likely changed train/val size). "
                "Clearing previous GEPA state in %s and retrying compile once. "
                "Details: %s",
                log_dir_path,
                exc,
            )
            # Remove all files/dirs in log_dir_path except best_prompt.txt so we
            # can still warm-start the prompt but not the internal GEPA state.
            for path in log_dir_path.iterdir():
                if path.is_file() and path.name == "best_prompt.txt":
                    continue
                if path.is_file():
                    try:
                        path.unlink()
                    except Exception:
                        logger.warning("Failed to delete GEPA state file: %s", path)
                elif path.is_dir():
                    try:
                        shutil.rmtree(path)
                    except Exception:
                        logger.warning("Failed to delete GEPA state directory: %s", path)

            # Recreate a fresh teleprompter and compile again.
            teleprompter = dspy.GEPA(
                metric=gepa_metric,
                reflection_lm=reflection_lm,
                max_full_evals=max_full_evals,
                num_threads=max(2, num_threads),
                log_dir=str(log_dir_path),
            )
            try:
                best_program = teleprompter.compile(
                    student=program,
                    trainset=trainset,
                    valset=valset,
                )
            except AssertionError as exc2:
                logger.error(
                    "GEPA compilation failed again after clearing state in %s. "
                    "This usually indicates an incompatible or corrupted GEPA log "
                    "directory, or a DSPy version mismatch. "
                    "Please delete the directory manually and retry. Details: %s",
                    log_dir_path,
                    exc2,
                )
                return

        logger.info("GEPA compilation complete!")

        # Persist a human-readable snapshot of the best program / prompt so it can
        # be inspected offline as a simple text file.
        try:
            best_prompt_text: str | None = None

            # Try a few common attribute names used in recent DSPy versions.
            plan = getattr(best_program, "plan", None)
            if plan is not None:
                for attr in ("prompt", "instruction", "template"):
                    candidate = getattr(plan, attr, None)
                    if isinstance(candidate, str) and candidate.strip():
                        best_prompt_text = candidate.strip()
                        break

            if not best_prompt_text:
                # Fallback: pretty-print the program representation.
                best_prompt_text = repr(best_program)

            best_prompt_file = log_dir_path / "best_prompt.txt"
            best_prompt_file.write_text(best_prompt_text, encoding="utf-8")
            logger.info("Saved best planner prompt to %s", best_prompt_file)
        except Exception as exc:  # pragma: no cover - best-effort logging only
            logger.error("Failed to save best planner prompt: %s", exc)

        # Basic demo: run the best program on the trainset and log scores.
        for example in trainset:
            raw = best_program(
                query=example.query,
                agent_catalog_json=example.agent_catalog_json,
            )
            subtasks = json.loads(raw) if raw else []
            catalog = json.loads(example.agent_catalog_json)
            score = eval_planner_output(
                query=example.query,
                subtasks=subtasks,
                agent_capabilities=catalog,
                model=model_name,
            )
            logger.info("Final score for query '%s': %.3f", example.query, score)
    finally:
        # Log a final usage summary to the console so you can quickly
        # see how many tokens/requests were consumed by this run.
        usage_stats = _get_llm_usage_stats()
        if usage_stats:
            logger.info(
                "LLM usage summary: tokens_total=%.0f (in=%.0f, out=%.0f), "
                "requests_total=%.0f, window_minutes=%.2f, "
                "tokens_per_minute=%.1f, requests_per_minute=%.2f",
                usage_stats.get("llm/tokens_total", 0.0),
                usage_stats.get("llm/tokens_input_total", 0.0),
                usage_stats.get("llm/tokens_output_total", 0.0),
                usage_stats.get("llm/requests_total", 0.0),
                usage_stats.get("llm/window_minutes", 0.0),
                usage_stats.get("llm/tokens_per_minute", 0.0),
                usage_stats.get("llm/requests_per_minute", 0.0),
            )

        if wandb_run is not None and wandb is not None:
            try:
                wandb_run.finish()  # type: ignore[call-arg]
            except Exception as exc:  # pragma: no cover - telemetry cleanup
                logger.warning("Failed to finish wandb run cleanly: %s", exc)
        _set_wandb_run(None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Offline DSPy/GEPA optimisation harness for the orchestrator planner. "
            "Requires optional dependencies (dspy, openai, wandb)."
        )
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default="gpt-4.1-mini",
        help="Base LLM to use for planner optimisation (default: gpt-4.1-mini).",
    )
    parser.add_argument(
        "--max-full-evals",
        type=int,
        default=5,
        help="Maximum number of full GEPA evaluations (default: 5; heavy runs may use 300+).",
    )
    parser.add_argument(
        "--num-threads",
        type=int,
        default=4,
        help="Number of threads for GEPA evaluation (default: 4).",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default=None,
        help="Directory for GEPA logs and best_prompt.txt (default: scripts/gepa_logs).",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Random seed for train/validation split (default: 42).",
    )
    parser.add_argument(
        "--wandb-project",
        type=str,
        default="gepa-opt-power-test",
        help="Weights & Biases project name (overrides WANDB_PROJECT env if set).",
    )
    parser.add_argument(
        "--wandb-run-name",
        type=str,
        default="gepa-opt-power-test",
        help="Weights & Biases run name (overrides WANDB_RUN_NAME env if set).",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    run_gepa_optimisation(
        model_name=args.model_name,
        max_full_evals=args.max_full_evals,
        num_threads=args.num_threads,
        log_dir=args.log_dir,
        random_seed=args.random_seed,
        wandb_project=args.wandb_project,
        wandb_run_name=args.wandb_run_name,
    )


