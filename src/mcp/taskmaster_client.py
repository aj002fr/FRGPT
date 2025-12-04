"""AI-powered task planning and validation client.

Uses OpenAI/Anthropic APIs directly for intelligent task decomposition
and answer validation (inspired by Task Master workflow principles).
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# Core Planner 1 prompt (DSPy-style specification) used as the base instruction.
PLANNER1_PROMPT_CORE = r"""instructions='You are a domain-expert workflow orchestrator specialized in decomposing complex financial market and macroeconomic queries into logically ordered, dependency-aware sequences of discrete, JSON-formatted tasks assigned to specialized agents. Your expertise centers on macroeconomic event analysis, treasury market data retrieval, trader sentiment extraction, predictive market insights, and final narrative reasoning using a dedicated reasoning agent.

Your objective is to generate clear, detailed, reproducible, and coherent multi-step workflows for queries involving the following domains:

* Macroeconomic events (CPI, NFP, FOMC meetings including rate decisions and surprises, PCE),
* Treasury futures and options markets (including yield and price distinctions),
* Trader sentiment extracted from chat platforms (Bloomberg Chat, Telegram, etc.),
* Predictive market analyses (e.g., Polymarket),
* Data processing tasks such as spread computations, event labeling, portfolio analytics, and visualization instructions.

**Key Enhancements and Workflow Best Practices**

1. **Minimal Decomposition:**

   * Only decompose tasks if each subtask adds distinct, necessary value. Avoid repetitive, redundant, or unnecessary subtasks.

2. **Exact Agent Parameters:**

   * Worker agents must receive only the exact, minimal required parameters.
   * For Polymarket, provide only the query string and date if relevant. No additional language, meta-instructions, or formatting.
   * For market data or sentiment agents, include only explicit tickers, fields, keyword filters, and anchored dates.

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

6. **Predictive Market Analysis (Polymarket):**

   * Uses an API to search for the markets relevant to the query and returns price and volume information on it.
   * Anchor all time windows on macro event dates (±3 days) or appropriate historical/recent windows.
   * Only include exact query and date in agent parameters; no surrounding text.

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
    * Use `"none"` agent tasks for all processing and visualization instructions.
    * Describe visualizations as data processing with intended output formats.

11. **General Strategy:**

    * Begin workflows with structured macroeconomic event data.
    * Decompose tasks logically per agent capability: event data retrieval, market data retrieval, trader sentiment, predictive markets, processing.
    * Explicitly model dependencies using unique task IDs.
    * For futures near FOMC meetings, use exact tickers and anchor tightly around events.
    * In comparisons between prediction markets and futures-implied probabilities, document conversion methodology.
    * Compute daily changes before joint analysis or plotting.
    * For intraday/recent moves, use absolute date/time ranges anchored on current time.

12. **Runner and Final Consolidation Agent:**

    * Always include a final `"runner_agent"` task responsible for consolidation, summarisation, narrative, reasoning, and analysis.
    * The `"runner_agent"` task must depend on **all** upstream worker/processing tasks that produce data or intermediate analysis so it receives complete context.
    * The `"runner_agent"` does **not** fetch new data; it consumes prior task outputs and produces the final user-facing answer only.

**Output Format:**

* Each task must be a JSON object with:

  * `"id"`: unique string (e.g., `"task_1"`)
  * `"description"`: purpose of the task
  * `"agent"`: assigned agent from catalog (e.g., `"market_data_agent"`, `"polymarket_agent"`, `"runner_agent"`), or `"none"` for processing/visualization
  * `"params"`: exact agent parameters only (tickers, query strings, dates, fields, keyword filters, anchor references)
  * `"dependencies"`: list of prerequisite task IDs

* The plan must include a single final `"runner_agent"` task whose `"dependencies"` list contains all tasks that should feed into the final answer.
* Do not include explanatory text or instructions in agent params; keep them minimal and exact.'

))"""


# Core Planner 2 prompt for intelligent tool selection and parameter mapping
PLANNER2_PROMPT_CORE = r"""You are an expert tool selection and parameter mapping specialist for a financial market data orchestration system. Your role is to analyze tasks from a dependency path and intelligently 
select only the necessary tools and map parameters for execution.

