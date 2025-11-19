"""Result Consolidation from Worker Agents."""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class ResultConsolidator:
    """
    Consolidates results from multiple worker agents into a unified answer.
    """
    
    def __init__(self):
        """Initialize consolidator."""
        logger.info("ResultConsolidator initialized")
    
    def consolidate(
        self,
        query: str,
        task_results: List[Dict[str, Any]],
        task_plan: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Consolidate worker results into unified answer.
        
        Args:
            query: Original query
            task_results: List of results from worker agents
            task_plan: Original task plan
            
        Returns:
            Consolidated result with answer and metadata
        """
        logger.info(f"Consolidating {len(task_results)} task results")
        
        # Separate successful and failed tasks
        successful = [r for r in task_results if r.get('status') == 'success']
        failed = [r for r in task_results if r.get('status') == 'failed']
        
        logger.info(f"Successful tasks: {len(successful)}, Failed tasks: {len(failed)}")
        
        # Extract data from successful tasks
        consolidated_data = self._merge_task_data(successful)
        
        # Generate natural language answer
        answer = self._generate_answer(query, successful, consolidated_data)
        
        # Build metadata
        metadata = {
            "query": query,
            "total_tasks": len(task_results),
            "successful_tasks": len(successful),
            "failed_tasks": len(failed),
            "agents_used": list(set(r.get('agent') for r in successful)),
            "task_plan": task_plan
        }
        
        # Include failed task details if any
        if failed:
            metadata["failures"] = [
                {
                    "task_id": r.get('task_id'),
                    "agent": r.get('agent'),
                    "error": r.get('error')
                }
                for r in failed
            ]
        
        result = {
            "answer": answer,
            "data": consolidated_data,
            "metadata": metadata,
            "worker_outputs": successful
        }
        
        logger.info("Consolidation complete")
        return result
    
    def _merge_task_data(self, successful_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Merge data from all successful tasks.
        
        Args:
            successful_results: List of successful task results
            
        Returns:
            Merged data dictionary
        """
        merged = {
            "by_agent": {},
            "summary": {}
        }
        
        for result in successful_results:
            agent = result.get('agent')
            data = result.get('data', {})
            
            # Store by agent
            if agent not in merged["by_agent"]:
                merged["by_agent"][agent] = []
            merged["by_agent"][agent].append(data)
            
            # Extract key metrics based on agent type
            if agent == "market_data_agent":
                self._extract_market_data_summary(data, merged["summary"])
            elif agent == "polymarket_agent":
                # Unified polymarket agent provides both market and reasoning-style outputs
                self._extract_polymarket_summary(data, merged["summary"])
                self._extract_reasoning_summary(data, merged["summary"])
        
        return merged
    
    def _extract_market_data_summary(self, data: Dict[str, Any], summary: Dict[str, Any]) -> None:
        """Extract summary from market data results."""
        if "market_data" not in summary:
            summary["market_data"] = {
                "total_records": 0,
                "symbols": set()
            }
        
        # Extract from data
        records = data.get('data', [])
        summary["market_data"]["total_records"] += len(records)
        
        for record in records:
            if 'symbol' in record:
                summary["market_data"]["symbols"].add(record['symbol'])
        
        # Convert set to list for JSON serialization
        summary["market_data"]["symbols"] = list(summary["market_data"]["symbols"])
    
    def _extract_polymarket_summary(self, data: Dict[str, Any], summary: Dict[str, Any]) -> None:
        """Extract summary from polymarket results."""
        if "polymarket" not in summary:
            summary["polymarket"] = {
                "total_markets": 0,
                "avg_probability": None,
                "total_volume": 0
            }
        
        # Extract from data
        data_list = data.get('data', [])
        for item in data_list:
            markets = item.get('markets', [])
            summary["polymarket"]["total_markets"] += len(markets)
            
            # Calculate averages
            if markets:
                probabilities = []
                volumes = []
                for market in markets:
                    if 'prices' in market and 'Yes' in market['prices']:
                        probabilities.append(market['prices']['Yes'])
                    if 'volume' in market:
                        volumes.append(market['volume'])
                
                if probabilities:
                    summary["polymarket"]["avg_probability"] = sum(probabilities) / len(probabilities)
                if volumes:
                    summary["polymarket"]["total_volume"] += sum(volumes)
    
    def _extract_reasoning_summary(self, data: Dict[str, Any], summary: Dict[str, Any]) -> None:
        """Extract summary from reasoning agent results."""
        if "reasoning" not in summary:
            summary["reasoning"] = {
                "analyses": 0,
                "topics": set(),
                "dates_analyzed": set()
            }
        
        # Extract from data
        data_list = data.get('data', [])
        for item in data_list:
            summary["reasoning"]["analyses"] += 1
            
            parsed = item.get('parsed', {})
            if 'topic' in parsed:
                summary["reasoning"]["topics"].add(parsed['topic'])
            
            if 'comparison_date' in item:
                summary["reasoning"]["dates_analyzed"].add(item['comparison_date'])
        
        # Convert sets to lists
        summary["reasoning"]["topics"] = list(summary["reasoning"]["topics"])
        summary["reasoning"]["dates_analyzed"] = list(summary["reasoning"]["dates_analyzed"])
    
    def _generate_answer(
        self,
        query: str,
        successful_results: List[Dict[str, Any]],
        consolidated_data: Dict[str, Any]
    ) -> str:
        """
        Generate natural language answer from consolidated data.
        
        Args:
            query: Original query
            successful_results: Successful task results
            consolidated_data: Consolidated data
            
        Returns:
            Natural language answer
        """
        if not successful_results:
            return "No results were obtained. All tasks failed."
        
        answer_parts = []
        answer_parts.append(f"Query: {query}\n")
        answer_parts.append(f"Results from {len(successful_results)} worker agent(s):\n")
        
        # Summarize by agent
        by_agent = consolidated_data.get("by_agent", {})
        summary = consolidated_data.get("summary", {})
        
        for agent, results in by_agent.items():
            answer_parts.append(f"\n{agent}:")
            
            if agent == "market_data_agent":
                count = summary.get("market_data", {}).get("total_records", 0)
                symbols = summary.get("market_data", {}).get("symbols", [])
                answer_parts.append(f"  - Found {count} market data records")
                if symbols:
                    answer_parts.append(f"  - Symbols: {', '.join(symbols[:5])}")
                    if len(symbols) > 5:
                        answer_parts.append(f"    (and {len(symbols) - 5} more)")
            
            elif agent == "polymarket_agent":
                count = summary.get("polymarket", {}).get("total_markets", 0)
                avg_prob = summary.get("polymarket", {}).get("avg_probability")
                volume = summary.get("polymarket", {}).get("total_volume", 0)
                
                answer_parts.append(f"  - Found {count} prediction markets")
                if avg_prob is not None:
                    answer_parts.append(f"  - Average probability: {avg_prob*100:.1f}%")
                if volume > 0:
                    answer_parts.append(f"  - Total volume: ${volume:,.0f}")
            
            # Reasoning-style details (now produced by polymarket_agent)
            if agent == "polymarket_agent" and "reasoning" in summary:
                count = summary["reasoning"].get("analyses", 0)
                topics = summary["reasoning"].get("topics", [])
                dates = summary["reasoning"].get("dates_analyzed", [])

                if count:
                    answer_parts.append(f"  - Performed {count} analysis/analyses")
                if topics:
                    answer_parts.append(f"  - Topics: {', '.join(topics)}")
                if dates:
                    answer_parts.append(f"  - Dates analyzed: {', '.join(dates)}")
        
        # Add note about accessing detailed data
        answer_parts.append("\n---")
        answer_parts.append("Detailed results are available in the worker_outputs section.")
        
        return '\n'.join(answer_parts)

