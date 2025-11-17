"""Test Two-Stage Planner with Single Agent."""

import logging
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.core.logging_config import setup_logging
from src.agents.orchestrator_agent.run import OrchestratorAgent

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


# Test Queries for ZN Treasury Futures
TEST_QUERIES = [
    {
        "name": "Basic ZN Query",
        "query": "Get 10 ZN treasury futures data from the database",
        "description": "Simple query for ZN futures"
    },
    {
        "name": "ZN Call Options",
        "query": "Show me ZN call options with prices above 1.5",
        "description": "Filter ZN call options by price"
    },
    {
        "name": "Recent ZN Data",
        "query": "Get the most recent 10 ZN treasury futures entries",
        "description": "Latest ZN data with limit"
    },
    {
        "name": "ZN Price Range",
        "query": "Find ZN treasury futures with prices between 0.1 and 1.0",
        "description": "ZN futures in specific price range"
    },
    {
        "name": "ZN August 2025",
        "query": "Get ZN treasury futures for August 2025 contracts",
        "description": "ZN futures for specific contract month"
    }
]


def run_single_query(query_info: dict, query_num: int) -> bool:
    """Run a single test query."""
    print("\n" + "="*70)
    print(f"TEST {query_num}: {query_info['name']}")
    print("="*70)
    print(f"Description: {query_info['description']}")
    
    agent = OrchestratorAgent()
    
    query = query_info['query']
    
    print(f"\nQuery: {query}")
    print("\nExecuting...\n")
    
    try:
        result = agent.run(
            query=query,
            num_subtasks=1,  # Force single task
            skip_validation=True  # Skip validation for faster testing
        )
        
        print("\n" + "-"*70)
        print("RESULT")
        print("-"*70)
        print(f"\nAnswer:\n{result['answer']}")
        
        metadata = result.get('metadata', {})
        print(f"\nMetadata:")
        print(f"  - Run ID: {metadata.get('run_id')}")
        print(f"  - Duration: {metadata.get('duration_ms', 0):.2f}ms")
        print(f"  - Total Tasks: {metadata.get('total_tasks', 0)}")
        print(f"  - Successful: {metadata.get('successful_tasks', 0)}")
        print(f"  - Failed: {metadata.get('failed_tasks', 0)}")
        print(f"  - Agents Used: {metadata.get('agents_used', [])}")
        print(f"  - Paths: {metadata.get('num_paths', 0)}")
        
        # Show data summary
        data = result.get('data', {})
        if data.get('summary_statistics'):
            stats = data['summary_statistics']
            print(f"\nData Summary:")
            if 'total_market_data_records' in stats:
                print(f"  - Records: {stats['total_market_data_records']}")
            if 'market_data' in stats:
                md_stats = stats['market_data']
                print(f"  - Price Range: ${md_stats.get('min_price', 0):.4f} - ${md_stats.get('max_price', 0):.4f}")
                print(f"  - Avg Price: ${md_stats.get('avg_price', 0):.4f}")
        
        if result.get('output_path'):
            print(f"\nOutput: {result['output_path']}")
        
        print("\n✅ Test PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Test FAILED: {e}")
        logger.exception("Test failed")
        return False


if __name__ == "__main__":
    print("\n" + "="*70)
    print("TWO-STAGE PLANNER - ZN TREASURY FUTURES TESTS")
    print("="*70)
    print("\nTesting with single agent (market_data_agent) on ZN data")
    
    results = []
    
    # Run all test queries
    for i, query_info in enumerate(TEST_QUERIES, 1):
        success = run_single_query(query_info, i)
        results.append((query_info['name'], success))
    
    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for name, success in results:
        status = '✅ PASSED' if success else '❌ FAILED'
        print(f"{name}: {status}")
    
    print("-"*70)
    print(f"Total: {passed}/{total} tests passed")
    print("="*70 + "\n")
    
    sys.exit(0 if passed == total else 1)

