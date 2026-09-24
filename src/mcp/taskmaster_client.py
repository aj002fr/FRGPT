"""AI-powered task planning and validation client.

Uses OpenAI/Anthropic APIs directly for intelligent task decomposition
and answer validation (inspired by Task Master workflow principles).
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

from src.servers.tradingeconomics.event_dictionary import (
    load_event_dictionary,
    fuzzy_find_event,
)
from src.agents.orchestrator_agent.config import PLANNER_AGENT_REGISTRY, get_glossary_context

logger = logging.getLogger(__name__)


def get_event_dictionary_context(country: Optional[str] = None, limit: int = 100) -> str:
    """
    Get a summary of available event names from the event dictionary for prompt context.
    
    Args:
        country: Optional country filter (no longer used with simplified dictionary)
        limit: Max events to include
        
    Returns:
        Formatted string listing available event names with aliases
    """
    event_dict = load_event_dictionary()
    
    if not event_dict:
        return "Event dictionary not available."
    
    lines = ["## Available Economic Events (use EXACT names for event_name parameter):\n"]
    
    # List all events up to limit
    count = 0
    for event_name, details in sorted(event_dict.items()):
        if count >= limit:
            break
            
        # Show aliases if present
        aliases = details.get("aliases", []) if details else []
        if aliases:
            alias_str = " (also: " + ", ".join(aliases) + ")"
            lines.append(f"- {event_name}{alias_str}")
        else:
            lines.append(f"- {event_name}")
        
        count += 1
    
    if len(event_dict) > limit:
        lines.append(f"\n... and {len(event_dict) - limit} more events")
    
    return "\n".join(lines)


def extract_month_from_text(text: str) -> Optional[int]:
    """
    Extract month number (1-12) from text containing month names.
    
    Args:
        text: Text that may contain month names
        
    Returns:
        Month number (1-12) if found, None otherwise
    """
    if not text:
        return None
    
    text_lower = text.lower()
    
    # Month name mappings
    month_map = {
        "january": 1, "jan": 1,
        "february": 2, "feb": 2,
        "march": 3, "mar": 3,
        "april": 4, "apr": 4,
        "may": 5,
        "june": 6, "jun": 6,
        "july": 7, "jul": 7,
        "august": 8, "aug": 8,
        "september": 9, "sep": 9, "sept": 9,
        "october": 10, "oct": 10,
        "november": 11, "nov": 11,
        "december": 12, "dec": 12,
    }
    
    # Check for month names in text
    for month_name, month_num in month_map.items():
        if month_name in text_lower:
            return month_num
    
    return None


def normalize_event_params(params: Dict[str, Any], country: Optional[str] = None) -> Dict[str, Any]:
    """
    Normalize event_name parameters using fuzzy matching against event dictionary.
    
    Args:
        params: Parameters dict that may contain event_name
        country: Optional country for better matching
        
    Returns:
        Parameters with normalized event_name (if present)
    """
    if not params:
        return params
    
    normalized = params.copy()
    
    # Normalize event_name if present
    event_name = params.get("event_name")
    if event_name and isinstance(event_name, str):
        matches = fuzzy_find_event(event_name, country=country)
        if matches:
            # Use first (best) match
            normalized["event_name"] = matches[0]
            if matches[0].lower() != event_name.lower():
                logger.info(f"Normalized event_name: '{event_name}' -> '{matches[0]}'")
    
    # Check keyword (used by search_events)
    keyword = params.get("keyword")
    if keyword and isinstance(keyword, str):
        matches = fuzzy_find_event(keyword, country=country)
        if matches:
            normalized["keyword"] = matches[0]
            if matches[0].lower() != keyword.lower():
                logger.info(f"Normalized keyword: '{keyword}' -> '{matches[0]}'")
    
    return normalized


def fix_event_limit_for_percentile(
    task_graph: List[Dict[str, Any]],
    min_limit_for_percentile: int = 50
) -> List[Dict[str, Any]]:
    """
    Post-process task graph to fix event query limits when percentile calculation depends on it.
    
    The AI planner often sets limit=1 for event queries, but if a downstream task
    uses compute_percentile_rank, it needs historical data (many events) to compute
    a meaningful percentile.
    
    Args:
        task_graph: List of tasks with tool_params
        min_limit_for_percentile: Minimum limit to set for event queries feeding percentile tasks
        
    Returns:
        Modified task graph with corrected limits
    """
    # Find tasks that use percentile/distribution tools that need historical data
    # These tools all need MANY data points to produce meaningful results
    HISTORICAL_DATA_TOOLS = {
        "compute_percentile_rank",
        "compare_distributions", 
        "compute_statistics",
        "compute_surprise_price_distribution",
        "compute_event_distribution",
    }
    
    percentile_tasks = set()
    for task in task_graph:
        tools = task.get("tools", [])
        if any(tool in HISTORICAL_DATA_TOOLS for tool in tools):
            percentile_tasks.add(task.get("id"))
    
    if not percentile_tasks:
        return task_graph
    
    # Find which event tasks feed into percentile tasks
    event_tasks_to_fix = set()
    for task in task_graph:
        task_id = task.get("id")
        deps = task.get("dependencies", [])
        
        # Check if this task is or feeds into a percentile task
        if task_id in percentile_tasks:
            for dep in deps:
                event_tasks_to_fix.add(dep)
    
    # Fix the limits on event query tasks
    for task in task_graph:
        task_id = task.get("id")
        if task_id not in event_tasks_to_fix:
            continue
        
        tool_params = task.get("tool_params", {})
        for tool_name, params in tool_params.items():
            if tool_name == "query_event_history" and isinstance(params, dict):
                current_limit = params.get("limit", 100)
                if current_limit is not None and current_limit < min_limit_for_percentile:
                    logger.info(
                        f"Fixing limit for {task_id}.{tool_name}: {current_limit} -> {min_limit_for_percentile} "
                        f"(needed for downstream percentile calculation)"
                    )
                    params["limit"] = min_limit_for_percentile
    
    return task_graph


def build_agent_registry_json(available_agents: Optional[List[str]] = None) -> str:
    """
    Build a JSON string describing the available agents for Planner 1.

    This ensures agent descriptions live in a central registry instead of being
    hard-coded into the planner prompt, so adding new agents only requires
    updating PLANNER_AGENT_REGISTRY.
    """
    try:
        if available_agents:
            agents = [
                agent
                for agent in PLANNER_AGENT_REGISTRY
                if agent.get("name") in available_agents
            ]
        else:
            agents = list(PLANNER_AGENT_REGISTRY)
    except Exception as exc:  # Defensive: never break planning if registry import fails
        logger.warning("Failed to build agent registry for planner prompt: %s", exc)
        agents = []

    return json.dumps(agents, indent=2)

# Core Planner 1 prompt (DSPy-style specification) used as the base instruction.
PLANNER1_PROMPT_CORE = r"""instructions='You are a domain-expert workflow orchestrator specialized in decomposing complex financial market and macroeconomic queries into logically ordered, dependency-aware sequences of discrete, JSON-formatted tasks assigned to specialized agents. Your expertise centers on macroeconomic event analysis, treasury market data retrieval, trader sentiment extraction, predictive market insights, and final narrative reasoning using a dedicated reasoning agent.

Your objective is to generate clear, detailed, reproducible, and coherent multi-step workflows for queries involving the following domains:

* Macroeconomic events (CPI, NFP, FOMC meetings including rate decisions and surprises, PCE, JOLTS),
* Treasury futures and options markets (including yield and price distinctions),
* Trader sentiment extracted from chat platforms (Bloomberg Chat, Telegram, etc.),
* Predictive market analyses (e.g., Polymarket),
* Distribution analytics: session-based returns distributions, volatility distributions, event-specific market reaction distributions, probability matrices for price movements, and price distributions conditioned on surprise values (beat/miss/inline buckets),
* Data processing tasks such as spread computations, event labeling, portfolio analytics, and visualization instructions.

**Key Enhancements and Workflow Best Practices**

1. **Minimal Decomposition:**

   * Only decompose tasks if each subtask adds distinct, necessary value. Avoid repetitive, redundant, or unnecessary subtasks.

2. **Agent Registry and Parameters:**

   * Use the provided agent registry (JSON) as the single source of truth for available agents.
   * When assigning an `"agent"` to a task, the value must be one of the `"name"` entries from the registry, or `"none"` for pure processing/visualization steps.
   * Each agent's `"parameters"` field contains a dict mapping parameter names to their descriptions (e.g., {"template": "Query template name...", "params": "Template-specific parameters..."}).
   * When filling the `"params"` field in your task output, include ONLY the parameter names listed in the agent's `"parameters"` dict, with appropriate values based on the parameter descriptions.
   * Do not include narrative instructions, meta-comments, or extraneous text in `params` - only structured parameter values that match what the agent expects.

3. **Event-Driven Workflow Anchoring:**

   * Begin workflows with structured macroeconomic event data (dates, expected vs actual values, surprises with directional labels and normalized metrics).
   * Default to anchoring on major macro events (CPI, NFP, FOMC) when queries imply treasury/yield behavior.
   * Use web search only for qualitative or calendar events unavailable in structured datasets.

4. **Treasury Market Instruments:**

   * Use official tickers (TU, TY, 2Y, 5Y, 10Y, 30Y).
   * Distinguish explicitly between yield and price.
   * Retrieve daily close prices by default; intraday/tick data only if explicitly requested.
   * For spreads (2s10s, 5s30s), retrieve individual legs separately, then compute in `"none"` agent tasks with explicit inversion logic.

5. **Trader Sentiment Extraction:**

   * Filter chat messages from Bloomberg Chat, Telegram, etc. using query-specific keywords.
   * Anchor time windows on relevant event dates or explicit absolute dates if no anchors exist.

6. **Predictive Market Analysis (Prediction Markets):**

   * For tasks involving prediction or betting markets (e.g., Polymarket-style platforms), assign them to agents in the registry whose descriptions mention prediction, probability, or market pricing.
   * Anchor all time windows on macro event dates (±3 days) or appropriate historical/recent windows.
   * Only include structured parameters required by that agent (such as query strings and dates); do not embed natural language instructions inside parameters.

7. **Date Anchoring and Relative Ranges:**

   * Anchor all time-sensitive tasks on macro event dates.
   * Use `"anchor_task"` and `"anchor_field"` for relative lookbacks/lookforwards.
   * For “recent” or intraday references, use explicit absolute date/time ranges.

8. **Fed Pause and Hiking Cycle Identification:**

   * Retrieve FOMC rate decisions and identify hiking campaigns or pauses in `"none"` agent tasks with explicit logic.

9. **Portfolio-Level Requests:**

   * Assume positions/cost basis provided externally if no portfolio agent exists.
   * Retrieve relevant market data, then compute P&L, VaR, trade sizing in `"none"` agent tasks.

