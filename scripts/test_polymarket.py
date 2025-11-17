"""Test script for Polymarket Agent."""

import sys
import argparse
import logging
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.agents.polymarket_agent.run import PolymarketAgent
from src.core.logging_config import setup_logging

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


# Sample queries for testing
# SAMPLE_QUERIES = {
#     1:  {
#         "query": "Will federal shutdown end by November 15, 2025?",
#         "description": "Government shutdown prediction"
#     }
# }


def list_queries():
    """List all available sample queries."""
    print("\n📋 Available Sample Queries:\n")
    for num, info in SAMPLE_QUERIES.items():
        print(f"  {num}. {info['query']}")
        print(f"     ({info['description']})\n")
    print(f"Total: {len(SAMPLE_QUERIES)} sample queries\n")


def run_query(query_num: int, max_results: int = 10):
    """
    Run a sample query.
    
    Args:
        query_num: Query number (1-10)
        max_results: Maximum results to return
    """
    if query_num not in SAMPLE_QUERIES:
        print(f"❌ Invalid query number: {query_num}")
        print(f"   Available: 1-{len(SAMPLE_QUERIES)}")
        return
    
    query_info = SAMPLE_QUERIES[query_num]
    query = query_info['query']
    
    print(f"\n>> Running Query #{query_num}:")
    print(f"   {query}")
    print(f"   Max results: {max_results}\n")
    
    try:
        # Initialize agent
        agent = PolymarketAgent()
        
        # Run query
        output_path = agent.run(
            query=query,
            limit=max_results
        )
        
        print(f"[SUCCESS] Query completed successfully!")
        print(f"   Output: {output_path}\n")
        
        # Read and display results
        import json
        with open(output_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        result_data = data['data'][0]
        session_id = result_data['session_id']
        expanded_keywords = result_data.get('expanded_keywords', [])
        markets = result_data['markets']
        
        print(f"\n[RESULTS]")
        print(f"   Session ID: {session_id}")
        print(f"   Expanded Keywords: {', '.join(expanded_keywords)}")
        print(f"   Markets Found: {len(markets)}\n")
        
        if markets:
            print("\n[TOP MARKETS]")
            print("   (Note: If results don't match your query, those markets may not exist on Polymarket)")
            for i, market in enumerate(markets[:5], 1):
                print(f"\n   {i}. {market.get('title', 'No title')}")
                print(f"      URL: {market.get('url', 'N/A')}")
                
                prices = market.get('prices', {})
                if isinstance(prices, dict):
                    if 'Yes' in prices and 'No' in prices:
                        print(f"      Prices: Yes {prices['Yes']:.2%}, No {prices['No']:.2%}")
                    elif 'price' in prices:
                        print(f"      Price: {prices['price']:.2%}")
                
                volume = market.get('volume', 0)
                if volume:
                    print(f"      Volume: ${volume:,.0f}")
        else:
            print("   ⚠️  No active markets found for this query.")
            print("   This may mean:")
            print("   - No markets match the expanded keywords")
            print("   - All matching markets are closed/inactive")
            print("   - Try a different query or check Polymarket directly\n")
    
    except Exception as e:
        print(f"[ERROR] Error running query: {e}")
        logger.exception("Query failed")


def run_custom_query(query: str, max_results: int = 10):
    """
    Run a custom query.
    
    Args:
        query: Custom search query
        max_results: Maximum results to return
    """
    print(f"\n>> Running Custom Query:")
    print(f"   {query}")
    print(f"   Max results: {max_results}\n")
    
    try:
        # Initialize agent
        agent = PolymarketAgent()
        
        # Run query
        output_path = agent.run(
            query=query,
            limit=max_results
        )
        
        print(f"[SUCCESS] Query completed successfully!")
        print(f"   Output: {output_path}\n")
        
        # Read and display results
        import json
        with open(output_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        result_data = data['data'][0]
        session_id = result_data['session_id']
        expanded_keywords = result_data.get('expanded_keywords', [])
        markets = result_data['markets']
        
        print(f"\n[RESULTS]")
        print(f"   Session ID: {session_id}")
        print(f"   Expanded Keywords: {', '.join(expanded_keywords)}")
        print(f"   Markets Found: {len(markets)}\n")
        
        if markets:
            print("\n[TOP MARKETS]")
            print("   (Note: If results don't match your query, those markets may not exist on Polymarket)")
            for i, market in enumerate(markets[:5], 1):
                print(f"\n   {i}. {market.get('title', 'No title')}")
                print(f"      URL: {market.get('url', 'N/A')}")
                
                prices = market.get('prices', {})
                if isinstance(prices, dict):
                    if 'Yes' in prices and 'No' in prices:
                        print(f"      Prices: Yes {prices['Yes']:.2%}, No {prices['No']:.2%}")
                    elif 'price' in prices:
                        print(f"      Price: {prices['price']:.2%}")
                
                volume = market.get('volume', 0)
                if volume:
                    print(f"      Volume: ${volume:,.0f}")
        else:
            print("   ⚠️  No active markets found for this query.")
            print("   This may mean:")
            print("   - No markets match the expanded keywords")
            print("   - All matching markets are closed/inactive")
            print("   - Try a different query or check Polymarket directly\n")
    
    except Exception as e:
        print(f"❌ Error running custom query: {e}")
        logger.exception("Custom query failed")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Test Polymarket Agent with sample or custom queries"
    )
    
    parser.add_argument(
        '--list',
        action='store_true',
        help='List all available sample queries'
    )
    
    parser.add_argument(
        '--query',
        type=int,
        metavar='N',
        help='Run sample query N (1-10)'
    )
    
    parser.add_argument(
        '--custom',
        type=str,
        metavar='QUERY',
        help='Run custom query'
    )
    
    parser.add_argument(
        '--max-results',
        type=int,
        default=10,
        metavar='N',
        help='Maximum results to return (default: 10)'
    )
    
    args = parser.parse_args()
    
    # Handle commands
    if args.list:
        list_queries()
    
    elif args.query:
        run_query(args.query, args.max_results)
    
    elif args.custom:
        run_custom_query(args.custom, args.max_results)
    
    else:
        # No args, show help
        parser.print_help()
        print("\n💡 Examples:")
        print("   python scripts/test_polymarket.py --list")
        print("   python scripts/test_polymarket.py --query 1")
        print("   python scripts/test_polymarket.py --custom 'Will AI reach AGI in 2025?'")
        print("   python scripts/test_polymarket.py --query 3 --max-results 5\n")


if __name__ == "__main__":
    main()

