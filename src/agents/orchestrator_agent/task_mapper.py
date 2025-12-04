"""Task-to-Agent Mapping System."""

import logging
from typing import Dict, Any, List, Optional, Tuple

from .config import AGENT_CAPABILITIES

logger = logging.getLogger(__name__)


class TaskMapper:
    """
    Maps task descriptions to appropriate worker agents.
    """
    
    def __init__(self):
        """Initialize task mapper with agent registry."""
        self.capabilities = AGENT_CAPABILITIES
        logger.info(f"TaskMapper initialized with {len(self.capabilities)} agents")
    
    def map_task(self, task: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Map a single task to an agent.
        
        Strategy:
        1. Validate LLM's suggested agent
        2. Use LLM's params if present (trust the planner)
        3. Only extract params as fallback if LLM didn't provide them
        
        Args:
            task: Task dictionary with 'description', optional 'agent', and optional 'params'
            
        Returns:
            Tuple of (agent_name, params_dict) or (None, {}) if unmappable
        """
        task_desc = task.get('description', '').lower()
        
        # Get LLM's suggested agent and params
        raw_agent = task.get('agent')
        llm_params = task.get('params', {})
        
        if isinstance(raw_agent, str):
            suggested_agent = raw_agent.lower().replace('-', '_')
        else:
            suggested_agent = ''
        
        # If agent is suggested and valid, use it
        if suggested_agent in self.capabilities:
            logger.info(f"Using suggested agent '{suggested_agent}' for task: {task_desc[:50]}")
            
            # ✅ Use LLM params if present, otherwise extract from description
            if llm_params:
                logger.debug(f"Using LLM-provided params: {llm_params}")
                return suggested_agent, llm_params
            else:
                logger.debug("LLM params empty, extracting from description")
                params = self._extract_params(task_desc, suggested_agent)
                return suggested_agent, params
        
        # Otherwise, find best match by keywords
        best_agent = None
        best_score = 0
        
        for agent_name, config in self.capabilities.items():
            score = self._calculate_match_score(task_desc, config['keywords'])
            if score > best_score:
                best_score = score
                best_agent = agent_name
        
        if best_agent and best_score > 0:
            logger.info(f"Mapped task to '{best_agent}' (score: {best_score}): {task_desc[:50]}")
            
            # ✅ Prefer LLM params even for fallback agent
            if llm_params:
                logger.debug("Using LLM params with fallback agent")
                return best_agent, llm_params
            else:
                params = self._extract_params(task_desc, best_agent)
                return best_agent, params
        
        # No match found
        logger.warning(f"Could not map task to any agent: {task_desc[:50]}")
        return None, {}
    
    def map_all_tasks(self, subtasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Map all subtasks to agents.
        
        Args:
            subtasks: List of task dictionaries
            
        Returns:
            List of mapped tasks with agent and params
        """
        mapped_tasks = []
        unmapped_count = 0
        
        for task in subtasks:
            agent_name, params = self.map_task(task)
            
            mapped_task = {
                **task,
                'mapped_agent': agent_name,
                'agent_params': params,
                'mappable': agent_name is not None
            }
            
            if agent_name is None:
                unmapped_count += 1
                mapped_task['unmapped_reason'] = "No suitable agent found"
            
            mapped_tasks.append(mapped_task)
        
        if unmapped_count > 0:
            logger.warning(f"{unmapped_count} of {len(subtasks)} tasks could not be mapped")
        else:
            logger.info(f"Successfully mapped all {len(subtasks)} tasks")
        
        return mapped_tasks
    
    def _calculate_match_score(self, task_desc: str, keywords: List[str]) -> int:
        """
        Calculate how well task description matches agent keywords.
        
        Args:
            task_desc: Task description (lowercase)
            keywords: Agent keywords
            
        Returns:
            Match score (number of matching keywords)
        """
        score = 0
        for keyword in keywords:
            if keyword.lower() in task_desc:
                score += 1
        return score
    
    def _extract_params(self, task_desc: str, agent_name: str) -> Dict[str, Any]:
        """
        Extract parameters for agent from task description.
        
        Args:
            task_desc: Task description
            agent_name: Name of mapped agent
            
        Returns:
            Parameters dictionary
        """
        params = {}
        
        if agent_name == "market_data_agent":
            params = self._extract_market_data_params(task_desc)
        elif agent_name == "polymarket_agent":
            params = self._extract_polymarket_params(task_desc)
        elif agent_name == "runner_agent":
            params = self._extract_runner_params(task_desc)
        elif agent_name == "analytics_agent":
            params = self._extract_analytics_params(task_desc)
        elif agent_name == "eventdata_puller_agent":
            params = self._extract_eventdata_params(task_desc)
        
        return params
    
    def _extract_market_data_params(self, task_desc: str) -> Dict[str, Any]:
        """Extract parameters for market_data_agent with advanced SQL feature support."""
        import re
        
        task_lower = task_desc.lower()
        
        params = {
            "template": "by_symbol",  # Default to symbol search
            "params": {"symbol_pattern": "%"},  # Default: all symbols
            "columns": None,
            "limit": 1000,  # Default limit
            "order_by_column": None,
            "order_by_direction": "ASC"
        }
        
        # Extract symbol patterns
        if any(kw in task_lower for kw in ['btc', 'bitcoin']):
            params["params"]["symbol_pattern"] = "%BTC%"
        elif any(kw in task_lower for kw in ['eth', 'ethereum']):
            params["params"]["symbol_pattern"] = "%ETH%"
        elif ' zn ' in task_lower or task_lower.startswith('zn ') or task_lower.endswith(' zn') or 'of zn' in task_lower:
            params["params"]["symbol_pattern"] = "%ZN%"
        elif 'symbol' in task_lower:
            # Look for explicit symbol patterns
            symbol_match = re.search(r'\b([A-Z]{2,5})\b', task_desc.upper())
            if symbol_match:
                params["params"]["symbol_pattern"] = f"%{symbol_match.group(1)}%"
        
        # Extract date
        date_pattern = r'\d{4}-\d{2}-\d{2}'
        date_match = re.search(date_pattern, task_desc)
        if date_match:
            if params["template"] == "by_symbol":
                params["template"] = "by_symbol_and_date"
            else:
                params["template"] = "by_date"
            params["params"]["file_date"] = date_match.group(0)
        
        # Detect price range filters (use custom template for complex WHERE)
        price_range_detected = False
        
        # Pattern: "between X and Y" or "from X to Y"
        between_match = re.search(r'between\s+(\d+\.?\d*)\s+and\s+(\d+\.?\d*)', task_lower)
        from_to_match = re.search(r'from\s+(\d+\.?\d*)\s+to\s+(\d+\.?\d*)', task_lower)
        
        if between_match or from_to_match:
            price_range_detected = True
            match = between_match or from_to_match
            min_price = float(match.group(1))
            max_price = float(match.group(2))
            
            # Use custom template with BETWEEN condition
            params["template"] = "custom"
            
            # Build WHERE clause
            conditions = []
            values = []
            
            # Add symbol condition if present
            if params["params"]["symbol_pattern"] != "%":
                conditions.append("symbol LIKE ?")
                values.append(params["params"]["symbol_pattern"])
            
            # Add price range
            conditions.append("price BETWEEN ? AND ?")
            values.extend([min_price, max_price])
            
            # Add is_valid
            conditions.append("is_valid = 1")
            
            params["params"] = {
                "conditions": " AND ".join(conditions),
                "values": values
            }
        
        # Pattern: "price > X", "price < X", "price >= X", "price <= X"
        price_compare_match = re.search(r'price\s*([><=]+)\s*(\d+\.?\d*)', task_lower)
        if price_compare_match and not price_range_detected:
            price_range_detected = True
            operator = price_compare_match.group(1)
            value = float(price_compare_match.group(2))
            
            params["template"] = "custom"
            conditions = []
            values = []
            
            if params["params"]["symbol_pattern"] != "%":
                conditions.append("symbol LIKE ?")
                values.append(params["params"]["symbol_pattern"])
            
            conditions.append(f"price {operator} ?")
            values.append(value)
            conditions.append("is_valid = 1")
            
            params["params"] = {
                "conditions": " AND ".join(conditions),
                "values": values
            }
        
        # Extract ORDER BY requirements
        order_column = None
        order_direction = "ASC"
        
        # Check for descending order keywords
        if any(kw in task_lower for kw in ['descending', 'desc', 'latest', 'most recent', 'newest']):
            order_direction = "DESC"
        elif any(kw in task_lower for kw in ['ascending', 'asc', 'oldest', 'earliest']):
            order_direction = "ASC"
        
        # Determine what to sort by
        if any(kw in task_lower for kw in ['date', 'when', 'recent', 'latest', 'earliest']):
            order_column = "file_date"
        elif any(kw in task_lower for kw in ['price', 'highest', 'lowest', 'expensive', 'cheap']):
            order_column = "price"
            if any(kw in task_lower for kw in ['highest', 'expensive']):
                order_direction = "DESC"
            elif any(kw in task_lower for kw in ['lowest', 'cheap']):
                order_direction = "ASC"
        elif 'sort' in task_lower or 'order' in task_lower:
            # Default to date if sorting requested but column unclear
            order_column = "file_date"
        
        if order_column:
            params["order_by_column"] = order_column
            params["order_by_direction"] = order_direction
        
        # Extract LIMIT if "most recent X" or "top X" or "first X"
        limit_match = re.search(r'(?:most recent|latest|first|top)\s+(\d+)', task_lower)
        if limit_match:
            params["limit"] = int(limit_match.group(1))
        elif any(kw in task_lower for kw in ['most recent', 'latest', 'first']) and not limit_match:
            # If asking for "most recent" without number, assume they want 1
            params["limit"] = 1
        
        return params
    
    def _extract_eventdata_params(self, task_desc: str) -> Dict[str, Any]:
        """Extract parameters for eventdata_puller_agent."""
        import re
        
        desc_lower = task_desc.lower()
        
        params = {
            "event_name": None,
            "country": None,
            "lookback_days": None,
            "window_hours": 12.0,
            "importance": None,
            "update_calendar": False
        }
        
        # Extract country
        country_match = re.search(r'\b(us|united states|uk|united kingdom|gb|eu|euro|japan|jp|china|cn|germany|de|canada|ca|australia|au)\b', desc_lower)
        if country_match:
            country_map = {
                "us": "US", "united states": "US",
                "uk": "GB", "united kingdom": "GB", "gb": "GB",
                "eu": "EU", "euro": "EU",
                "japan": "JP", "jp": "JP",
                "china": "CN", "cn": "CN",
                "germany": "DE", "de": "DE",
                "canada": "CA", "ca": "CA",
                "australia": "AU", "au": "AU"
            }
            params["country"] = country_map.get(country_match.group(1), country_match.group(1).upper())
        
        # Extract lookback days
        lookback_match = re.search(r'last (\d+) (?:days|years|months)', desc_lower)
        if lookback_match:
            val = int(lookback_match.group(1))
            unit = lookback_match.group(0)
            if 'year' in unit:
                params["lookback_days"] = val * 365
            elif 'month' in unit:
                params["lookback_days"] = val * 30
            else:
                params["lookback_days"] = val
        elif 'recent' in desc_lower or 'latest' in desc_lower:
             # Default to reasonable lookback for "recent"
             params["lookback_days"] = 90
        
        # Extract window hours (for correlations)
        window_match = re.search(r'within (\d+) (?:hour|hr)s?', desc_lower)
        if window_match:
            params["window_hours"] = float(window_match.group(1))
        
        # Extract importance
        if 'high importance' in desc_lower or 'major' in desc_lower:
            params["importance"] = 'high'
        elif 'medium importance' in desc_lower:
            params["importance"] = 'medium'
            
        # Extract event name (heuristic: look for common event names)
        # This is tricky as event names can be anything. We'll look for known keywords.
        event_keywords = [
            "nonfarm", "non-farm", "nfp", "payrolls",
            "cpi", "inflation", "ppi",
            "gdp", "growth",
            "fomc", "rate decision", "fed funds",
            "retail sales",
            "unemployment", "jobless claims",
            "ism", "pmi",
            "consumer confidence",
            "durable goods",
            "housing starts",
            "industrial production"
        ]
        
        for kw in event_keywords:
            if kw in desc_lower:
                # If found, try to extract the full phrase or just use the keyword
                params["event_name"] = kw
                break
        
        # If no known keyword but "event" is mentioned, maybe extract nearby words?
        # For now, let Planner 2 AI refine it if missing.
        
        # Check for update request
        if 'update' in desc_lower and 'calendar' in desc_lower:
            params["update_calendar"] = True
            
        return params

    def _extract_polymarket_params(self, task_desc: str) -> Dict[str, Any]:
        """Extract parameters for polymarket_agent (direct + reasoning modes)."""
        params = {
            "query": task_desc,
            "session_id": None,  # Will be auto-generated by agent
            "limit": 10,
        }

        # Try to extract limit
        import re

        limit_match = re.search(r'top (\d+)|first (\d+)|(\d+) market', task_desc)
        if limit_match:
            limit = int(
                limit_match.group(1) or limit_match.group(2) or limit_match.group(3)
            )
            params["limit"] = min(limit, 50)

        return params

    def _extract_runner_params(self, task_desc: str) -> Dict[str, Any]:
        """Extract high-level parameters for runner_agent.

        The runner agent is used for final consolidation and answer formatting.
        We intentionally keep its params minimal and descriptive; low-level worker
        parameters are passed separately via worker_outputs from the DB.
        """
        # Very lightweight heuristics to detect desired style/focus.
        desc_lower = task_desc.lower()
        focus: str = "general"

        if any(k in desc_lower for k in ["compare", "difference", "vs ", "versus"]):
            focus = "comparison"
        elif any(k in desc_lower for k in ["trend", "over time", "evolution"]):
            focus = "trend"
        elif any(k in desc_lower for k in ["summary", "summarize", "overview"]):
            focus = "summary"

        style: str = "analytical"
        if any(k in desc_lower for k in ["executive", "high level", "tl;dr"]):
            style = "executive"

        return {
            "focus": focus,
            "style": style,
            # The full query and worker data will be supplied by the orchestrator
            # when it calls the RunnerAgent directly.
        }
    
    def _extract_analytics_params(self, task_desc: str) -> Dict[str, Any]:
        """Extract parameters for analytics_agent.
        
        Determines analysis type and relevant parameters from task description.
        """
        import re
        
        desc_lower = task_desc.lower()
        
        # Default params
        params = {
            "analysis_type": "descriptive",
            "params": {},
            "generate_plot": True,
        }
        
        # Determine analysis type based on keywords
        if any(k in desc_lower for k in ["percentile rank", "where does", "what percentile", "rank of"]):
            params["analysis_type"] = "percentile_rank"
        elif any(k in desc_lower for k in ["surprise", "actual vs", "actual minus", "beat", "miss", "consensus"]):
            params["analysis_type"] = "surprise_analysis"
            # Try to extract event name
            event_patterns = [
                r"(?:for|of)\s+([a-zA-Z\s]+?)(?:\s+event|\s+data|\s+release|$)",
                r"(nonfarm|non-farm|gdp|cpi|ppi|unemployment|payroll|fomc|fed)",
            ]
            for pattern in event_patterns:
                match = re.search(pattern, desc_lower)
                if match:
                    params["params"]["event_name_pattern"] = match.group(1).strip()
                    break
        elif any(k in desc_lower for k in ["market on event", "prices on", "market data on", "event dates"]):
            params["analysis_type"] = "event_impact"
        elif any(k in desc_lower for k in ["compare", "comparison", "vs ", "versus", "difference between"]):
            params["analysis_type"] = "comparison"
        elif any(k in desc_lower for k in ["correlation", "correlated", "relationship between"]):
            params["analysis_type"] = "correlation"
        elif any(k in desc_lower for k in ["histogram", "distribution", "frequency"]):
            params["analysis_type"] = "descriptive"
            params["params"]["title"] = "Distribution"
        elif any(k in desc_lower for k in ["line chart", "time series", "trend"]):
            params["analysis_type"] = "descriptive"
            params["params"]["plot_type"] = "line"
        elif any(k in desc_lower for k in ["scatter", "scatter plot"]):
            params["analysis_type"] = "correlation"
        elif any(k in desc_lower for k in ["bar chart", "bar graph"]):
            params["analysis_type"] = "descriptive"
            params["params"]["plot_type"] = "bar"
        
        # Extract country if mentioned
        country_match = re.search(r'\b(us|united states|uk|eu|euro|japan|china|germany)\b', desc_lower)
        if country_match:
            country_map = {
                "us": "US", "united states": "US",
                "uk": "GB", "eu": "EU", "euro": "EU",
                "japan": "JP", "china": "CN", "germany": "DE"
            }
            params["params"]["country"] = country_map.get(country_match.group(1), country_match.group(1).upper())
        
        # Extract symbol pattern if mentioned
        symbol_match = re.search(r'\b(xcme|zn|tn|zf|zb)\b', desc_lower)
        if symbol_match:
            params["params"]["symbol_pattern"] = f"%{symbol_match.group(1).upper()}%"
        
        # Check if plot is explicitly not wanted
        if any(k in desc_lower for k in ["no plot", "without plot", "skip plot", "no chart", "no visualization"]):
            params["generate_plot"] = False
        
        return params
    
    def get_agent_info(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """Get information about an agent."""
        return self.capabilities.get(agent_name)
    
    def list_agents(self) -> List[str]:
        """List all available agents."""
        return list(self.capabilities.keys())

