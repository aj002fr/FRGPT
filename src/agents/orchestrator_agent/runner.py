"""Runner - Final Result Consolidation with AI Validation."""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from .workers_db import WorkersDB
from .validator import AnswerValidator

logger = logging.getLogger(__name__)


class Runner:
    """
    Final consolidation of multi-agent results.
    
    Responsibilities:
    - Query DB for all task outputs by run_id
    - Merge data from all workers
    - Generate natural language answer
    - Use AI validation
    """
    
    def __init__(self, db_path: Path):
        """
        Initialize runner.
        
        Args:
            db_path: Path to workers database
        """
        self.db_path = Path(db_path)
        self.db = WorkersDB(self.db_path)
        self.validator = AnswerValidator()
        
        logger.info("Runner initialized")
    
    def consolidate(
        self,
        run_id: str,
        query: str,
        skip_validation: bool = False
    ) -> Dict[str, Any]:
        """
        Consolidate all results for a run.
        
        Args:
            run_id: Orchestration run ID
            query: Original user query
            skip_validation: Skip AI validation
            
        Returns:
            Consolidated result dictionary
        """
        logger.info(f"Consolidating results for run {run_id}")
        
        # Step 1: Get all task outputs from DB
        task_outputs = self.db.get_all_task_outputs(run_id)
        
        logger.info(f"Retrieved {len(task_outputs)} task outputs from DB")
        
        # Step 2: Get run summary
        run_summary = self.db.get_run_summary(run_id)
        
        # Step 3: Separate successful and failed tasks
        successful_tasks = [
            output for output in task_outputs
            if self.db.get_task_status(run_id, output['task_id']) == 'success'
        ]
        
        failed_tasks = self.db.get_failed_tasks(run_id)
        
        logger.info(f"Successful: {len(successful_tasks)}, Failed: {len(failed_tasks)}")
        
        # Step 4: Merge data by agent type
        merged_data = self._merge_task_data(successful_tasks)
        
        # Step 5: Generate natural language answer
        answer = self._generate_answer(query, merged_data, successful_tasks, failed_tasks)
        
        # Step 6: AI Validation (if enabled)
        validation_result = None
        if not skip_validation and successful_tasks:
            logger.info("Running AI validation")
            validation_result = self._validate_answer(
                query,
                answer,
                successful_tasks,
                merged_data
            )
        
        # Step 7: Build consolidated result
        consolidated = {
            'query': query,
            'answer': answer,
            'data': merged_data,
            'validation': validation_result,
            'metadata': {
                'run_id': run_id,
                'total_tasks': run_summary['total_tasks'],
                'successful_tasks': run_summary['successful'],
                'failed_tasks': run_summary['failed'],
                'total_duration_ms': run_summary.get('total_duration_ms', 0),
                'avg_duration_ms': run_summary.get('avg_duration_ms', 0),
                'agents_used': list(set(t['agent_name'] for t in task_outputs)),
                'validation_passed': validation_result.get('valid') if validation_result else None
            },
            'worker_outputs': task_outputs,
            'failed_tasks': failed_tasks
        }
        
        logger.info(f"Consolidation complete for run {run_id}")
        
        return consolidated
    
    def _merge_task_data(
        self,
        task_outputs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Merge data from all successful tasks.
        
        Args:
            task_outputs: List of task output dictionaries
            
        Returns:
            Merged data dictionary
        """
        merged = {
            "market_data": [],
            "polymarket_markets": [],
            "reasoning_insights": [],
            "summary_statistics": {},
        }
        
        for output in task_outputs:
            agent_name = output['agent_name']
            output_data = output['output_data']
            
            if agent_name == "market_data_agent":
                merged["market_data"].extend(output_data)

            elif agent_name == "polymarket_agent":
                # Extract markets and reasoning insights from unified polymarket output
                for item in output_data:
                    # Reasoning-enabled agent wraps full data under "result"
                    result = item.get("result", item)
                    markets = result.get("markets", [])
                    if markets:
                        merged["polymarket_markets"].extend(markets)
                    merged["reasoning_insights"].append(result)
        
        # Calculate summary statistics
        merged['summary_statistics'] = self._calculate_summary_stats(merged)
        
        return merged
    
    def _calculate_summary_stats(
        self,
        merged_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Calculate summary statistics from merged data."""
        stats = {
            "total_market_data_records": len(merged_data.get("market_data", [])),
            "total_polymarket_markets": len(merged_data.get("polymarket_markets", [])),
            "total_reasoning_insights": len(merged_data.get("reasoning_insights", [])),
        }
        
        # Market data statistics
        market_data = merged_data.get('market_data', [])
        if market_data:
            prices = [r.get('price') for r in market_data if r.get('price') is not None]
            if prices:
                stats['market_data'] = {
                    'min_price': min(prices),
                    'max_price': max(prices),
                    'avg_price': sum(prices) / len(prices),
                    'num_records': len(market_data)
                }
        
        # Polymarket statistics
        markets = merged_data.get('polymarket_markets', [])
        if markets:
            volumes = [m.get('volume', 0) for m in markets]
            stats['polymarket'] = {
                'total_markets': len(markets),
                'total_volume': sum(volumes),
                'avg_volume': sum(volumes) / len(volumes) if volumes else 0
            }
        
        return stats
    
    def _generate_answer(
        self,
        query: str,
        merged_data: Dict[str, Any],
        successful_tasks: List[Dict[str, Any]],
        failed_tasks: List[Dict[str, Any]]
    ) -> str:
        """
        Generate natural language answer.
        
        Args:
            query: Original query
            merged_data: Merged data from all tasks
            successful_tasks: Successful task outputs
            failed_tasks: Failed task records
            
        Returns:
            Natural language answer
        """
        answer_parts = []
        
        # Header
        answer_parts.append(f"Query: {query}\n")
        
        # Execution summary
        total_tasks = len(successful_tasks) + len(failed_tasks)
        answer_parts.append(f"Executed {total_tasks} tasks: "
                          f"{len(successful_tasks)} successful, {len(failed_tasks)} failed.\n")
        
        # Data summary
        stats = merged_data.get('summary_statistics', {})
        
        if stats.get('total_market_data_records', 0) > 0:
            answer_parts.append(f"\nMarket Data:")
            answer_parts.append(f"  - Retrieved {stats['total_market_data_records']} records")
            
            if 'market_data' in stats:
                md_stats = stats['market_data']
                answer_parts.append(f"  - Price range: ${md_stats['min_price']:.2f} - ${md_stats['max_price']:.2f}")
                answer_parts.append(f"  - Average price: ${md_stats['avg_price']:.2f}")
        
        if stats.get('total_polymarket_markets', 0) > 0:
            answer_parts.append(f"\nPolymarket Predictions:")
            answer_parts.append(f"  - Found {stats['total_polymarket_markets']} markets")
            
            if 'polymarket' in stats:
                pm_stats = stats['polymarket']
                answer_parts.append(f"  - Total volume: ${pm_stats['total_volume']:,.0f}")
                answer_parts.append(f"  - Average volume: ${pm_stats['avg_volume']:,.0f}")
        
        if stats.get('total_reasoning_insights', 0) > 0:
            answer_parts.append(f"\nReasoning Insights:")
            answer_parts.append(f"  - Generated {stats['total_reasoning_insights']} insights")
        
        # Failed tasks warning
        if failed_tasks:
            answer_parts.append(f"\n⚠ Warning: {len(failed_tasks)} tasks failed:")
            for failed in failed_tasks[:3]:  # Show first 3
                answer_parts.append(f"  - {failed['task_id']}: {failed['error'][:100]}")
        
        # Agents used
        agents = list(set(t['agent_name'] for t in successful_tasks))
        if agents:
            answer_parts.append(f"\nAgents used: {', '.join(agents)}")
        
        return '\n'.join(answer_parts)
    
    def _validate_answer(
        self,
        query: str,
        answer: str,
        successful_tasks: List[Dict[str, Any]],
        merged_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate answer using AI validator.
        
        Args:
            query: Original query
            answer: Generated answer
            successful_tasks: Successful task outputs
            merged_data: Merged data
            
        Returns:
            Validation result dictionary
        """
        # Build consolidated result for validator
        consolidated_result = {
            'answer': answer,
            'data': merged_data,
            'metadata': {
                'total_tasks': len(successful_tasks),
                'successful_tasks': len(successful_tasks)
            },
            'worker_outputs': successful_tasks
        }
        
        # Build task plan summary for validator
        task_plan = {
            'subtasks': [
                {
                    'id': task['task_id'],
                    'description': f"{task['agent_name']} task",
                    'agent': task['agent_name']
                }
                for task in successful_tasks
            ]
        }
        
        # Run validation
        validation_result = self.validator.validate(
            query=query,
            answer=answer,
            task_plan=task_plan,
            consolidated_result=consolidated_result
        )
        
        return validation_result
    
    def get_task_output(
        self,
        run_id: str,
        task_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get output for a specific task.
        
        Args:
            run_id: Run ID
            task_id: Task ID
            
        Returns:
            Task output or None
        """
        return self.db.get_task_output(run_id, task_id)
    
    def get_run_summary(
        self,
        run_id: str
    ) -> Dict[str, Any]:
        """
        Get summary for a run.
        
        Args:
            run_id: Run ID
            
        Returns:
            Run summary
        """
        return self.db.get_run_summary(run_id)
    
    def close(self):
        """Close database connection."""
        if self.db:
            self.db.close()
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

