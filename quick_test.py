"""Quick single query test for Two-Stage Planner."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.core.logging_config import setup_logging
from src.agents.orchestrator_agent.run import OrchestratorAgent

setup_logging()
logger = logging.getLogger(__name__)


def quick_test():
    """Quick test with limited data."""
    print("\n" + "="*70)
    print("QUICK TEST - Two-Stage Planner (Limited Data)")
    print("="*70)
    
    agent = OrchestratorAgent()
    
    # Use a LIMITED query for speed
    query = "Get the 5 most recent ZN treasury futures entries"
    
    print(f"\nQuery: {query}")
    print("Executing...\n")
    
    try:
        result = agent.run(
            query=query,
            num_subtasks=1,
            skip_validation=True
        )
        
        print("\n" + "="*70)
        print("SUCCESS!")
        print("="*70)
        print(f"\nAnswer:\n{result['answer']}")
        
        metadata = result.get('metadata', {})
        print(f"\n📊 Metadata:")
        print(f"  ⏱️  Duration: {metadata.get('duration_ms', 0):.2f}ms")
        print(f"  ✅ Successful Tasks: {metadata.get('successful_tasks', 0)}")
        print(f"  🤖 Agents: {metadata.get('agents_used', [])}")
        
        data = result.get('data', {})
        if data.get('summary_statistics'):
            stats = data['summary_statistics']
            print(f"\n📈 Data Summary:")
            if 'total_market_data_records' in stats:
                print(f"  📝 Records: {stats['total_market_data_records']}")
            if 'market_data' in stats:
                md_stats = stats['market_data']
                print(f"  💰 Price Range: ${md_stats.get('min_price', 0):.4f} - ${md_stats.get('max_price', 0):.4f}")
        
        print(f"\n💾 Output: {result.get('output_path')}")
        print(f"📂 Database: workspace/orchestrator_results.db")
        
        print("\n✅ TEST PASSED!\n")
        return True
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        logger.exception("Test failed")
        return False


if __name__ == "__main__":
    success = quick_test()
    sys.exit(0 if success else 1)

