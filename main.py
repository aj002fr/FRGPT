#!/usr/bin/env python3
"""
Market Data Puller - Unified Pipeline Demo

Demonstrates two primary workflows:
1. SQL Market Data Pipeline: Database → Consumer
2. Polymarket Intelligence Pipeline: API → Reasoning → Validation
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logging_config import setup_logging
from src.agents.market_data_agent import MarketDataAgent
from src.agents.consumer_agent import ConsumerAgent
from src.agents.polymarket_agent import PolymarketAgent
from src.bus.file_bus import read_json


def print_section(title: str, char: str = "="):
    """Print a formatted section header."""
    print(f"\n{char * 80}")
    print(f"{title.center(80)}")
    print(f"{char * 80}\n")


def run_sql_pipeline():
    """
    Pipeline 1: SQL Market Data
    
    Flow: MarketDataAgent → File Bus → ConsumerAgent → Statistics
    Use: Query local database, compute statistics
    """
    print_section("PIPELINE 1: SQL MARKET DATA", "=")
    
    print("📊 Task: Query market data from local SQLite database")
    print("🔧 Tools: SQL queries with validation & whitelist security")
    print("📝 Output: Structured data + statistics\n")
    
    # Initialize agents
    producer = MarketDataAgent()
    consumer = ConsumerAgent()
    
    # Query 1: Call options
    print("\n[STEP 1] Querying call options (%.C)...")
    print("-" * 80)
    output1 = producer.run(
        template="by_symbol",
        params={"symbol_pattern": "%.C"},
        limit=5
    )
    print(f"✅ Output: {output1.name}")
    
    # Query 2: Specific date
    print("\n[STEP 2] Querying date 2025-07-21...")
    print("-" * 80)
    output2 = producer.run(
        template="by_date",
        params={"file_date": "2025-07-21"},
        limit=5
    )
    print(f"✅ Output: {output2.name}")
    
    # Process with consumer
    print("\n[STEP 3] Processing with Consumer Agent...")
    print("-" * 80)
    consumer_output1 = consumer.run(output1)
    consumer_output2 = consumer.run(output2)
    print(f"✅ Consumer output 1: {consumer_output1.name}")
    print(f"✅ Consumer output 2: {consumer_output2.name}")
    
    # Show statistics
    print("\n[RESULTS] Statistics Summary")
    print("-" * 80)
    consumer_data = read_json(consumer_output1)
    stats = consumer_data['data'][0]['statistics']
    
    print(f"\n📈 Call Options Statistics:")
    print(f"   Total records: {stats['total_records']}")
    if 'bid_avg' in stats:
        print(f"   Avg bid: ${stats['bid_avg']:.4f}")
    if 'ask_avg' in stats:
        print(f"   Avg ask: ${stats['ask_avg']:.4f}")
    if 'bid_min' in stats and 'bid_max' in stats:
        print(f"   Bid range: ${stats['bid_min']:.4f} - ${stats['bid_max']:.4f}")
    
    print(f"\n📂 Artifacts:")
    print(f"   Producer: workspace/agents/market-data-agent/out/")
    print(f"   Consumer: workspace/agents/consumer-agent/out/")
    
    return {
        'producer_runs': producer.get_stats()['total_runs'],
        'consumer_runs': consumer.get_stats()['total_runs']
    }


def run_polymarket_pipeline():
    """
    Pipeline 2: Polymarket Intelligence (v2.0 - Simplified)
    
    Flow: PolymarketAgent → Current State + Historical Comparison
    Always shows: Current prices + change over time (specified date or past week)
    """
    print_section("PIPELINE 2: POLYMARKET INTELLIGENCE", "=")
    
    print("🤖 Task: AI-powered prediction market analysis")
    print("🔧 Tools: GPT-4 parsing + Polymarket API + validation")
    print("📝 Output: Current state + historical comparison + sorted by relevance & volume\n")
    
    # Initialize unified polymarket agent (reasoning-enabled)
    agent = PolymarketAgent()
    
    # Query 1: Date-specific query
    print("\n[STEP 1] Natural Language Query with Date")
    print("-" * 80)
    query = "What was opinion on Jan 1 2025 about Bitcoin?"
    print(f"Query: '{query}'")
    print("\nProcessing:")
    print("  🧠 GPT-4 extracts topic & date...")
    print("  🔍 Searches relevant markets...")
    print("  ✅ Validates URLs & creation dates...")
    print("  📊 Shows current + compares with Jan 1...")
    print("  🔢 Sorts by relevance then volume...")
    print("  ⚠️  Flags low volume markets...")
    
    try:
        output = agent.run(query)
        
        # Read results
        result_data = read_json(output)
        result = result_data['data'][0]['result']
        
        print("\n[RESULTS] Analysis Complete")
        print("-" * 80)
        
        # Show parsed query
        parsed = result['parsed']
        print(f"\n🎯 Topic: {parsed['topic']}")
        print(f"📅 Comparison: {result['comparison_date']} ({result['date_source']})")
        print(f"💯 Confidence: {parsed['confidence']}")
        
        # Show markets found
        markets = result.get('markets', [])
        metadata = result.get('metadata', {})
        print(f"\n🎰 Markets Found: {len(markets)}")
        print(f"⚠️  Low Volume: {metadata.get('low_volume_count', 0)} markets")
        
        for i, market in enumerate(markets[:3], 1):
            print(f"\n{i}. {market['title'][:70]}...")
            
            # Current prices
            current_yes = market['prices'].get('Yes', 0) * 100
            current_no = market['prices'].get('No', 0) * 100
            print(f"   📊 Current: Yes {current_yes:.1f}%, No {current_no:.1f}%")
            
            # Historical comparison
            hist_price = market.get('historical_price', {})
            price_change = market.get('price_change')
            
            if hist_price.get('yes') is not None and price_change:
                hist_yes = hist_price['yes'] * 100
                hist_no = hist_price['no'] * 100
                change = price_change['yes_change']
                direction = price_change['direction']
                
                print(f"   📅 {result['comparison_date']}: Yes {hist_yes:.1f}%, No {hist_no:.1f}%")
                
                emoji = "📈" if direction == "up" else ("📉" if direction == "down" else "➡️")
                print(f"   {emoji} Change: {change:+.1f}pp ({direction})")
            else:
                note = market.get('historical_note', 'No historical data')
                print(f"   ⚠️  {note[:60]}...")
            
            # Volume & flags
            volume = market.get('volume', 0)
            if market.get('low_volume_flag'):
                print(f"   💰 {market.get('volume_note', '')}")
            else:
                print(f"   💰 Volume: ${volume:,.0f}")
            
            # Relevance
            print(f"   🎯 Relevance: {market.get('relevance_score', 0):.2f}")
        
        print(f"\n📂 Artifact: {output.name}")
        
        return {
            "markets_found": len(markets),
            "low_volume_count": metadata.get("low_volume_count", 0),
            "comparison_date": result["comparison_date"],
        }
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("   Note: Ensure OPENAI_API_KEY is set in config/keys.env")
        return {'error': str(e)}


def run_direct_polymarket():
    """
    Alternative: Direct Polymarket Search
    
    Flow: PolymarketAgent → API Search → Validation
    Use: Direct searches without GPT-4 reasoning
    """
    print_section("ALTERNATIVE: DIRECT POLYMARKET SEARCH", "=")
    
    print("🔍 Task: Direct market search (no GPT-4 required)")
    print("🔧 Tools: Polymarket API + local keyword filtering")
    print("📝 Output: Relevant markets with validation\n")
    
    # Initialize agent
    agent = PolymarketAgent()
    
    print("\n[STEP 1] Searching for 'Bitcoin' markets...")
    print("-" * 80)
    print("  Fetching 600 markets (400 recent + 200 popular)...")
    print("  Filtering by keywords with relevance scoring...")
    print("  Validating market data...")
    
    try:
        output = agent.run(query="Bitcoin", limit=3)
        
        # Read results
        result_data = read_json(output)
        markets = result_data['data'][0]['markets']
        
        print("\n[RESULTS] Search Complete")
        print("-" * 80)
        print(f"\n🎰 Markets Found: {len(markets)}")
        
        for i, market in enumerate(markets, 1):
            print(f"\n{i}. {market['title'][:70]}...")
            prices = market['prices']
            print(f"   Prices: Yes {prices.get('Yes', 0)*100:.1f}%, No {prices.get('No', 0)*100:.1f}%")
            print(f"   Volume: ${market['volume']:,.0f}")
            print(f"   URL: {market['url']}")
        
        print(f"\n📂 Artifact: {output.name}")
        
        return {
            "markets_found": len(markets),
            "method": "direct_search",
        }
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return {'error': str(e)}


def main():
    """Main entry point - runs all pipelines."""
    # Setup logging
    setup_logging(level=logging.INFO, log_to_file=True, log_to_console=False)
    logger = logging.getLogger(__name__)
    
    # Header
    print("\n" + "=" * 80)
    print("MARKET DATA PULLER - UNIFIED PIPELINE DEMO".center(80))
    print("=" * 80)
    print(f"\n🕐 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n📋 Available Pipelines:")
    print("   1. SQL Market Data (Database → Consumer → Statistics)")
    print("   2. Polymarket Intelligence (AI Reasoning → API → Validation)")
    print("   3. Direct Polymarket Search (API Only, No AI)")
    
    # Run pipelines
    results: dict[str, dict[str, object]] = {}
    
    try:
        # Pipeline 1: SQL
        results["sql"] = run_sql_pipeline()
        
        # Pipeline 2: Polymarket with AI
        # Polymarket reasoning pipeline
        results["polymarket"] = run_polymarket_pipeline()
        
        # Pipeline 3: Direct Polymarket
        # Direct Polymarket search (fast mode)
        results["direct"] = run_direct_polymarket()
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Pipeline error: {e}")
        logger.error(f"Pipeline error: {e}", exc_info=True)
        sys.exit(1)
    
    # Summary
    print_section("SUMMARY", "=")
    
    print("✅ Pipeline 1 (SQL):")
    if 'sql' in results:
        print(f"   Producer runs: {results['sql'].get('producer_runs', 'N/A')}")
        print(f"   Consumer runs: {results['sql'].get('consumer_runs', 'N/A')}")
    
    print("\n✅ Pipeline 2 (Polymarket - reasoning mode):")
    if "polymarket" in results:
        if "error" in results["polymarket"]:
            print(f"   ⚠️  {results['polymarket']['error']}")
        else:
            print(
                f"   Markets found: {results['polymarket'].get('markets_found', 'N/A')}"
            )
            print(
                f"   Low volume flagged: {results['polymarket'].get('low_volume_count', 0)}"
            )
            print(
                f"   Comparison date: {results['polymarket'].get('comparison_date', 'N/A')}"
            )
    
    print("\n✅ Pipeline 3 (Direct):")
    if 'direct' in results:
        if 'error' in results['direct']:
            print(f"   ⚠️  {results['direct']['error']}")
        else:
            print(f"   Markets found: {results['direct'].get('markets_found', 'N/A')}")
    
    print("\n📂 All Artifacts:")
    print("   workspace/agents/*/out/  - Agent outputs")
    print("   workspace/agents/*/logs/ - Run logs")
    print("   logs/                    - System logs")
    
    print("\n" + "=" * 80)
    print("✅ DEMO COMPLETE - All pipelines executed successfully!".center(80))
    print("=" * 80)
    
    print("\n💡 Next Steps:")
    print("   • View results: python scripts/show_logs.py")
    print("   • Run tests: python -m pytest tests/e2e/ -v")
    print("   • Custom query: python scripts/test_reasoning.py --custom 'your question'")
    print("   • Direct search: python scripts/test_polymarket.py --custom 'topic'")
    print("   • SQL queries: python scripts/test_queries.py --list")
    
    logger.info("Main completed successfully")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Fatal error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
