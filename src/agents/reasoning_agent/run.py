"""
Reasoning Agent for parsing market queries.

Simplified approach:
- Always returns current state
- Adds historical comparison (specified date OR past week)
- Sorts by relevance then volume
- Flags low volume markets
"""

import json
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List

from src.mcp.client import MCPClient
from src.bus.manifest import Manifest
from src.bus.file_bus import write_atomic, ensure_dir
from src.bus.schema import create_output_template
from .config import (
    AGENT_NAME,
    AGENT_VERSION,
    WORKSPACE_ROOT,
    OUTPUT_DIR,
    LOGS_DIR,
    LOW_VOLUME_THRESHOLD,
    DEFAULT_LOOKBACK_DAYS,
    MAX_MARKETS_TO_RETURN
)

logger = logging.getLogger(__name__)


class ReasoningAgent:
    """
    Reasoning Agent that processes market queries with a simplified approach:
    
    1. Always show current market state
    2. Add historical comparison:
       - If date mentioned → compare with that date
       - If no date → compare with past week
    3. Sort by relevance first, then volume
    4. Flag low volume markets
    """
    
    def __init__(self):
        """Initialize the reasoning agent."""
        # Get workspace path
        project_root = Path(__file__).parent.parent.parent.parent
        self.workspace = project_root / WORKSPACE_ROOT
        
        # Initialize manifest
        self.manifest = Manifest(self.workspace)
        
        # Initialize MCP client
        self.client = MCPClient()
        
        logger.info(f"{AGENT_NAME} v{AGENT_VERSION} initialized at {self.workspace}")
    
    def parse_query_with_gpt(self, query: str) -> Dict[str, Any]:
        """
        Use GPT-4 to extract topic and optional date from query.
        
        Args:
            query: User's natural language query
            
        Returns:
            {
                "topic": "extracted market topic",
                "date": "YYYY-MM-DD or null",
                "confidence": 0.0-1.0
            }
        """
        import os
        from pathlib import Path
        
        try:
            # Load API key from config/keys.env
            config_dir = Path(__file__).parent.parent.parent.parent / "config"
            env_file = config_dir / "keys.env"
            
            api_key = os.environ.get('OPENAI_API_KEY')
            
            # Try loading from file if not in environment
            if not api_key and env_file.exists():
                with open(env_file, 'r') as f:
                    for line in f:
                        if line.startswith('OPENAI_API_KEY='):
                            api_key = line.split('=', 1)[1].strip()
                            break
            
            if not api_key:
                logger.warning("OPENAI_API_KEY not found, falling back to rule-based parsing")
                return self._fallback_parse(query)
            
            # Import OpenAI
            try:
                from openai import OpenAI
            except ImportError:
                logger.warning("OpenAI library not installed, falling back to rule-based parsing")
                return self._fallback_parse(query)
            
            client = OpenAI(api_key=api_key)
            
            # Simplified prompt - just extract topic and optional date
            today = datetime.now().strftime('%Y-%m-%d')
            prompt = f"""
You are a query parser for a prediction markets system. Extract the topic and any date mentioned.

Query: "{query}"

Extract:
1. TOPIC - The main subject (e.g., "Bitcoin price", "federal shutdown", "AI regulation")
2. DATE - Any specific past date mentioned:
   - Parse natural language: "Nov 1", "November 1st", "January 1 2025", "last week"
   - Convert to YYYY-MM-DD format
   - Return null if no specific date mentioned
   - Today is {today}

Return ONLY a JSON object:
{{
    "topic": "extracted topic",
    "date": "YYYY-MM-DD or null",
    "confidence": 0.95
}}

Examples:
1. Query: "Bitcoin price predictions"
   {{"topic": "bitcoin price", "date": null, "confidence": 0.98}}

2. Query: "What was the opinion on Jan 1 2025 about Bitcoin?"
   {{"topic": "bitcoin", "date": "2025-01-01", "confidence": 0.95}}

3. Query: "AI regulation markets"
   {{"topic": "AI regulation", "date": null, "confidence": 0.97}}

4. Query: "Federal shutdown on November 5th"
   {{"topic": "federal shutdown", "date": "2024-11-05", "confidence": 0.90}}

Now parse the query above and return ONLY the JSON.
""".strip()
            
            logger.info("Calling GPT-4 to parse query")
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=150
            )
            
            # Extract and parse JSON response
            content = response.choices[0].message.content.strip()
            
            # Remove markdown code blocks if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            
            parsed = json.loads(content)
            logger.info(f"Parsed - topic: '{parsed['topic']}', date: {parsed.get('date')}, confidence: {parsed.get('confidence', 'N/A')}")
            
            return parsed
            
        except Exception as e:
            logger.error(f"GPT-4 parsing failed: {e}. Falling back to rule-based parsing")
            return self._fallback_parse(query)
    
    def _fallback_parse(self, query: str) -> Dict[str, Any]:
        """
        Simple rule-based parsing as fallback.
        
        Args:
            query: User query
            
        Returns:
            Parsed query dict
        """
        return {
            "topic": query,
            "date": None,
            "confidence": 0.5
        }
    
    def run(self, query: str, session_id: Optional[str] = None) -> Path:
        """
        Process a market query with unified approach:
        1. Show current market state
        2. Add historical comparison (specified date OR past week)
        3. Sort by relevance then volume
        4. Flag low volume
        
        Args:
            query: Natural language query
            session_id: Optional session ID
            
        Returns:
            Path to output file
        """
        # Generate run ID and session ID
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        if not session_id:
            session_id = self._generate_session_id(query, run_id)
        
        logger.info(f"Run {run_id}: Starting with query='{query}'")
        logger.info(f"Session ID: {session_id}")
        
        try:
            # Step 1: Parse query to extract topic and optional date
            logger.info("Step 1: Parsing query with GPT-4")
            parsed = self.parse_query_with_gpt(query)
            
            # Determine comparison date
            comparison_date = parsed.get('date')
            if not comparison_date:
                # Default to 1 week ago
                comparison_date = (datetime.now() - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime('%Y-%m-%d')
                date_source = "default"
                logger.info(f"No date specified, defaulting to {DEFAULT_LOOKBACK_DAYS} days ago: {comparison_date}")
            else:
                date_source = "specified"
                logger.info(f"Using specified date: {comparison_date}")
            
            # Step 2: Search for markets
            logger.info(f"Step 2: Searching markets for topic: '{parsed['topic']}'")
            search_result = self.client.call_tool(
                "search_polymarket_markets",
                {
                    "query": parsed['topic'],
                    "session_id": session_id,
                    "limit": MAX_MARKETS_TO_RETURN
                }
            )
            
            markets = search_result.get("markets", [])
            logger.info(f"Found {len(markets)} markets")
            
            if not markets:
                result = {
                    "query": query,
                    "parsed": parsed,
                    "comparison_date": comparison_date,
                    "date_source": date_source,
                    "markets": [],
                    "error": f"No markets found about '{parsed['topic']}'"
                }
            else:
                # Step 3: Add historical comparison for each market
                logger.info(f"Step 3: Fetching historical prices for {len(markets)} markets on {comparison_date}")
                markets_with_history = self._add_historical_comparison(markets, comparison_date)
                
                # Step 4: Sort by relevance first, then volume
                logger.info("Step 4: Sorting by relevance and volume")
                sorted_markets = self._sort_markets(markets_with_history)
                
                # Step 5: Flag low volume markets
                logger.info("Step 5: Flagging low volume markets")
                flagged_markets = self._flag_low_volume(sorted_markets)
                
                result = {
                    "query": query,
                    "parsed": parsed,
                    "comparison_date": comparison_date,
                    "date_source": date_source,
                    "markets": flagged_markets,
                    "metadata": {
                        "total_markets": len(flagged_markets),
                        "low_volume_count": sum(1 for m in flagged_markets if m.get('low_volume_flag')),
                        "comparison_note": f"Comparing current vs {comparison_date} ({date_source})"
                    }
                }
            
            # Step 6: Write output to file bus
            output_path = self._write_output(result)
            
            # Step 7: Log run
            self._log_run(run_id, query, session_id, parsed, comparison_date, output_path, "success")
            
            logger.info(f"Run {run_id}: Completed successfully. Output: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Run {run_id}: Failed with error: {e}")
            self._log_run(run_id, query, session_id, {}, None, None, "error", str(e))
            raise
    
    def _add_historical_comparison(self, markets: List[Dict], comparison_date: str) -> List[Dict]:
        """
        Add historical prices to each market for comparison.
        
        Args:
            markets: List of market dicts
            comparison_date: Date to compare against (YYYY-MM-DD)
            
        Returns:
            Markets with historical price data added
        """
        from src.servers.polymarket.schema import (
            market_exists_on_date,
            get_token_id_for_price_history,
            validate_market_url
        )
        
        for market in markets:
            # Validate URL
            url = market.get('url')
            url_valid = validate_market_url(url) if url else False
            market['url_valid'] = url_valid
            
            if not url_valid:
                logger.warning(f"Market URL not accessible: {url}")
                market['url_note'] = "URL may be incorrect or market removed"
            
            # Check if market existed on comparison date
            created_at = market.get('created_at')
            existed_on_date = market_exists_on_date(created_at, comparison_date)
            
            if not existed_on_date:
                logger.info(f"Market '{market.get('title', 'N/A')[:50]}' was created after {comparison_date}")
                market['historical_price'] = {'yes': None, 'no': None}
                market['historical_date'] = comparison_date
                market['historical_note'] = f"Market created after {comparison_date}. Creation: {created_at[:10] if created_at else 'Unknown'}"
                market['price_change'] = None
                continue
            
            # Get token ID
            token_id = get_token_id_for_price_history(market)
            
            if not token_id:
                logger.warning(f"No token ID for: {market.get('title', 'N/A')[:50]}")
                market['historical_price'] = {'yes': None, 'no': None}
                market['historical_note'] = "Token ID not available"
                market['price_change'] = None
                continue
            
            # Fetch historical price
            try:
                history = self.client.call_tool(
                    "get_market_price_history",
                    {
                        "market_id": token_id,
                        "date": comparison_date
                    }
                )
                
                historical_price = history.get('price')
                market['historical_price'] = historical_price
                market['historical_date'] = comparison_date
                market['historical_note'] = history.get('note')
                
                # Calculate price change
                current_yes = market.get('prices', {}).get('Yes', 0)
                historical_yes = historical_price.get('yes') if historical_price else None
                
                if historical_yes is not None and current_yes is not None:
                    change = (current_yes - historical_yes) * 100  # Convert to percentage points
                    market['price_change'] = {
                        'yes_change': round(change, 2),
                        'direction': 'up' if change > 0 else ('down' if change < 0 else 'unchanged')
                    }
                    logger.info(f"Price change for '{market.get('title', 'N/A')[:40]}': {change:+.2f}pp")
                else:
                    market['price_change'] = None
                
            except Exception as e:
                logger.warning(f"Failed to get historical price for {token_id}: {e}")
                market['historical_price'] = {'yes': None, 'no': None}
                market['historical_note'] = f"Historical data unavailable: {str(e)}"
                market['price_change'] = None
        
        return markets
    
    def _sort_markets(self, markets: List[Dict]) -> List[Dict]:
        """
        Sort markets by relevance score first, then by volume.
        
        Args:
            markets: List of market dicts
            
        Returns:
            Sorted markets
        """
        def sort_key(market):
            # Primary: relevance score (higher is better)
            # Secondary: volume (higher is better)
            relevance = market.get('relevance_score', 0)
            volume = market.get('volume', 0)
            return (-relevance, -volume)
        
        sorted_markets = sorted(markets, key=sort_key)
        
        logger.info(f"Sorted {len(sorted_markets)} markets by relevance and volume")
        return sorted_markets
    
    def _flag_low_volume(self, markets: List[Dict]) -> List[Dict]:
        """
        Flag markets with volume below threshold.
        
        Args:
            markets: List of market dicts
            
        Returns:
            Markets with low_volume_flag added
        """
        for market in markets:
            volume = market.get('volume', 0)
            
            if volume < LOW_VOLUME_THRESHOLD:
                market['low_volume_flag'] = True
                market['volume_note'] = f"⚠️ Low volume (${volume:,.0f} < ${LOW_VOLUME_THRESHOLD:,.0f})"
                logger.info(f"Flagged low volume: '{market.get('title', 'N/A')[:40]}' (${volume:,.0f})")
            else:
                market['low_volume_flag'] = False
                market['volume_note'] = f"Volume: ${volume:,.0f}"
        
        return markets
    
    def _generate_session_id(self, query: str, run_id: str) -> str:
        """Generate deterministic session ID."""
        hash_input = f"{run_id}_{query}"
        hash_obj = hashlib.sha256(hash_input.encode())
        return f"{run_id}_{hash_obj.hexdigest()[:6]}"
    
    def _write_output(self, result: Dict) -> Path:
        """Write result to file bus."""
        # Get next output path
        output_path = self.manifest.get_next_filepath(subdir="out")
        
        # Prepare data
        data = [{
            "query": result.get("query"),
            "parsed": result.get("parsed"),
            "comparison_date": result.get("comparison_date"),
            "date_source": result.get("date_source"),
            "result": result
        }]
        
        # Create output with template
        output_data = create_output_template(
            data=data,
            query=f"Market query: {result.get('query', 'N/A')}",
            agent_name=AGENT_NAME,
            version=AGENT_VERSION
        )
        
        # Add custom metadata
        output_data["metadata"]["comparison_date"] = result.get("comparison_date")
        output_data["metadata"]["date_source"] = result.get("date_source")
        output_data["metadata"]["low_volume_threshold"] = LOW_VOLUME_THRESHOLD
        
        # Write atomically
        logger.info(f"Writing output to {output_path}")
        write_atomic(output_path, output_data)
        
        return output_path
    
    def _log_run(
        self,
        run_id: str,
        query: str,
        session_id: str,
        parsed: Dict,
        comparison_date: Optional[str],
        output_path: Optional[Path],
        status: str,
        error: Optional[str] = None
    ) -> None:
        """Log run metadata."""
        log_data = {
            "run_id": run_id,
            "query": query,
            "session_id": session_id,
            "parsed_topic": parsed.get("topic"),
            "parsed_date": parsed.get("date"),
            "comparison_date": comparison_date,
            "output_path": str(output_path) if output_path else None,
            "status": status,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": AGENT_NAME,
            "version": AGENT_VERSION
        }
        
        log_file = self.workspace / "logs" / f"{run_id}.json"
        ensure_dir(log_file.parent)
        write_atomic(log_file, log_data)
        
        logger.info(f"Run log: {log_file}")
