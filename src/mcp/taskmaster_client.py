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
        
        planning_prompt = f"""Decompose this query into {num_subtasks} or fewer subtasks that can be delegated to worker agents.

Query: {query}
{agent_info}

IMPORTANT GUIDELINES:
1. market_data_agent can handle complex SQL operations in ONE task:
   - Filtering by symbol, date, price ranges (e.g., "between X and Y")
   - Sorting by any column (ascending/descending)
   - Limiting results (e.g., "most recent", "top N")
   - Example: "most recent date when ZN price was between 112.5 and 112.9" → 1 task, NOT 4
   
2. Prefer FEWER tasks over MORE tasks when:
   - All data comes from the same source (database or API)
   - Operations are SQL-expressible (filter + sort + limit)
   - No cross-agent data dependency exists
   
3. Use MULTIPLE tasks only when:
   - Data sources are truly different (e.g., SQL database + prediction markets)
   - Analysis requires reasoning/comparison between different datasets
   - Tasks can run in parallel with independent results

For each subtask, provide:
1. A clear description
2. Which agent should handle it (if known)
3. Any dependencies on other subtasks

Return a structured task plan with MINIMAL task count."""
        
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