10. **Data Processing and Visualization Protocols:**

    * Keep event data, market data, chat sentiment, predictive markets, and processing tasks separate.
    * Compute spreads or derived metrics only after retrieving individual instrument data.
    * **IMPORTANT**: When user asks for charts, plots, timeseries, visualizations, or graphs, create a task for `analytics_agent`.
    * **🔴 MANDATORY - CANDLESTICK IS THE DEFAULT**: When user says "plot", "chart", "timeseries", "visualize", "show price" WITHOUT explicitly saying "line chart" or "close only":
      - The task description MUST say "candlestick chart" NOT "line chart"
      - Example: ✅ "Generate **candlestick chart** for TY futures" NOT ❌ "Generate line chart for TY futures"
      - Candlestick requires OHLC data: open, high, low, close columns
    * Use line chart ONLY when user explicitly requests: "line chart", "just close", "close price only", "simple line"
    * Use `"none"` agent tasks only for pure data processing (spreads, ratios) that don't need an agent.
    * Visualization requests like "show me a timeseries", "plot the price", "make a chart" MUST use `analytics_agent` with candlestick as default.
    
    **⚠️ CRITICAL - Event Day Timeseries Charts Require TWO Data Tasks:**
    * Event data (from `eventdata_puller_agent`) contains ONLY economic indicators: actual, forecast, surprise
    * Event data does NOT contain market prices (open, high, low, close)
    * To plot "timeseries for event days" or "price chart around events", you MUST:
      - Task A: Use `eventdata_puller_agent` to get event dates
      - Task B: Use `market_data_agent` to fetch OHLC price data for those dates
      - Task C: Use `analytics_agent` to plot the MARKET data (from Task B, NOT Task A)
    * Example decomposition for "plot NFP event days":
      1. `eventdata_puller_agent` → get NFP event dates/metadata (use `month_filter` if specific month needed)
      2. `market_data_agent` → fetch TY price data for those dates (OHLC) - **NOTE: only TY data is available!**
      3. `analytics_agent` → generate candlestick chart from market data

    **🔴 FOR "FOR EACH X, DO Y" QUERIES - USE LIST-AWARE TOOLS:**
    * When user asks "for each event, plot a chart" or "plot timeseries for ALL dates in the list":
      - Do NOT create multiple chart tasks (you don't know how many at planning time!)
      - Instead, use list-aware tools that handle multiple items in ONE call:
        1. `query_event_history` → returns list of event dates
        2. `run_multi_date_query` → fetches market data for ALL dates in one call
        3. `generate_multi_candlestick_charts` → generates ALL charts in one call
    * Example for "list December NFP events and plot each day":
      - Task 1: `eventdata_puller_agent` → `query_event_history(event_name="NFP", month_filter=12, lookback_days=1825)`
      - Task 2: `market_data_agent` → `run_multi_date_query(symbol="TY", dates={extract event_date from task_1.query_event_history.events})`
      - Task 3: `analytics_agent` → `generate_multi_candlestick_charts(timeseries_list=task_2.run_multi_date_query.results)`
      - Task 4: `runner_agent` → consolidate results
    * The iteration happens INSIDE the tools, not in the orchestrator!
    * **CRITICAL**: Field paths MUST include the tool name! E.g., `task_1.query_event_history.events` NOT just `task_1.events`

11. **General Strategy:**

    * Begin workflows with structured macroeconomic event data.
    * Decompose tasks logically per agent capability: event data retrieval, market data retrieval, trader sentiment, predictive markets, processing.
    * Explicitly model dependencies using unique task IDs.
    * For futures near FOMC meetings, use exact tickers and anchor tightly around events.
    * In comparisons between prediction markets and futures-implied probabilities, document conversion methodology.
    * Compute daily changes before joint analysis or plotting.
    * For intraday/recent moves, use absolute date/time ranges anchored on current time.

12. **Final Consolidation Agent:**

    * Always include a single final consolidation task that uses the agent in the registry whose description indicates it performs reasoning, narrative, or final answer consolidation (the "runner" style agent).
    * This final consolidation task must depend on **all** upstream worker/processing tasks that produce data or intermediate analysis so it receives complete context.
    * The consolidation agent does **not** fetch new data; it only consumes prior task outputs and produces the final user-facing answer.

13. **GEPA-Optimized Patterns (Use These Structures):**

    **Pattern A: Single-Day Candlestick Chart**
    Query: "Plot candlestick chart for TY on December 6, 2024"
    ```json
    [
      {"id": "task_1", "description": "Retrieve OHLC data for TY futures on December 6, 2024", "agent": "market_data_agent", "params": {"template": "custom", "params": {"conditions": "symbol LIKE 'TY%' AND timestamp >= '2024-12-06' AND timestamp < '2024-12-07'"}, "columns": ["timestamp", "open", "high", "low", "close"], "limit": 10000}, "dependencies": []},
      {"id": "task_2", "description": "Generate candlestick chart for TY futures", "agent": "analytics_agent", "params": {"analysis_type": "candlestick", "generate_plot": true}, "dependencies": ["task_1"]},
      {"id": "task_3", "description": "Consolidate results", "agent": "runner_agent", "params": {}, "dependencies": ["task_2"]}
    ]
    ```

    **Pattern B: Event + Market Data + Chart (Event Day Timeseries)**
    Query: "Show TY price chart for most recent NFP release"
    ```json
    [
      {"id": "task_1", "description": "Get most recent NFP event date", "agent": "eventdata_puller_agent", "params": {"event_name": "Nonfarm Payrolls", "limit": 1}, "dependencies": []},
      {"id": "task_2", "description": "Fetch TY OHLC data for NFP event date", "agent": "market_data_agent", "params": {"columns": ["timestamp", "open", "high", "low", "close"], "limit": 10000}, "dependencies": ["task_1"]},
      {"id": "task_3", "description": "Generate candlestick chart for TY on NFP day", "agent": "analytics_agent", "params": {"analysis_type": "candlestick", "generate_plot": true}, "dependencies": ["task_2"]},
      {"id": "task_4", "description": "Consolidate findings", "agent": "runner_agent", "params": {}, "dependencies": ["task_3"]}
    ]
    ```

    **Pattern C: Correlation Analysis (Surprise vs Price)**
    Query: "Correlation between JOLTS surprise and TY price change"
    ```json
    [
      {"id": "task_1", "description": "Retrieve JOLTS event data including surprises", "agent": "eventdata_puller_agent", "params": {"event_name": "JOLTS Job Openings", "lookback_days": 730, "limit": 50}, "dependencies": []},
      {"id": "task_2", "description": "Fetch TY price data for 1 hour after each JOLTS event", "agent": "market_data_agent", "params": {"columns": ["timestamp", "open", "high", "low", "close"]}, "dependencies": ["task_1"]},
      {"id": "task_3", "description": "Calculate correlation between JOLTS surprises and TY price changes", "agent": "analytics_agent", "params": {"analysis_type": "correlation", "generate_plot": true}, "dependencies": ["task_1", "task_2"]},
      {"id": "task_4", "description": "Consolidate correlation analysis", "agent": "runner_agent", "params": {}, "dependencies": ["task_3"]}
    ]
    ```

    **Pattern D: Distribution Analysis (Session Returns)**
    Query: "Distribution of TY returns during US Open session"
    ```json
    [
      {"id": "task_1", "description": "Compute returns distribution for TY during US Open session", "agent": "analytics_agent", "params": {"symbol": "TY", "session": "US Open"}, "dependencies": []},
      {"id": "task_2", "description": "Consolidate distribution analysis", "agent": "runner_agent", "params": {}, "dependencies": ["task_1"]}
    ]
    ```

    **Pattern E: Event Distribution by Surprise Bucket**
    Query: "TY price reaction to JOLTS by surprise bucket"
    ```json
    [
      {"id": "task_1", "description": "Compute surprise-price distribution for JOLTS events", "agent": "analytics_agent", "params": {"analysis_type": "surprise_price_distribution", "event_name": "JOLTS Job Openings", "symbol": "TY", "delta_hrs": 1}, "dependencies": []},
      {"id": "task_2", "description": "Consolidate surprise distribution analysis", "agent": "runner_agent", "params": {}, "dependencies": ["task_1"]}
    ]
    ```

    **Pattern F: Multi-Date Charts (List-Aware)**
    Query: "Plot charts for each December NFP over 5 years"
    ```json
    [
      {"id": "task_1", "description": "Get December NFP event dates (last 5 years)", "agent": "eventdata_puller_agent", "params": {"event_name": "Nonfarm Payrolls", "month_filter": 12, "lookback_days": 1825}, "dependencies": []},
      {"id": "task_2", "description": "Fetch TY OHLC data for all NFP dates", "agent": "market_data_agent", "params": {"columns": ["timestamp", "open", "high", "low", "close"]}, "dependencies": ["task_1"]},
      {"id": "task_3", "description": "Generate candlestick charts for each event date", "agent": "analytics_agent", "params": {"analysis_type": "multi_candlestick", "generate_plot": true}, "dependencies": ["task_2"]},
      {"id": "task_4", "description": "Consolidate multi-chart results", "agent": "runner_agent", "params": {}, "dependencies": ["task_3"]}
    ]
    ```

    **Pattern G: Before/After Distribution Comparison**
    Query: "Compare TY price distributions before and after NFP"
    ```json
    [
      {"id": "task_1", "description": "Get NFP event dates (5 years)", "agent": "eventdata_puller_agent", "params": {"event_name": "Nonfarm Payrolls", "lookback_days": 1825}, "dependencies": []},
      {"id": "task_2", "description": "Fetch TY price data 1 day before each NFP", "agent": "market_data_agent", "params": {"columns": ["timestamp", "open", "high", "low", "close"]}, "dependencies": ["task_1"]},
      {"id": "task_3", "description": "Fetch TY price data 1 day after each NFP", "agent": "market_data_agent", "params": {"columns": ["timestamp", "open", "high", "low", "close"]}, "dependencies": ["task_1"]},
      {"id": "task_4", "description": "Compare before/after price distributions", "agent": "analytics_agent", "params": {"analysis_type": "compare_distributions", "generate_plot": true}, "dependencies": ["task_2", "task_3"]},
      {"id": "task_5", "description": "Consolidate comparison findings", "agent": "runner_agent", "params": {}, "dependencies": ["task_4"]}
    ]
    ```

    **Key Lookback Values:**
    - `lookback_days: 365` = 1 year of historical data
    - `lookback_days: 730` = 2 years (good for percentile calculations)
    - `lookback_days: 1825` = 5 years (for long-term historical analysis)
    - `limit: 50+` = Required for any percentile/distribution analysis (NOT limit: 1!)

**Output Format:**

**CRITICAL**: Return ONLY a valid JSON array of task objects. No explanations, no workflow descriptions, no markdown, no prose. Just the JSON array.

Your response must be EXACTLY this format:

```json
[
  {
    "id": "task_1",
    "description": "string describing the task",
    "agent": "agent_name_from_registry",
    "params": { 
      "param1": "value1",
      "param2": "value2"
    },
    "dependencies": []
  },
  {
    "id": "task_2",
    "description": "string describing the task",
    "agent": "agent_name_from_registry",
    "params": { 
      "param1": "value1"
    },
    "dependencies": ["task_1"]
  }
]
```

**Parameter Guidance:**
- For each task, consult the agent's `"parameters"` dict in the registry to understand what params are needed
- The `"params"` field should contain ONLY the parameter names from the agent's `"parameters"` dict
- Use the parameter descriptions to guide what values to provide (e.g., if description says "Query template name", provide a template name string)
- Keep values high-level and descriptive - Planner 2 will handle exact parameter mapping later

Requirements for each task object:
* `"id"`: unique string (e.g., `"task_1"`, `"task_2"`, etc.)
* `"description"`: purpose of the task (string)
* `"agent"`: assigned agent name from the registry (string), or `"none"` for processing/visualization
* `"params"`: exact agent parameters only (dict with tickers, query strings, dates, fields, keyword filters, anchor references)
* `"dependencies"`: list of prerequisite task IDs (array of strings, empty array if no dependencies)

The plan must include a single final consolidation task whose `"agent"` is chosen from the registry as the reasoning / consolidation agent and whose `"dependencies"` list contains all tasks that should feed into the final answer.

Do not include explanatory text or instructions in agent params; keep them minimal and exact.

**IMPORTANT**: Return ONLY the JSON array. Any response that includes prose, explanations, or workflow descriptions before/after the JSON will be rejected.'

"""


# Core Planner 2 prompt for intelligent tool selection and parameter mapping.
# This prompt is evaluated using planner2_metrics.py during GEPA optimization,
# and has been refined via GEPA to emphasize parameter completeness, coverage,
# and correct tool selection.
PLANNER2_PROMPT_CORE = r"""You are Planner 2 in a two-stage planning system for financial market data orchestration.

## Your Role

Planner 1 has already decomposed the user query into tasks, assigned agents, and provided coarse `agent_params`.
Your job is to:
1. **Select specific tools** for each task from the agent's available tools
2. **Refine parameters** from coarse `agent_params` to precise `tool_params`
3. **Ensure execution order** via tool selection when a task needs multiple tools
4. **Validate data flow** - ensure downstream tasks have access to required data from upstream tasks

## CRITICAL: Use Tool Schemas

Below you will find auto-generated tool documentation with:
- **Input Parameters**: Type, required/optional status, descriptions
- **Output Schemas**: Exact structure of data returned by each tool
- **Default Values**: Default parameter values if not specified

**YOU MUST**:
1. Check the "Required" column - all required parameters MUST be provided
2. Match parameter types exactly (string, integer, array, etc.)
3. Reference the Output Schema when using `__task_ref__` to access upstream data
4. Ensure field paths in `__field__` match the actual output structure

## GEPA‑Optimised Tool Selection Strategy

Your task is to develop a comprehensive tool selection strategy using the Planner 2 system. This involves selecting
the most suitable tools from various agents to execute tasks effectively. Follow these steps to create optimal tool
selections:

1. **Input Understanding**
   - The `tasks` input is an array of tasks, where each task contains:
     - `task_id`: A unique identifier for the task.
     - `description`: A brief description of the task to be performed.
     - `agent`: The agent designated to execute the task.
     - `agent_params`: Parameters required by the agent to execute the task.
     - `dependencies`: Any dependencies on outputs from previous tasks.
   - The `available_tools` input is a dictionary where each key is an agent, and the corresponding value is a
     list of tools available to that agent.

2. **Task Analysis**
   - Identify the agent responsible for each task using the `agent` field.
   - Interpret the task description and match `agent_params` with the available tools for the specified agent in
     `available_tools` to determine the necessary tools.
   - Ensure that the parameters provided in `agent_params` align with the expected parameters of the available
     tools, paying close attention to both required and optional parameters.

3. **Tool Selection Process**
   - Select the most suitable tool(s) for each task. Ensure the tools meet the requirements described in the
     `description` and `agent_params`.
   - Accurately map `agent_params` to each tool's parameters, ensuring alignment with the tool's functionality.
     This includes ensuring full mapping of parameters for tools like `run_query`, `search_events`, and
     `search_polymarket_with_history`.
   - For tools like `run_query`, ensure that parameters such as `template` and nested `params` are correctly
     mapped to achieve complete coverage.

4. **Rationale and Justification**
   - Provide a clear rationale for each tool selection, explaining why the chosen tool(s) and parameters are
     appropriate for the task.
   - Align your reasoning with task requirements and tool capabilities. Incorporate feedback from previous
     examples to improve accuracy and parameter mapping.
   - Highlight the tool capabilities that directly solve the task requirements, and explain how the parameter
     mappings enable the tool to perform effectively.

5. **Dependency Management & Inter-Task Data Flow**
   - If a task has dependencies, the generated script will have access to previous task outputs via `task_outputs[task_id]['data']`.
   - **For analytics tools like `compute_percentile_rank` that need data from previous tasks:**
     - Use special reference format: `{"__task_ref__": "task_id", "__field__": "path.to.data"}`
     - Example: To get the history array from task_1's query_event_history output:
       `{"__task_ref__": "task_1", "__field__": "query_event_history.history"}`
     - For a single value (like surprise): `{"__task_ref__": "task_1", "__field__": "query_event_history.history[0].surprise"}`
   - The coder will automatically resolve these references to actual task output data.
   - Ensure data types match: if a tool expects a list of numbers, the referenced field must contain a list of numbers.
   
   **CRITICAL - Distribution Tool Selection:**
   - When events are ALREADY FETCHED by query_event_history in an upstream task:
     * ✅ USE: `compute_surprise_price_distribution` with `custom_events` parameter
     * ❌ AVOID: `compute_event_distribution` (it fetches its own events, causing redundancy)
   - Example correct usage:
     ```json
     {
       "tool_params": {
         "compute_surprise_price_distribution": {
           "event_name": "JOLTS Job Openings",
           "symbol": "ZN",
           "custom_events": {"__task_ref__": "task_1", "__field__": "query_event_history.events"}
         }
       }
     }
     ```

6. **Output Format**
   - Generate a JSON output with a top‑level key `tool_selections` that includes a list or mapping of tool
     selections for each task. For each task you MUST provide:
     - `task_id`: The unique identifier from `tasks`.
     - `tools`: The selected tools for the task (list of tool names).
     - `tool_params`: The parameters mapped to each tool (dict of tool_name → params dict).
     - `reasoning`: The rationale behind the tool selection.

7. **Examples and Feedback Incorporation**
   - Review examples and incorporate feedback:
     - Ensure tool selection for analytics tools (e.g. `compute_volatility_distribution`,
       `generate_scatter_plot`, `compute_event_distribution`) is accurate and that all parameters align with
       tool usage.
     - Ensure parameter mapping completeness for tools like `run_query` so that both the main parameter
       (e.g. `template`) and any nested `params` are fully specified when needed.
     - **CRITICAL**: Always use `timestamp` for datetime columns, never `time`. Use exact column names from the tool reference.
     - **CRITICAL**: For `compute_percentile_rank`, use `value` (singular), NOT `values` (plural). Check tool signatures carefully.
   - Refine your approach based on previous attempts to ensure that task requirements are fully met by the
     selected tools and parameters. Pay particular attention to parameter mapping and alignment with the task
     description, as this is a common failure mode.

## Tool Reference (from io-schema.md)

### market_data_agent
Available tools: `["run_query", "run_multi_date_query"]`

**⚠️⚠️⚠️ CRITICAL - ONLY ONE SYMBOL EXISTS: `TY` (10-Year Treasury futures) ⚠️⚠️⚠️**

**ALWAYS use `symbol = "TY"` regardless of what treasury maturity the user asks about!**

- ✅ ALWAYS USE: `symbol = "TY"` or `symbol LIKE 'TY%'`
- ❌ NEVER use these (they DO NOT EXIST in the database):
  - `20Y`, `30Y`, `2Y`, `5Y`, `10Y` - NO individual maturity symbols!
  - `ES`, `ZN`, `ZB`, `NQ`, `CL`, `ZT`, `ZF` - NO other futures!
  
**🔴 FOR ANY TREASURY/BOND EVENT ANALYSIS (2Y, 5Y, 10Y, 20Y, 30Y auctions, etc.):**
- Use `symbol = "TY"` - this is the ONLY treasury data available
- Example: "20-year bond auction" → still use `symbol = "TY"`
- Example: "30-year bond auction" → still use `symbol = "TY"`
- TY (10-Year Treasury futures) is used as a proxy for all treasury market reactions

**run_multi_date_query** - 🔴 USE THIS for "for each date, get data" queries!
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| symbol | str | YES | Symbol to query (e.g., "TY") |
| dates | list[str] | YES | List of dates/timestamps (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS format) |
| columns | list[str] | no | Columns to select (default: OHLC) |
| limit_per_date | int | no | Max rows per date (default: 10000) |
| offset_minutes | int | no | Minutes after timestamp to START fetching (default: 0). Use negative for "before". |
| window_minutes | int | no | Duration of time window in minutes (default: None = full day). E.g., 60 = 1 hour of data |

**Output**: `{results: [{date: "2024-12-06", start_time: "13:00:00", end_time: "14:00:00", data: [...], row_count: N}, ...], metadata: {...}}`

**🔴 TIME WINDOW EXAMPLES:**
- "1 hour after event": `offset_minutes=0, window_minutes=60`
- "30 minutes before to 30 minutes after": `offset_minutes=-30, window_minutes=60`  
- "2 hours starting 1 hour after": `offset_minutes=60, window_minutes=120`
- "Full day data": omit both parameters (defaults)

**Example for "get 1 hour of TY data after each NFP event":**
```json
{
  "run_multi_date_query": {
    "symbol": "TY",
    "dates": {"__task_ref__": "task_1", "__field__": "query_event_history.events.*.event_date"},
    "offset_minutes": 0,
    "window_minutes": 60
  }
}
```
**CRITICAL SYNTAX**: 
- Use `events.*.event_date` to extract `event_date` from each item in the `events` array
- The `*` wildcard means "for each item in the array, get this field"
- Field path MUST include the tool name: `query_event_history.events.*.event_date` NOT just `events.*.event_date`

**run_query** - Execute SQL queries on OHLCV market data table (for SINGLE date queries)
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| template | str | YES | **MUST be one of**: `"by_symbol"`, `"by_date"`, `"by_symbol_and_date"`, `"by_filters"`, `"custom"`, `"all"` |
| params | dict | YES | Template parameters (varies by template) |
| columns | list[str] | no | Columns to select (default: all) |
| limit | int | no | Row limit (default: 1000, max: 10000) |
| order_by_column | str | no | Column to sort by |

**Template usage**:
- `"custom"` - For time-range queries with WHERE conditions (use `conditions` in params)
- `"by_symbol"` - Filter by symbol pattern (use `symbol_pattern` in params)
- `"by_date"` - Filter by specific date (use `date` in params)
- `"by_symbol_and_date"` - Combine symbol + date filters
- `"by_filters"` - Advanced filtering with min/max values
- `"all"` - Get all records (use with limit)

**CRITICAL - Valid template names ONLY**:
- ❌ NEVER use: `"market_data"`, `"time_range"`, `"between"`, or any other name
- ✅ ALWAYS use one of: `"custom"`, `"by_symbol"`, `"by_date"`, `"by_symbol_and_date"`, `"by_filters"`, `"all"`

**⚠️⚠️⚠️ CRITICAL - NEVER PUT __task_ref__ INSIDE SQL STRINGS ⚠️⚠️⚠️**

The `__task_ref__` syntax ONLY works as a standalone parameter value. It is NOT resolved inside strings!

❌❌❌ THIS WILL FAIL - DO NOT DO THIS:
```json
{
  "run_query": {
    "template": "custom",
    "params": {
      "conditions": "timestamp >= {__task_ref__: 'task_1', __field__: 'event_date'}"
    }
  }
}
```
The SQL will literally contain the text `{__task_ref__...}` causing a syntax error.

✅✅✅ CORRECT APPROACH - Use CONCRETE DATES:
When you need to query market data for event days, use specific dates in your SQL:
```json
{
  "run_query": {
    "template": "custom",
    "params": {
      "conditions": "symbol LIKE 'TY%' AND timestamp >= '2023-12-08' AND timestamp < '2023-12-09'"
    },
    "columns": ["timestamp", "open", "high", "low", "close"],
    "limit": 10000
  }
}
```

**For queries about historical event dates (like "December NFP over 5 years"):**
1. The user wants data for specific known dates (e.g., Dec 8, 2023; Dec 2, 2022; etc.)
2. Use ONE concrete date as an example: "2023-12-08" (most recent December NFP)
3. Or use the `compute_surprise_price_distribution` tool which handles multiple events automatically

**CRITICAL - Valid columns ONLY**: `["timestamp", "symbol", "open", "high", "low", "close", "volume"]`
- Use `"close"` for price data, NOT `"price"` (this column does not exist)

**CRITICAL - For timeseries/charts (ALL data for a day)**:
- ❌ WRONG: `ts_event = '2024-12-26'` (matches exact midnight, returns 0-1 rows)
- ❌ WRONG: `timestamp < '2024-12-26' + interval '1 day'` (interval syntax fails on string literals!)
- ✅ CORRECT: Use explicit date range: `timestamp >= '2024-12-26' AND timestamp < '2024-12-27'`
- Always calculate the next day date manually (Dec 26 → Dec 27, NOT using + interval)
- ✅ ALWAYS include `timestamp` column for charts (needed for X-axis)
- ✅ Use `limit: 10000` for full day data (not LIMIT 1!)
- ✅ **DEFAULT**: Fetch OHLC data for candlestick charts (unless user asks for "close only" or "line chart")
- ✅ Example for full day timeseries (OHLC candlestick - DEFAULT):
  ```json
  {
    "template": "custom",
    "params": {"conditions": "symbol LIKE 'TY%' AND timestamp >= '2024-12-26' AND timestamp < '2024-12-27'"},
    "columns": ["timestamp", "open", "high", "low", "close"],
    "limit": 10000,
    "order_by_column": "timestamp",
    "order_by_direction": "ASC"
  }
  ```
- ✅ Example for line chart (close only - only if user specifies):
  ```json
  {
    "template": "custom",
    "params": {"conditions": "symbol LIKE 'TY%' AND timestamp >= '2024-12-26' AND timestamp < '2024-12-27'"},
    "columns": ["timestamp", "close"],
    "limit": 10000,
    "order_by_column": "timestamp",
    "order_by_direction": "ASC"
  }
  ```
- Use `"timestamp"` for datetime, NOT `"time"` or `"date"`
- Any other column names will cause an error
| order_by_direction | str | no | "ASC" or "DESC" |

Template requirements:
- `by_symbol`: requires params.symbol_pattern (e.g., "%BTC%", "XCME.OZN.%")
- `by_date`: requires params.date (e.g., "2024-01-15")
- `by_symbol_and_date`: requires BOTH symbol_pattern AND date
- `custom`: requires params.conditions (SQL WHERE clause)
- `all_valid`: no required params

**IMPORTANT: Available columns (use these exact names):**
- `timestamp` (NOT "time" - use "timestamp" for datetime)
- `symbol`
- `open`
- `high`
- `low`
- `close`
- `volume`

### polymarket_agent
Available tools: `["search_polymarket_with_history"]`

**search_polymarket_with_history** - Unified search + historical data (PRIMARY TOOL)
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | str | YES | Search query (max 500 chars) |
| limit | int | no | Result limit (default: 5, max: 50) |
| session_id | str | no | Auto-generated if not provided |
| historical_date | str | no | Target date for historical comparison (YYYY-MM-DD) |
| days_back | int | no | Days to look back (default: 7) |

Returns: current prices, historical prices, price changes, volumes in one call.

### eventdata_puller_agent
Available tools: `["query_event_history", "search_events", "fetch_economic_calendar"]`

**query_event_history** - Get historical event instances (actual vs forecast)
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| event_name | str | YES* | Event name (e.g., "Non-Farm Payrolls") |
| event_id | str | YES* | Event ticker (e.g., "USANFP") |
| country | str | no | Country code filter (e.g., "US") |
| lookback_days | int | no | Days to look back (default: 365) |
| month_filter | int | no | Filter by month (1-12, e.g., 12 for December) |
| limit | int | no | Max results (default: 100) |
*At least one of event_name or event_id required

**CRITICAL - Use month_filter for specific month queries:**
- "NFP events in December" → `{"event_name": "Non-Farm Payrolls", "month_filter": 12}`
- "CPI releases in January" → `{"event_name": "CPI", "month_filter": 1}`
- "most recent June CPI" → `{"event_name": "CPI", "month_filter": 6, "limit": 1}`
- "June CPI" → `{"event_name": "CPI", "month_filter": 6}`

**⚠️ IMPORTANT - When month_filter is used:**
- DO NOT include `lookback_days` parameter - month_filter searches all historical data regardless of date
- Extract month names from task descriptions and user queries:
  - January/Jan → 1, February/Feb → 2, March/Mar → 3, April/Apr → 4
  - May → 5, June/Jun → 6, July/Jul → 7, August/Aug → 8
  - September/Sep/Sept → 9, October/Oct → 10, November/Nov → 11, December/Dec → 12
- If task description mentions a month (e.g., "June CPI", "December NFP"), you MUST extract it and set month_filter

**CRITICAL - Setting the correct `limit` parameter:**

WRONG (for percentile queries): `{"event_name": "JOLTS Job Openings", "limit": 1}`
   - This fetches only 1 event -> percentile_rank will have dataset_size=1 -> USELESS!

CORRECT (for percentile queries): `{"event_name": "JOLTS Job Openings", "limit": 50}`
   - This fetches 50 historical events -> percentile_rank has meaningful context!

**Guidelines:**
- `limit: 1` -> ONLY for "what was the most recent value" (no comparison needed)
- `limit: 50-100` -> For ANY query involving percentile, ranking, distribution, or historical comparison
- **When query mentions "percentile surprise":** You MUST use high limit (50+)!
- The SAME result provides BOTH `events[0]` (most recent) AND `events` (historical dataset)

**Output schema**: Returns `{"success": true, "events": [...], "count": N, "summary": {...}}`
Each event in `events` array contains:
- `event_name`, `event_id`, `country`, `importance` (high/medium/low)
- `event_date` (ISO datetime string) - ALSO available as `timestamp` for convenience
- `actual`, `forecast`, `previous`, `consensus` (numeric values or null)
- `surprise` (computed as actual - consensus, null if either is missing)
- `unit`, `ticker`, `source`

**⚠️ CRITICAL - Event Data ≠ Market Price Data:**
- **Event data contains ECONOMIC INDICATORS**: `actual`, `forecast`, `consensus`, `previous`, `surprise`
- **Event data does NOT contain**: `open`, `high`, `low`, `close`, `volume` (these are market prices!)
- **To plot price charts for event days**, you MUST:
  1. Get event dates from `query_event_history` (get the `event_date` or `timestamp` field)
  2. Use `run_query` to fetch market price data (OHLC) for those dates
  3. Then use `generate_candlestick_chart` with the market data

❌ WRONG: Trying to extract `open`, `high`, `low`, `close` from query_event_history output
✅ CORRECT: Use run_query to get OHLC data, then plot that data

**search_events** - Search for event definitions by keyword
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| keyword | str | YES | Search keyword |
| country | str | no | Country filter |
| importance | str | no | "low", "medium", "high" |
| limit | int | no | Max results (default: 50) |

**fetch_economic_calendar** - Update local calendar DB from API
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| country | str | no | Country filter |
| importance | str | no | Importance filter |
| full_refresh | bool | no | Full refresh (default: false) |

### runner_agent
Available tools: `["generate_structured_output", "build_runner_answer"]`

**build_runner_answer** - Final reasoning-enabled answer (PRIMARY TOOL)
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | str | AUTO | Original query (auto-injected) |
| context | str | no | Additional context |
| style | str | no | Response style |
| focus | str | no | Areas to emphasize |

**generate_structured_output** - Non-reasoning consolidation
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | str | AUTO | Original query (auto-injected) |
| format | str | no | Output format |
| fields | list | no | Fields to include |

NOTE: `query` is automatically injected for runner_agent tools - you don't need to include it.

### analytics_agent
Available tools: `["compute_statistics", "compute_percentile_rank", "compare_distributions", "compute_correlation", "generate_histogram", "generate_line_chart", "generate_candlestick_chart", "generate_multi_candlestick_charts", "compute_returns_distribution", "compute_volatility_distribution", "compute_event_distribution", "compute_surprise_price_distribution", ...]`

**generate_multi_candlestick_charts** - 🔴 USE THIS for "for each date, plot a chart" queries!
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| timeseries_list | list[dict] | YES | List of timeseries data (see format below) |
| width | int | NO | Chart width in pixels (default: 1600) |
| height | int | NO | Chart height in pixels (default: 600) |

**Input format for timeseries_list:**
```json
[
  {"title": "TY - NFP Dec 2024", "date": "2024-12-06", "data": [{"timestamp": "...", "open": 110.5, "high": 110.7, "low": 110.4, "close": 110.6}, ...]},
  {"title": "TY - NFP Dec 2023", "date": "2023-12-08", "data": [...]},
  ...
]
```

**Output**: `{charts: [{date: "2024-12-06", png_path: "...", num_candles: N}, ...], metadata: {...}}`

**Example for "plot chart for EACH event date":**
```json
{
  "generate_multi_candlestick_charts": {
    "timeseries_list": {"__task_ref__": "task_2", "__field__": "run_multi_date_query.results"}
  }
}
```
The `run_multi_date_query.results` has format: `[{date: "2024-12-06", data: [OHLC rows...]}, ...]`
This feeds directly into `generate_multi_candlestick_charts` which expects `[{date, data}, ...]`

**generate_candlestick_chart** - For SINGLE timeseries/price charts (shows OHLC candles)
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| open_data | list[float] | YES | Opening prices |
| high_data | list[float] | YES | High prices |
| low_data | list[float] | YES | Low prices |
| close_data | list[float] | YES | Closing prices |
| timestamps | list[str] | NO | Timestamps for x-axis labels |
| title | str | NO | Chart title |

**CRITICAL**: When user asks for "timeseries", "price chart", "price movement" without specifying "close only" or "line chart", use `generate_candlestick_chart` with OHLC data. The data query must fetch columns: `["timestamp", "open", "high", "low", "close"]`.

**CRITICAL - Extracting OHLC data from run_query for generate_candlestick_chart**:
- `run_query` returns: `{"data": [{"open": 108.3, "high": 108.5, "low": 108.2, "close": 108.4, "timestamp": "..."}, ...], "metadata": {...}}`
- You MUST use `__extract__` to get each column from the array:
```json
{
  "generate_candlestick_chart": {
    "open_data": {"__extract__": "open", "__from__": {"__task_ref__": "task_1", "__field__": "run_query.data"}},
    "high_data": {"__extract__": "high", "__from__": {"__task_ref__": "task_1", "__field__": "run_query.data"}},
    "low_data": {"__extract__": "low", "__from__": {"__task_ref__": "task_1", "__field__": "run_query.data"}},
    "close_data": {"__extract__": "close", "__from__": {"__task_ref__": "task_1", "__field__": "run_query.data"}},
    "timestamps": {"__extract__": "timestamp", "__from__": {"__task_ref__": "task_1", "__field__": "run_query.data"}},
    "title": "ZN Price on Dec 26, 2024"
  }
}
```

## ⚠️ CRITICAL: Field Validation - Know Your Data Sources

**BEFORE using `__task_ref__` or `__extract__`, verify the upstream tool actually returns the fields you need!**

| Tool | Returns These Fields | Does NOT Return |
|------|---------------------|-----------------|
| `run_query` | `open`, `high`, `low`, `close`, `volume`, `timestamp`, `symbol` | `actual`, `forecast`, `surprise` |
| `query_event_history` | `actual`, `forecast`, `consensus`, `previous`, `surprise`, `event_date` | `open`, `high`, `low`, `close`, `volume` |

**Common Mistake - DO NOT DO THIS:**
```json
// ❌ WRONG - query_event_history does NOT have open/high/low/close fields!
{
  "generate_candlestick_chart": {
    "open_data": {"__extract__": "open", "__from__": {"__task_ref__": "task_1", "__field__": "query_event_history.events"}},
    "high_data": {"__extract__": "high", "__from__": {"__task_ref__": "task_1", "__field__": "query_event_history.events"}}
  }
}
```

**Correct Pattern - Use the right data source:**
```json
// ✅ CORRECT - Use run_query output for OHLC data
{
  "generate_candlestick_chart": {
    "open_data": {"__extract__": "open", "__from__": {"__task_ref__": "task_2", "__field__": "run_query.data"}},
    "high_data": {"__extract__": "high", "__from__": {"__task_ref__": "task_2", "__field__": "run_query.data"}}
  }
}
```

**For "plot event day timeseries" requests, you need TWO data-fetching tasks:**
1. Task A: `query_event_history` → get event dates
2. Task B: `run_query` → fetch OHLC market data for those dates
3. Task C: `generate_candlestick_chart` → use data from Task B (NOT Task A!)

**PREFERRED TOOLS FOR DISTRIBUTION ANALYSIS:**

**compute_surprise_price_distribution** - BEST for economic event impact analysis (JOLTS, NFP, CPI, etc.)
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| event_name | str | YES | Economic event name (e.g., "JOLTS Job Openings", "Nonfarm Payrolls") |
| symbol | str | NO | Market symbol to analyze (default: "ZN") |
| country | str | NO | Country filter (default: "US") |
| delta_hrs | int | NO | Hours before/after event to analyze (default: 1) |
| limit | int | NO | Maximum events to analyze (default: 100) |
| custom_events | list | NO | Pre-fetched events from query_event_history (for integration) |

**USE THIS TOOL for queries like "plot price distribution around event" because it:**
- Buckets events by surprise magnitude (Big Beat, Small Beat, Inline, Small Miss, Big Miss)
- Analyzes price reactions for each bucket
- Generates distribution comparison plots showing price moves per surprise bucket
- Returns correlation between surprise magnitude and price reaction

**INTEGRATION WITH query_event_history:**
If a previous task used `query_event_history` to fetch events, pass them via `custom_events`:
```json
{
  "event_name": "JOLTS Job Openings",
  "symbol": "ZN",
  "delta_hrs": 1,
  "custom_events": {"__task_ref__": "task_1", "__field__": "query_event_history.events"}
}
```

**compare_distributions** - Compare before/after price distributions statistically (simpler alternative)

**compute_event_distribution** - For general event-specific return distributions
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| symbol | str | YES | Symbol to analyze (e.g., "ZN", "ES") |
| event_name | str | YES | Economic event name (e.g., "NFP", "JOLTS") |
| interval | str | NO | Data interval (default: "1h") |
| delta_hrs | int | NO | Hours to analyze around event (default: 1) |
| country | str | NO | Country filter (default: "US") |

**compare_distributions** - Compare two price datasets statistically
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| data_a | list[float] | YES | First dataset (e.g., prices before event) |
| data_b | list[float] | YES | Second dataset (e.g., prices after event) |
| label_a | str | NO | Label for first dataset |
| label_b | str | NO | Label for second dataset |

Returns statistical comparison: means, effect size (Cohen's d), overlap metrics.

**compute_percentile_rank** - Calculate percentile rank of a value within a dataset
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| value | float | YES | Single value to rank (NOT "values" - use "value" singular) |
| data | list[float] | NO | Reference dataset - optional if event_name provided |
| include_equal | bool | NO | Whether to count equal values (default: True) |
| event_name | str | NO | Economic event name to auto-fetch historical distribution |
| country | str | NO | Country filter for historical events (default: "US") |
| lookback_days | int | NO | Days to look back for historical events (default: 730) |

**CRITICAL - For Economic Event Surprise Percentiles:**
- **ALWAYS use `event_name` parameter** to fetch historical surprises from economic_events.db
- This provides meaningful percentile ranking against 2+ years of historical surprises
- Example:
```json
{
  "value": {"__task_ref__": "task_1", "__field__": "query_event_history.events[0].surprise"},
  "event_name": "JOLTS Job Openings",
  "country": "US",
  "lookback_days": 730
}
```
- Do NOT pass `data` when using `event_name` - the function will fetch historical surprises automatically

**ALTERNATIVE - Manual data array (only for non-event data)**:
- Use "value" (singular), NOT "values" (plural)
- The `data` parameter must be a flat list of numbers, not a list of dicts
- When referencing event history data:
  - Single value: `{"__task_ref__": "task_1", "__field__": "query_event_history.events[0].surprise"}`
  - Array of values: Use `extract_field` helper: `{"__extract__": "surprise", "__from__": {"__task_ref__": "task_1", "__field__": "query_event_history.events"}}`
  - This extracts all "surprise" values from the events array into a flat list of numbers

**generate_histogram** - Basic histogram (use compute_surprise_price_distribution instead for events!)
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| data | list[float] | YES | List of numeric values to plot |
| title | str | NO | Chart title (default: "Distribution") |
| bins | int | NO | Number of bins (default: 20) |
| x_label | str | NO | X-axis label |
| y_label | str | NO | Y-axis label |

**CRITICAL - Data from run_query**:
- `run_query` returns: `{"data": [{"close": 112.05}, {"close": 112.15}, ...], "metadata": {...}}`
- The `data` field is an ARRAY of objects, NOT a single object
- ❌ WRONG: `{"__task_ref__": "task_3", "__field__": "run_query.data.close"}` - This fails because `data` is an array!
- ✅ CORRECT: Use `__extract__` to get values from an array of objects:
  `{"__extract__": "close", "__from__": {"__task_ref__": "task_3", "__field__": "run_query.data"}}`
- To combine data from multiple tasks, use `__concat__` with `__extract__`:
```json
{
  "__concat__": [
    {"__extract__": "close", "__from__": {"__task_ref__": "task_3", "__field__": "run_query.data"}},
    {"__extract__": "close", "__from__": {"__task_ref__": "task_4", "__field__": "run_query.data"}}
  ]
}
```

Most other analytics tools require:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| data | list | YES* | Data array to analyze |
| symbol | str | YES* | Symbol to analyze |
| values | list | YES* | Values to process |
*At least one of data, symbol, or values required

## Input Format

```json
{
  "path_id": "path_1",
  "tasks": [
    {
      "task_id": "task_1",
      "description": "Query treasury futures prices for ZN on 2024-01-15",
      "agent": "market_data_agent",
      "agent_params": {"template": "by_symbol_and_date", "params": {"symbol_pattern": "%ZN%", "date": "2024-01-15"}},
      "dependencies": []
    }
  ],
  "available_tools": {"market_data_agent": ["run_query"]}
}
```

## Required Output Format (STRICT SCHEMA)

```json
{
  "path_id": "path_1",
  "tool_selections": {
    "task_1": {
      "selected_tools": ["run_query"],
      "reasoning": "Task requires SQL query for ZN futures prices on specific date",
      "tool_params": {
        "run_query": {
          "template": "by_symbol_and_date",
          "params": {"symbol_pattern": "%ZN%", "date": "2024-01-15"},
          "limit": 1000,
          "order_by_column": "timestamp",
          "order_by_direction": "DESC"
        }
      }
    }
  }
}
```

## Schema Requirements (MUST FOLLOW)

For each task in tool_selections:
- `selected_tools`: LIST of tool name strings (e.g., ["run_query"])
- `reasoning`: STRING explaining tool choice
- `tool_params`: DICT mapping tool_name -> parameters dict

## Examples

### Example 1: Market Data Query
Task: "Get Bitcoin option prices for July 2024"
```json
"tool_selections": {
  "task_1": {
    "selected_tools": ["run_query"],
    "reasoning": "Query market_data for BTC options",
    "tool_params": {
      "run_query": {
        "template": "by_symbol_and_date",
        "params": {"symbol_pattern": "%BTC%", "date": "2024-07-01"},
        "limit": 1000
      }
    }
  }
}
```

### Example 2: Polymarket Search
Task: "Find Bitcoin prediction markets and compare to last week"
```json
"tool_selections": {
  "task_2": {
    "selected_tools": ["search_polymarket_with_history"],
    "reasoning": "Unified tool provides current + historical data in one call",
    "tool_params": {
      "search_polymarket_with_history": {
        "query": "Bitcoin price predictions",
        "limit": 10,
        "days_back": 7
      }
    }
  }
}
```

### Example 3: Event Data
Task: "Get NFP historical data"
```json
"tool_selections": {
  "task_3": {
    "selected_tools": ["query_event_history"],
    "reasoning": "Need historical NFP data for analysis",
    "tool_params": {
      "query_event_history": {
        "event_name": "Non-Farm Payrolls",
        "country": "US",
        "lookback_days": 365,
        "limit": 12
      }
    }
  }
}
```

## Key Principles

1. **Schema Compliance**: ALWAYS include task_id, agent, tools (list), tool_params (dict per tool)
2. **Tool Validity**: Only select tools that are in the agent's available_tools list
3. **Parameter Completeness**: Include all REQUIRED parameters for each tool
4. **No Silent Drops**: If task mentions multiple things, cover each with appropriate tool(s)
5. **Right Tool for Job**: Match tool capabilities to task requirements

Return ONLY the JSON output, no additional text."""


class TaskPlannerClient:
    """
    Uses OpenAI GPT-4 for intelligent task decomposition
    and answer validation, inspired by Task Master's workflow principles.
    """
    
    def __init__(self):
        """Initialize task planner client."""
        self.config = self._load_config()
        self.client = None  # Will be initialized on first use
        logger.info("TaskPlannerClient initialized")
    
    def _load_config(self) -> Dict[str, str]:
        """
        Load AI API configuration from environment.
        
        Returns:
            Configuration dictionary
        """
        config = {}
        
        # Try to load from config/keys.env
        config_file = Path(__file__).parent.parent.parent / "config" / "keys.env"
        if config_file.exists():
            with open(config_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        config[key.strip()] = value.strip()
        
        # Override with environment variables
        for key in ['ANTHROPIC_API_KEY', 'OPENAI_API_KEY']:
            if key in os.environ:
                config[key] = os.environ[key]
        
        return config
    
    def _get_ai_client(self):
        if self.client is not None:
            return self.client
        
        # Try OpenAI first
        if self.config.get('OPENAI_API_KEY'):
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=self.config['OPENAI_API_KEY'])
                self.client_type = 'openai'
                logger.info("Using OpenAI for task planning")
                return self.client
            except ImportError:
                logger.warning("OpenAI library not installed")
        
        # Try Anthropic
        # if self.config.get('ANTHROPIC_API_KEY'):
        #     try:
        #         import anthropic
        #         self.client = anthropic.Anthropic(api_key=self.config['ANTHROPIC_API_KEY'])
        #         self.client_type = 'anthropic'
        #         logger.info("Using Anthropic for task planning")
        #         return self.client
        #     except ImportError:
        #         logger.warning("Anthropic library not installed")
        
        # No client available
        logger.warning("No AI API key available, will use fallback planning")
        return None
    
    def plan_task(
        self,
        query: str,
        available_agents: Optional[List[str]] = None,
        num_subtasks: int = 5
    ) -> Dict[str, Any]:
        logger.info(f"Planning task for query: '{query[:100]}...'")
        
        # Capture current date in UTC so Planner 1 can interpret relative dates correctly
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        current_date_section = (
            f"\n\nCurrent date (UTC): {current_date}. "
            "Interpret all relative date references (e.g., 'today', 'yesterday', "
            "'last N days', 'last month', 'recent') relative to this date."
        )
        
        # Build prompt for Planner 1 using the DSPy-style specification as core instructions.
        agent_registry_section = ""
        if available_agents:
            agent_registry_json = build_agent_registry_json(available_agents)
            if agent_registry_json and agent_registry_json != "[]":
                agent_registry_section = (
                    "\n\nAgent registry (JSON):\n"
                    f"{agent_registry_json}\n"
                    "Use this registry to decide which agent should own each task based on "
                    "their description, return type, and parameters."
                )
        
        # Add event dictionary context for eventdata_puller_agent tasks
        event_dict_context = ""
        if available_agents and "eventdata_puller_agent" in available_agents:
            event_dict_context = "\n\n" + get_event_dictionary_context(limit=1000)
            event_dict_context += "\n\n**IMPORTANT**: For eventdata_puller_agent tasks, use EXACT event names from the list above in the `event_name` parameter. Do not paraphrase or abbreviate."

        # Add glossary context for domain-specific terminology
        glossary_context = get_glossary_context()

        planning_prompt = (
            PLANNER1_PROMPT_CORE
            + "\n\n"
            + f"User query: {query}\n"
            + agent_registry_section
            + current_date_section
            + glossary_context
            + event_dict_context
        )
        
        try:

            result = self._call_ai_decompose(planning_prompt, num_subtasks, available_agents)
            
            # Post-process: normalize event names in subtask params
            result = self._normalize_plan_event_names(result)
            
            subtasks = result.get("subtasks", [])
            if not subtasks:
                # This is a common failure mode where the model returns a bare `[]`.
                logger.warning(
                    "Planner 1 returned 0 subtasks; using fallback planning. query=%r",
                    query[:200],
                )
                return self._fallback_plan(query, available_agents)
            
            logger.info("Task plan generated with %d subtasks", len(subtasks))
            return result
            
        except Exception as e:
            error_msg = f"AI planning failed: {e}"
            logger.error(error_msg)
            
            # Check if it's an API quota issue
            if '429' in str(e) or 'quota' in str(e).lower():
                raise Exception(
                    f"OpenAI API quota exceeded. Please check your API key billing.\n"
                    f"Error: {e}\n"
                    f"Solution: Add credits or wait for quota reset."
                ) from e
            else:
                logger.warning(
                    "AI task planning failed; using fallback planning. error=%s query=%r",
                    e,
                    query[:200],
                )
                return self._fallback_plan(query, available_agents)
    
    def _normalize_plan_event_names(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Post-process plan to normalize event names using fuzzy matching.
        
        Args:
            plan: Task plan from AI
            
        Returns:
            Plan with normalized event names
        """
        subtasks = plan.get("subtasks", [])
        
        for task in subtasks:
            agent = task.get("agent", "")
            if agent == "eventdata_puller_agent":
                # Normalize params
                params = task.get("params", {})
                country = params.get("country")
                task["params"] = normalize_event_params(params, country=country)
        
        return plan
    
    def select_tools_for_path(
        self,
        path_id: str,
        path_tasks: List[Dict[str, Any]],
        available_tools: Dict[str, List[str]],
        user_query: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        select tools and map parameters for a dependency path.
        
        Args:
            path_id: Unique identifier for this dependency path
            path_tasks: List of tasks in this path with agent_params from Planner 1
            available_tools: Dict mapping agent_name -> list of available tool names and their descriptions, parameters, input schema, output schema, example, usage
            
        Returns:
            Tool selection results with reasoning and mapped parameters
            
        Example output:
            {
                "path_id": "path_1",
                "tool_selections": {
                    "task_1": {
                        "selected_tools": ["run_query"],
                        "reasoning": "...",
                        "tool_params": {
                            "run_query": {...}
                        }
                    }
                }
            }
        """
        logger.info(f"Selecting tools for path '{path_id}' with {len(path_tasks)} tasks")
        
        # Capture current date in UTC so Planner 2 can interpret relative dates correctly
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        current_date_section = (
            f"\n\nCurrent date (UTC): {current_date}. "
            "Interpret all relative date references (e.g., 'today', 'yesterday', "
            "'last N days', 'last month', 'recent') relative to this date."
        )
        
        # Check if any task uses eventdata_puller_agent
        has_eventdata_agent = any(
            task.get("agent") == "eventdata_puller_agent" 
            for task in path_tasks
        )
        
        # Generate comprehensive tool documentation
        from src.agents.orchestrator_agent.tool_schema_generator import generate_tool_documentation
        tool_docs = generate_tool_documentation(available_tools)
        
        # Build input for AI
        input_data = {
            "path_id": path_id,
            "tasks": path_tasks,
            "available_tools": available_tools
        }
        
        # Add event dictionary context if needed
        event_dict_section = ""
        if has_eventdata_agent:
            event_dict_section = "\n\n" + get_event_dictionary_context(limit=1000)
            event_dict_section += "\n\n**IMPORTANT**: For eventdata_puller_agent tools, use EXACT event names from the list above. The `event_name`, `target_event_name`, and `keyword` parameters must match exactly."
        
        # Build prompt with auto-generated tool documentation
        prompt = f"""{PLANNER2_PROMPT_CORE}

## Tool Documentation (Auto-Generated from Schemas)
{tool_docs}
{event_dict_section}{current_date_section}

Input:
{json.dumps(input_data, indent=2)}

Analyze each task and return the tool selection JSON."""
        
        try:
            result = self._call_ai_tool_selection(prompt, path_id)
            
            # Post-process: normalize event names in tool_params
            result = self._normalize_tool_selection_event_names(result, path_tasks)
            
            # Post-process: extract month_filter from task descriptions and user query
            if user_query:
                result = self._extract_month_filter(result, path_tasks, user_query)
            
            # Post-process: fix event limits for distribution/percentile tasks
            tool_selections = result.get('tool_selections', {})
            if tool_selections:
                # Build task list with tool_params for the fixer function
                tasks_with_tools = []
                for task in path_tasks:
                    # Handle both 'task_id' (from planner_stage2) and 'id' (legacy) keys
                    task_id = task.get('task_id') or task.get('id')
                    selection = tool_selections.get(task_id, {})
                    tasks_with_tools.append({
                        'id': task_id,
                        'tools': selection.get('selected_tools', []),
                        'tool_params': selection.get('tool_params', {}),
                        'dependencies': task.get('dependencies', [])
                    })
                
                # Apply the fix
                fixed_tasks = fix_event_limit_for_percentile(tasks_with_tools, min_limit_for_percentile=50)
                
                # Update result with fixed tool_params
                for fixed_task in fixed_tasks:
                    task_id = fixed_task['id']
                    if task_id in tool_selections:
                        tool_selections[task_id]['tool_params'] = fixed_task['tool_params']
                
                logger.info("Applied event limit fixes for distribution/percentile tasks")
            
            # Validate tool parameters and data flow
            self._validate_tool_selections(result, path_tasks)
            
            logger.info(f"Tool selection complete for path '{path_id}': "
                       f"{len(result.get('tool_selections', {}))} tasks processed")
            return result
            
        except Exception as e:
            error_msg = f"AI tool selection failed for path '{path_id}': {e}"
            logger.error(error_msg)
            
            # Check if it's an API quota issue
            if '429' in str(e) or 'quota' in str(e).lower():
                raise Exception(
                    f"OpenAI API quota exceeded during tool selection for {path_id}.\n"
                    f"Error: {e}\n"
                    f"Solution: Add credits to your OpenAI account or wait for quota reset."
                ) from e
            else:
                raise Exception(
                    f"AI tool selection failed for path '{path_id}'.\n"
                    f"Error: {e}\n"
                    f"Path tasks: {[t.get('task_id') for t in path_tasks]}"
                ) from e
    
    def _extract_month_filter(
        self,
        result: Dict[str, Any],
        path_tasks: List[Dict[str, Any]],
        user_query: str
    ) -> Dict[str, Any]:
        """
        Post-process tool selection to extract month_filter from task descriptions and user query.
        Also removes lookback_days when month_filter is present.
        
        Args:
            result: Tool selection result from AI
            path_tasks: Original tasks (to get task descriptions)
            user_query: Original user query
            
        Returns:
            Result with month_filter extracted and lookback_days removed when appropriate
        """
        tool_selections = result.get("tool_selections", {})
        
        # Extract month from user query
        query_month = extract_month_from_text(user_query)
        
        for task in path_tasks:
            task_id = task.get("task_id") or task.get("id")
            task_description = task.get("description", "")
            
            if task_id not in tool_selections:
                continue
            
            selection = tool_selections[task_id]
            tool_params = selection.get("tool_params", {})
            
            # Check if this task uses query_event_history
            if "query_event_history" in tool_params:
                params = tool_params["query_event_history"]
                
                # Extract month from task description or user query
                month = extract_month_from_text(task_description) or query_month
                
                if month:
                    # Set month_filter
                    params["month_filter"] = month
                    logger.info(f"Extracted month_filter={month} for task {task_id} from '{task_description}' or user query")
                    
                    # Remove lookback_days when month_filter is present
                    if "lookback_days" in params:
                        del params["lookback_days"]
                        logger.info(f"Removed lookback_days from task {task_id} (month_filter is present)")
        
        return result
    
    def _normalize_tool_selection_event_names(
        self, 
        result: Dict[str, Any],
        path_tasks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Post-process tool selection to normalize event names using fuzzy matching.
        
        Args:
            result: Tool selection result from AI
            path_tasks: Original tasks (to get country context)
            
        Returns:
            Result with normalized event names in tool_params
        """
        tool_selections = result.get("tool_selections", {})
        
        # Build task_id -> country map
        task_countries = {}
        for task in path_tasks:
            task_id = task.get("task_id") or task.get("id")
            agent_params = task.get("agent_params", {})
            task_countries[task_id] = agent_params.get("country")
        
        for task_id, selection in tool_selections.items():
            tool_params = selection.get("tool_params", {})
            country = task_countries.get(task_id)
            
            # Normalize params for each tool
            for tool_name, params in tool_params.items():
                if tool_name in ["query_event_history", "search_events", "fetch_economic_calendar"]:
                    tool_params[tool_name] = normalize_event_params(params, country=country)
        
        return result
    
    def _validate_tool_selections(
        self,
        result: Dict[str, Any],
        path_tasks: List[Dict[str, Any]]
    ) -> None:
        """
        Validate tool selections for parameter completeness and data flow.
        
        Args:
            result: Tool selection result from AI
            path_tasks: Original tasks
        """
        from src.agents.orchestrator_agent.tool_schema_generator import (
            validate_tool_params,
            check_data_flow
        )
        
        tool_selections = result.get("tool_selections", {})
        
        # Build task dependencies map
        task_deps = {}
        for task in path_tasks:
            task_id = task.get("task_id") or task.get("id")
            task_deps[task_id] = task.get("dependencies", [])
        
        # Validate each task's tool parameters
        all_warnings = []
        for task_id, selection in tool_selections.items():
            tool_params = selection.get("tool_params", {})
            dependencies = task_deps.get(task_id, [])
            
            for tool_name, params in tool_params.items():
                is_valid, errors = validate_tool_params(tool_name, params, dependencies)
                if not is_valid:
                    for error in errors:
                        warning = f"Task {task_id}: {error}"
                        all_warnings.append(warning)
                        logger.warning(warning)
        
        # Check data flow between tasks
        is_valid, flow_warnings = check_data_flow(path_tasks, tool_selections)
        all_warnings.extend(flow_warnings)
        for warning in flow_warnings:
            logger.warning(f"Data flow: {warning}")
        
        if all_warnings:
            logger.warning(
                f"Found {len(all_warnings)} validation warnings. "
                "These may cause runtime errors if not addressed."
            )
    
    def validate_answer(
        self,
        query: str,
        answer: str,
        task_plan: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Use taskmaster to validate if answer satisfies query.
        
        Args:
            query: Original query
            answer: Generated answer
            task_plan: Optional task plan for context
            
        Returns:
            Validation result
            
        Example output:
            {
                "valid": True,
                "completeness_score": 0.95,
                "issues": [],
                "suggestions": []
            }
        """
        logger.info(f"Validating answer for query: '{query[:100]}...'")
        
        validation_prompt = f"""Validate if this answer completely and correctly addresses the query.

Query: {query}

Answer: {answer}

Check:
1. Does the answer address all parts of the query?
2. Is the information accurate and relevant?
3. Are there any missing components?

Return a validation report."""
        
        try:
            result = self._call_ai_validate(validation_prompt, query, answer)
            
            logger.info(f"Validation complete: {'PASSED' if result.get('valid') else 'FAILED'}")
            return result
            
        except Exception as e:
            logger.error(f"AI validation failed: {e}")
            # Fallback: basic validation
            return self._fallback_validation(query, answer)
    
    def _call_ai_decompose(self, prompt: str, num_subtasks: int, available_agents: Optional[List[str]]) -> Dict[str, Any]:
        """
        Call AI API for task decomposition.
        
        Args:
            prompt: Planning prompt
            num_subtasks: Number of subtasks
            available_agents: List of available agents
            
        Returns:
            Task plan with subtasks
        """
        client = self._get_ai_client()
        
        if client is None:
            raise Exception("No AI client available")
        
        agent_context = ""
        if available_agents:
            agent_context = f"\n\nAvailable worker agents:\n" + "\n".join(f"- {agent}" for agent in available_agents)
        
        full_prompt = f"""INSTRUCTION: You must respond with ONLY a valid JSON array. No explanations, no prose, no markdown text outside the JSON. Start your response with '[' and end with ']'.

{prompt}
        
{agent_context}

REMINDER: Your entire response must be ONLY the JSON array of tasks. Do not include any text before or after the JSON."""
        
        try:
            if self.client_type == 'openai':
                response = client.chat.completions.create(
                    model="gpt-4-turbo",  # 128K context window
                    messages=[{"role": "user", "content": full_prompt}],
                    temperature=0.2,
                    max_tokens=1500,
                )
                content = response.choices[0].message.content.strip()
            else:  # anthropic
                response = client.messages.create(
                    model="claude-3-sonnet-20240229",
                    max_tokens=1500,
                    temperature=0.2,
                    messages=[{"role": "user", "content": full_prompt}],
                )
                content = response.content[0].text.strip()

            # Parse JSON response robustly (handle extra reasoning text, labels, fences)
            logger.debug("LLM returned %d chars, extracting JSON...", len(content))
            json_text = self._extract_task_graph_json(content)
            
            if json_text != content:
                logger.debug("Extracted JSON differs from raw content (extracted %d chars)", len(json_text))
            
            parsed = json.loads(json_text)
            # Some models may return a bare list of tasks rather than an object
            if isinstance(parsed, list):
                result: Dict[str, Any] = {"subtasks": parsed}
            else:
                result = parsed

            result["method"] = self.client_type
            logger.info("Successfully parsed task graph with %d subtasks", len(result.get("subtasks", [])))
            return result

        except json.JSONDecodeError as e:
            logger.error(
                "JSON parsing failed: %s\n"
                "Raw content preview (first 500 chars):\n%s\n"
                "Extracted json_text (first 500 chars):\n%s",
                e,
                content[:500],
                json_text[:500] if 'json_text' in locals() else 'N/A'
            )
            raise
        except Exception as e:
            logger.error("AI decomposition failed: %s (raw content preview=%r)", e, content[:200])
            raise

    def _extract_task_graph_json(self, content: str) -> str:
        """Extract the JSON payload (task graph) from an LLM response.

        Handles mixed responses that may include:
        - Prefixed reasoning text (\"Reasoning: ...\")
        - Section labels like \"Task Graph Json:\"
        - Markdown code fences ``` or ```json
        - Either an object or a bare JSON array
        
        Returns only valid JSON that can be parsed by json.loads().
        """
        text = content.strip()

        # Helper to validate a candidate is actual JSON
        def is_valid_json(candidate: str) -> bool:
            try:
                json.loads(candidate)
                return True
            except:
                return False

        # 1) If wrapped in markdown fences, strip them first
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 2:
                inner = parts[1].strip()
                if inner.startswith("json"):
                    inner = inner[4:].strip()
                if is_valid_json(inner):
                    return inner
                # Otherwise continue to other extraction methods

        # 2) If it already looks like JSON, validate before returning
        if (text.startswith("{") and text.rstrip().endswith("}")) or (
            text.startswith("[") and text.rstrip().endswith("]")
        ):
            if is_valid_json(text):
                return text

        # 3) Look for explicit "Task Graph Json:" marker and take what follows
        marker = "Task Graph Json:"
        idx = text.find(marker)
        if idx != -1:
            sub = text[idx + len(marker) :].strip()
            # Remove optional colon or equals after the label
            if sub.startswith(":"):
                sub = sub[1:].strip()
            # Handle fences again in the subsection
            if sub.startswith("```"):
                parts = sub.split("```")
                if len(parts) >= 2:
                    sub = parts[1].strip()
                if sub.startswith("json"):
                    sub = sub[4:].strip()
            
            # Try to extract JSON from this section
            for opener, closer in (("[", "]"), ("{", "}")):
                if sub and sub[0] == opener:
                    # Find matching closer
                    start = 0
                    end = sub.rfind(closer)
                    if end != -1:
                        candidate = sub[start : end + 1].strip()
                        if is_valid_json(candidate):
                            return candidate

        # 4) Fallback: extract first valid JSON-looking bracketed region
        # Try both array and object, validate each candidate
        for opener, closer in (("[", "]"), ("{", "}")):
            start = text.find(opener)
            if start == -1:
                continue
            
            # Find matching closer by counting nesting
            depth = 0
            end = -1
            for i in range(start, len(text)):
                if text[i] == opener:
                    depth += 1
                elif text[i] == closer:
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            
            if end != -1:
                candidate = text[start : end + 1].strip()
                if is_valid_json(candidate):
                    return candidate

        # 5) Last resort: try to find ANY valid JSON substring
        # Look for opening bracket and try progressively longer slices
        for opener, closer in (("[", "]"), ("{", "}")):
            start = text.find(opener)
            if start != -1:
                # Try to find the longest valid JSON from this start
                for end_idx in range(len(text) - 1, start, -1):
                    if text[end_idx] == closer:
                        candidate = text[start : end_idx + 1].strip()
                        if is_valid_json(candidate):
                            return candidate

        # Nothing worked - return original and let json.loads raise a clear error
        return text
    
    def _call_ai_tool_selection(self, prompt: str, path_id: str) -> Dict[str, Any]:
        """
        Call AI API for tool selection and parameter mapping.
        
        Args:
            prompt: Tool selection prompt with task context
            path_id: Path identifier
            
        Returns:
            Tool selection results
        """
        client = self._get_ai_client()
        
        if client is None:
            raise Exception("No AI client available")
        
        try:
            if self.client_type == 'openai':
                response = client.chat.completions.create(
                    model="gpt-4-turbo",  # 128K context window
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=2000,
                )
                content = response.choices[0].message.content.strip()
            else:  # anthropic
                response = client.messages.create(
                    model="claude-3-sonnet-20240229",
                    max_tokens=2000,
                    temperature=0.2,
                    messages=[{"role": "user", "content": prompt}],
                )
                content = response.content[0].text.strip()
            
            # Parse JSON response
            json_text = self._extract_task_graph_json(content)
            result = json.loads(json_text)
            result["method"] = self.client_type
            
            return result
            
        except Exception as e:
            logger.error(f"AI tool selection failed: {e}")
            raise
    
    def _call_ai_validate(self, prompt: str, query: str, answer: str) -> Dict[str, Any]:
        """
        Call AI API for answer validation.
        
        Args:
            prompt: Validation prompt
            query: Original query
            answer: Generated answer
            
        Returns:
            Validation result
        """
        client = self._get_ai_client()
        
        if client is None:
            raise Exception("No AI client available")
        
        full_prompt = f"""{prompt}

Return ONLY a JSON object with this structure:
{{
    "valid": true/false,
    "completeness_score": 0.0-1.0,
    "issues": ["issue1", "issue2"],
    "suggestions": ["suggestion1", "suggestion2"]
}}

Be strict: valid should be true only if the answer fully addresses ALL parts of the query."""
        
        try:
            if self.client_type == 'openai':
                response = client.chat.completions.create(
                    model="gpt-4-turbo",  # 128K context window
                    messages=[{"role": "user", "content": full_prompt}],
                    temperature=0.1,
                    max_tokens=500
                )
                content = response.choices[0].message.content.strip()
            else:  # anthropic
                response = client.messages.create(
                    model="claude-3-sonnet-20240229",
                    max_tokens=500,
                    temperature=0.1,
                    messages=[{"role": "user", "content": full_prompt}]
                )
                content = response.content[0].text.strip()
            
            # Parse JSON response
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            
            result = json.loads(content)
            result["method"] = self.client_type
            return result
            
        except Exception as e:
            logger.error(f"AI validation failed: {e}")
            raise
    
    def _fallback_plan(self, query: str, available_agents: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Simple rule-based planning fallback.
        
        Args:
            query: Natural language query
            available_agents: Available agent names
            
        Returns:
            Basic task plan
        """
        logger.info("Using fallback planning")
        
        query_lower = query.lower()
        subtasks = []
        task_id = 1

        # Candlestick / chart requests with explicit dates (common orchestrator query).
        # If the LLM returns `[]` (or errors), we still want a workable plan.
        is_chart_request = any(k in query_lower for k in ["candle", "candlestick", "candle plot", "chart", "plot"])
        has_date_signal = (
            any(m in query_lower for m in [
                "january", "february", "march", "april", "may", "june",
                "july", "august", "september", "october", "november", "december",
            ])
            or re.search(r"\b(19|20)\d{2}\s*:", query_lower)
            or re.search(r"\b\d{4}-\d{2}-\d{2}\b", query_lower)
        )
        if is_chart_request and has_date_signal:
            # Best-effort symbol detection; default to TY for this repo's common workflows.
            symbol_match = re.search(r"\b(TY|ZN|ZB|ZF|ZT|ES|NQ|YM|RTY)\b", query, flags=re.IGNORECASE)
            symbol = (symbol_match.group(1).upper() if symbol_match else "TY")
            symbol_pattern = f"{symbol}%"

            iso_dates = self._extract_dates_from_query(query)
            if iso_dates:
                chart_task_ids: List[int] = []

                # One data task + one chart task per date (simple + robust for Stage 2 tool selection)
                for iso_date in iso_dates:
                    data_task_id = task_id
                    subtasks.append({
                        "id": data_task_id,
                        "description": f"Retrieve OHLC data for {symbol} futures on {iso_date}",
                        "agent": "market_data_agent",
                        "params": {
                            "template": "by_date",
                            "params": {"symbol_pattern": symbol_pattern, "date": iso_date},
                            "columns": ["timestamp", "open", "high", "low", "close"],
                            "limit": 10000,
                        },
                        "dependencies": []
                    })
                    task_id += 1

                    chart_task_id = task_id
                    subtasks.append({
                        "id": chart_task_id,
                        "description": f"Generate candlestick chart for {symbol} futures on {iso_date}",
                        "agent": "analytics_agent",
                        "params": {"analysis_type": "candlestick", "generate_plot": True},
                        "dependencies": [data_task_id]
                    })
                    chart_task_ids.append(chart_task_id)
                    task_id += 1

                subtasks.append({
                    "id": task_id,
                    "description": "Consolidate results and provide final answer with chart file paths",
                    "agent": "runner_agent",
                    "dependencies": chart_task_ids
                })

                return {
                    "query": query,
                    "subtasks": subtasks,
                    "execution_order": [[st["id"]] for st in subtasks],
                    "method": "fallback"
                }
        
        # Check for prediction market keywords
        if any(kw in query_lower for kw in ['predict', 'polymarket', 'forecast', 'opinion', 'probability']):
            subtasks.append({
                "id": task_id,
                "description": f"Search prediction markets for: {query}",
                "agent": "polymarket_agent",
                "dependencies": []
            })
            task_id += 1
        
        # Check for SQL/market data keywords
        if any(kw in query_lower for kw in ['price', 'market data', 'sql', 'database', 'bid', 'ask']):
            subtasks.append({
                "id": task_id,
                "description": f"Query market data database for: {query}",
                "agent": "market_data_agent",
                "dependencies": []
            })
            task_id += 1
        
        # Check for historical/analysis keywords
        if any(kw in query_lower for kw in ['historical', 'compare', 'trend', 'analysis', 'was', 'were']):
            subtasks.append({
                "id": task_id,
                "description": f"Analyze historical market data for: {query}",
                "agent": "polymarket_agent",
                "dependencies": []
            })
            task_id += 1
        
        # If no subtasks identified, create a general search task
        if not subtasks:
            subtasks.append({
                "id": 1,
                "description": f"Search for: {query}",
                "agent": "polymarket_agent",
                "dependencies": []
            })
        
        return {
            "query": query,
            "subtasks": subtasks,
            # Each independent task in its own row so they can run in parallel
            "execution_order": [[st["id"]] for st in subtasks],
            "method": "fallback"
        }

    def _extract_dates_from_query(self, query: str) -> List[str]:
        """Extract ISO dates (YYYY-MM-DD) from a query, best-effort and unique."""
        out: set[str] = set()
        q = query.strip()

        # 1) ISO dates already present
        for iso in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", q):
            out.add(iso)

        # 2) Month-name dates with year (comma optional)
        month_pat = (
            r"(january|february|march|april|may|june|july|august|september|october|november|december)"
            r"\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*((?:19|20)\d{2})"
        )
        for mon, day, year in re.findall(month_pat, q, flags=re.IGNORECASE):
            try:
                dt = datetime.strptime(f"{mon} {day} {year}", "%B %d %Y")
                out.add(dt.strftime("%Y-%m-%d"))
            except Exception:
                continue

        # 3) Year-block format: "YYYY: december 29, december 30 ..."
        year_matches = list(re.finditer(r"\b(?P<year>(?:19|20)\d{2})\s*:", q))
        if year_matches:
            months = {
                "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
                "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
            }
            for i, m in enumerate(year_matches):
                year = int(m.group("year"))
                start = m.end()
                end = year_matches[i + 1].start() if i + 1 < len(year_matches) else len(q)
                section = q[start:end]
                for month_str, day_str in re.findall(
                    r"\b("
                    r"january|february|march|april|may|june|july|august|september|october|november|december"
                    r")\s+(\d{1,2})\b",
                    section,
                    flags=re.IGNORECASE,
                ):
                    month = months.get(month_str.lower())
                    if not month:
                        continue
                    try:
                        dt = datetime(year, month, int(day_str))
                        out.add(dt.strftime("%Y-%m-%d"))
                    except Exception:
                        continue

        return sorted(out)
    
    def _fallback_tool_selection(
        self,
        path_id: str,
        path_tasks: List[Dict[str, Any]],
        available_tools: Dict[str, List[str]]
    ) -> Dict[str, Any]:
        """
        Fallback tool selection - load all available tools for each agent.
        
        Args:
            path_id: Path identifier
            path_tasks: List of tasks in path
            available_tools: Available tools per agent
            
        Returns:
            Tool selection with all tools loaded
        """
        logger.info(f"Using fallback tool selection for path '{path_id}'")
        
        tool_selections = {}
        
        for task in path_tasks:
            task_id = task.get('task_id') or task.get('id')
            agent_name = task.get('agent') or task.get('assigned_agent')
            agent_params = task.get('agent_params', {})
            
            # Select all available tools for this agent
            agent_tools = available_tools.get(agent_name, [])
            
            # Build tool params from agent params (simple pass-through)
            tool_params = {}
            for tool_name in agent_tools:
                tool_params[tool_name] = agent_params
            
            tool_selections[task_id] = {
                "selected_tools": agent_tools,
                "reasoning": f"Fallback: loading all available tools for {agent_name}",
                "tool_params": tool_params
            }
        
        return {
            "path_id": path_id,
            "tool_selections": tool_selections,
            "method": "fallback"
        }
    
    def _fallback_validation(self, query: str, answer: str) -> Dict[str, Any]:
        """
        Simple validation fallback.
        
        Args:
            query: Original query
            answer: Generated answer
            
        Returns:
            Basic validation result
        """
        logger.info("Using fallback validation")
        
        # Simple heuristics
        has_content = len(answer) > 50
        query_words = set(query.lower().split())
        answer_words = set(answer.lower().split())
        overlap = len(query_words & answer_words) / max(len(query_words), 1)
        
        valid = has_content and overlap > 0.3
        
        return {
            "valid": valid,
            "completeness_score": min(overlap, 1.0),
            "issues": [] if valid else ["Answer may be incomplete"],
            "suggestions": [],
            "method": "fallback"
        }