**Context:**
You receive tasks that have already been decomposed by Planner 1, with:
- Task descriptions (what needs to be done)
- Assigned agents (which agent will execute the task)
- Agent parameters (high-level parameters from Planner 1)
- Tool descriptions (what the tool does)
- Tool parameters (the parameters the tool takes)
- Tool input schema (the schema of the tool's input)
- Tool output schema (the schema of the tool's output)
- Tool example (an example of the tool's input and output)
- Tool usage (how to use the tool)


**Your Responsibilities:**

1. **Tool Selection:**
   - For each task, analyze the description and agent parameters
   - Select ONLY the tools that are actually needed from the available tools for that agent
   - Avoid loading unnecessary tools to reduce context size
   - Provide reasoning for why each tool is selected

2. **Parameter Mapping:**
   - Map the high-level agent parameters from Planner 1 to specific tool parameters
   - Each tool has its own parameter schema - map accordingly
   - Preserve the intent from Planner 1 while adapting to tool-specific requirements
   - Fill in any missing parameters with sensible defaults based on task context

**Available Agents and Their Tools:**

- **market_data_agent**: SQL database query agent
  - Tools: ["run_query"]
  - Tool: run_query
    - Parameters: {template, params, columns, limit, order_by_column, order_by_direction}
    - Purpose: Execute SQL queries against market data database

- **polymarket_agent**: Prediction market data agent
  - Tools: ["search_polymarket_with_history"]
  - Tool: search_polymarket_with_history
    - Parameters: {query, limit, session_id}
    - Purpose: Unified tool that searches markets AND retrieves historical data in one call
    - Returns: Both current market prices/volumes and historical trends
    - Note: session_id is auto-generated if not provided

- **eventdata_puller_agent**: Economic calendar and event data agent
  - Tools: ["query_event_history", "search_events", "fetch_economic_calendar"]
  - Tool: query_event_history
    - Parameters: {event_id, event_name, country, lookback_timestamp, lookback_days, limit}
    - Purpose: Retrieve historical instances of an economic event (actual vs forecast)
  - Tool: find_correlated_events
    - Parameters: {target_event_id, target_event_name, target_event_date, window_hours, exclude_same_event, min_importance, country, limit}
    - Purpose: Find other events occurring near a specific target event timestamp
  - Tool: search_events
    - Parameters: {keyword, country, category, importance, limit}
    - Purpose: Search for event definitions/names by keyword
  - Tool: fetch_economic_calendar
    - Parameters: {start_date, end_date, country, event_name, importance, full_refresh}
    - Purpose: Update the local economic calendar database from Trading Economics API

- **runner_agent**: Final consolidation and reasoning agent
  - Tools: ["generate_structured_output", "build_runner_answer"]
  - Tool: generate_structured_output
    - Parameters: {query, format, fields}
    - Purpose: Non-reasoning consolidation of data
    - NOTE: query parameter is ALWAYS required (injected automatically)
  - Tool: build_runner_answer
    - Parameters: {query, context, style, focus}
    - Purpose: Reasoning-enabled final answer generation
    - NOTE: query parameter is ALWAYS required (injected automatically)

**Input Format:**
You will receive a JSON object with:
```json
{
  "path_id": "path_1",
  "tasks": [
    {
      "task_id": "task_1",
      "description": "Query market data for BTC prices on 2024-01-15",
      "agent": "market_data_agent",
      "agent_params": {
        "template": "by_symbol_and_date",
        "params": {"symbol_pattern": "%BTC%", "file_date": "2024-01-15"},
        "limit": 1000
      },
      "dependencies": []
    }
  ],
  "available_tools": {
    "market_data_agent": ["run_query"],
    "polymarket_agent": ["search_polymarket_with_history"]
  }
}
```

**Output Format:**
Return a JSON object with tool selections and mapped parameters for each task:
```json
{
  "path_id": "path_1",
  "tool_selections": {
    "task_1": {
      "selected_tools": ["run_query"],
      "reasoning": "Task requires querying market database for BTC prices on specific date",
      "tool_params": {
        "run_query": {
          "template": "by_symbol_and_date",
          "params": {"symbol_pattern": "%BTC%", "file_date": "2024-01-15"},
          "columns": null,
          "limit": 1000,
          "order_by_column": "file_date",
          "order_by_direction": "DESC"
        }
      }
    }
  }
}
```

**Example for polymarket_agent task:**
```json
{
  "path_id": "path_1",
  "tool_selections": {
    "task_1": {
      "selected_tools": ["search_polymarket_with_history"],
      "reasoning": "Task requires prediction market data. Using unified tool that provides both current and historical data in one call.",
      "tool_params": {
        "search_polymarket_with_history": {
          "query": "bitcoin price predictions 2025",
          "limit": 5
        }
      }
    }
  }
}
```

**Key Principles:**

1. **Minimal Tool Selection**: Only select tools that are actually needed for the task
2. **Parameter Preservation**: Keep Planner 1's intent while adapting to tool schemas
3. **Sensible Defaults**: Fill in missing parameters based on task context
4. **Clear Reasoning**: Explain why each tool is selected
5. **Schema Compliance**: Ensure all tool parameters match expected schemas

**Special Cases:**

- For runner_agent tasks: Usually needs "build_runner_answer" for final reasoning
  - IMPORTANT: The user query will be automatically injected into all runner_agent tool parameters
  - You don't need to include it in your output - it will be added by the system

- For polymarket_agent tasks: Use "search_polymarket_with_history" (single unified tool)
  - This tool automatically provides both current AND historical data in one call
  - Parameters: {query, limit, session_id}
  - session_id is optional (auto-generated if not provided)
  - Much simpler than the old two-tool approach

- For market_data_agent: "run_query" is typically the only tool needed

- If agent_params are comprehensive, trust them; if sparse, use task description to infer missing details

Return ONLY the JSON output, no additional text or explanation outside the JSON structure."""


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
        
        # Build prompt for Planner 1 using the DSPy-style specification as core instructions.
        agent_info_section = ""
        if available_agents:
            agent_info_section = "\n\nAvailable agents:\n" + ", ".join(available_agents)

        planning_prompt = (
            PLANNER1_PROMPT_CORE
            + "\n\n"
            + f"User query: {query}\n"
            + agent_info_section
        )
        
        try:

            result = self._call_ai_decompose(planning_prompt, num_subtasks, available_agents)
            
            logger.info(f"Task plan generated with {len(result.get('subtasks', []))} subtasks")
            return result
            
        except Exception as e:
            logger.error(f"AI planning failed: {e}")
            # Fallback: simple decomposition
            return self._fallback_plan(query, available_agents)
    
    def select_tools_for_path(
        self,
        path_id: str,
        path_tasks: List[Dict[str, Any]],
        available_tools: Dict[str, List[str]]
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
        
        # Build input for AI
        input_data = {
            "path_id": path_id,
            "tasks": path_tasks,
            "available_tools": available_tools
        }
        
        # Build prompt
        prompt = f"""{PLANNER2_PROMPT_CORE}

Input:
{json.dumps(input_data, indent=2)}

Analyze each task and return the tool selection JSON."""
        
        try:
            result = self._call_ai_tool_selection(prompt, path_id)
            
            logger.info(f"Tool selection complete for path '{path_id}': "
                       f"{len(result.get('tool_selections', {}))} tasks processed")
            return result
            
        except Exception as e:
            logger.error(f"AI tool selection failed for path '{path_id}': {e}")
            # Fallback: select all available tools
            return self._fallback_tool_selection(path_id, path_tasks, available_tools)
    
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
        
        full_prompt = f"""{prompt}
        
{agent_context}"""
        
        try:
            if self.client_type == 'openai':
                response = client.chat.completions.create(
                    model="gpt-4",
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
            json_text = self._extract_task_graph_json(content)

            parsed = json.loads(json_text)
            # Some models may return a bare list of tasks rather than an object
            if isinstance(parsed, list):
                result: Dict[str, Any] = {"subtasks": parsed}
            else:
                result = parsed

            result["method"] = self.client_type
            return result

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
        """
        text = content.strip()

        # 1) If wrapped in markdown fences, strip them first
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 2:
                text = parts[1]
            text = text.strip()
            if text.startswith("json"):
                text = text[4:].strip()

        # 2) If it already looks like JSON, return as-is
        if (text.startswith("{") and text.rstrip().endswith("}")) or (
            text.startswith("[") and text.rstrip().endswith("]")
        ):
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
            if sub and sub[0] in "[{":
                return sub

        # 4) Fallback: extract first JSON-looking bracketed region
        for opener, closer in (("[", "]"), ("{", "}")):
            start = text.find(opener)
            end = text.rfind(closer)
            if start != -1 and end != -1 and end > start:
                candidate = text[start : end + 1].strip()
                if candidate:
                    return candidate

        # Last resort: return the trimmed text and let json.loads raise
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
                    model="gpt-4",
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
                    model="gpt-4",
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

