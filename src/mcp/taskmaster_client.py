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
        
        # Build prompt for taskmaster
        agent_info = ""
        if available_agents:
            agent_info = f"\n\nAvailable agents: {', '.join(available_agents)}"
        
        planning_prompt = f"""Decompose this query into tasks <= {num_subtasks} or fewer subtasks that can be delegated to worker agents.

Query: {query}
{agent_info}

You are tasked with generating a series of structured tasks in JSON format to answer user queries related to financial markets, focusing on macroeconomic events, market data, trader sentiment, and predictive markets.

1. Query types and domain context:
   - Queries may involve macroeconomic event data (for example CPI, NFP, FOMC), treasury futures and options (for example TY, 2Y, 10Y futures), trader sentiment from chat messages, predictive market probabilities (for example Polymarket), and related news.
   - Users can request historical and current data, comparative analyses, surprises versus expectations, event-driven price moves, sentiment summaries, distributions, and probability estimations.
   - Events such as CPI, NFP, and FOMC are critical anchors for data retrieval and analysis.
   - Instruments like treasury futures (TY), treasury notes (2Y, 10Y), options skew, and swaptions are common data subjects.

2. Agent capabilities and usage:
   - `event_data_puller_agent`: Retrieves structured macro event data including event dates, expected versus actual values, and surprise labels (positive or negative). Often used to get event dates and metrics spanning user-specified historical ranges.
   - `market_data_agent`: Executes parameterized SQL queries on market data tables. Used to retrieve prices, volumes, bid or ask, and related fields for specified instruments and date windows. Supports filtering by symbol, date ranges, and sampling frequency (tick, intraday, daily).
   - `message_puller_agent`: Retrieves trader chat messages filtered by keyword patterns, channels or groups, and time windows (which can be anchored around events). Used for sentiment and qualitative analysis.
   - `polymarket_agent`: Queries Polymarket prediction markets by natural language, returning probabilities, volumes, liquidity, and historical price series. Useful for fetching current prediction market states and trends.
   - `web_search_agent`: Searches macro-relevant news and articles, filtered by time windows, to provide contextual or recent analysis affecting markets.
   - `calender_checker_agent`: Specialized for finding historical prices and event-related data tied to scheduled macro events.
   - `distribution_wizard_agent`: Performs statistical analysis and plotting of historical data distributions, especially event-driven return distributions.

3. Task decomposition and dependencies:
   - Break down the user query into discrete, actionable subtasks aligned with specific agent capabilities.
   - When the query involves event-driven analysis, use `event_data_puller_agent` first to identify relevant macro event dates and metrics.
   - Use event dates as anchors for querying market data or chat messages within relative windows (for example 3 days before and after each CPI event).
   - Retrieve market data for specified instruments and time windows relevant to those events.
   - For sentiment analysis, query message streams filtered by keywords and time windows anchored around events or recent periods.
   - For predictive market queries, use `polymarket_agent` directly with the natural language query and optionally request historical data for trend analysis.
   - Use `distribution_wizard_agent` after obtaining raw price data when the query asks for distributions, histograms, or event-conditioned statistics.

4. Task output format:
   - Represent each task as a JSON object with at least these keys:
     - `id`: A unique identifier for the task (string, for example "task_1", "task_2").
     - `description`: A clear description of what the task is supposed to accomplish (string).
     - `dependencies`: An array (list) of task IDs (strings) that this task depends on to execute (empty list if no dependencies).
   - You should also include, when relevant:
     - `agent`: The agent to execute the task (string, must be one of the agents implied by the catalog or available_agents list).
     - `params`: The parameters needed for the agent to execute the task, formatted according to that agent's input requirements.

5. Best practices:
   - When relative date ranges are needed (for example three days before each CPI event), express these as structured parameters, not informal prose.
   - If no explicit channels are specified for `message_puller_agent`, you may leave the channels list empty to indicate a broad search.
   - Summaries or derived analytics (for example surprise in standard deviations, yield moves) should be implied as downstream analysis of earlier data-collection tasks, not folded into raw data retrieval tasks.
   - Respect dependencies explicitly: later tasks should reference the outputs or IDs of earlier tasks through their `dependencies` field.

6. Domain-specific mappings:
   - "NFP" means Nonfarm Payroll releases.
   - "CPI" means Consumer Price Index releases.
   - "FOMC" means Federal Open Market Committee meetings.
   - Treasury instruments: TY (10-year treasury futures), 2Y and 10Y Treasury Notes.
   - "Surprise" usually means the deviation of the actual macroeconomic release from consensus expectations, sometimes expressed in standard deviations.
   - "Yield moves" refer to price or yield changes in treasury securities around event dates.

You must always ensure that the final plan can be expressed as a JSON array of task objects following the schema above, suitable for execution by the available agents."""
        
        try:

            result = self._call_ai_decompose(planning_prompt, num_subtasks, available_agents)
            
            logger.info(f"Task plan generated with {len(result.get('subtasks', []))} subtasks")
            return result
            
        except Exception as e:
            logger.error(f"AI planning failed: {e}")
            # Fallback: simple decomposition
            return self._fallback_plan(query, available_agents)
    
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

{agent_context}

Return ONLY a JSON object with this structure:
{{
    "query": "original query",
    "subtasks": [
        {{
            "id": 1,
            "description": "clear task description",
            "agent": "suggested_agent_name or null",
            "dependencies": []
        }}
    ],
    "execution_order": [
        [1, 2],
        [3]
    ]
}}

The execution_order is an array of rows.
Each row is an ordered sequence of task IDs that must run sequentially from left to right.
Tasks in different rows can run in parallel.
If a task depends on the output of another task, put BOTH tasks in the SAME row,
with the dependency appearing earlier in that row.
Independent tasks that can be executed in parallel should be placed in different rows.
Limit to {num_subtasks} or fewer subtasks."""
        
        try:
            if self.client_type == 'openai':
                response = client.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": full_prompt}],
                    temperature=0.2,
                    max_tokens=1500
                )
                content = response.choices[0].message.content.strip()
            else:  # anthropic
                response = client.messages.create(
                    model="claude-3-sonnet-20240229",
                    max_tokens=1500,
                    temperature=0.2,
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
            logger.error(f"AI decomposition failed: {e}")
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

