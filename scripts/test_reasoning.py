"""
Test script for ReasoningAgent v2.0 with simplified output.

Usage:
    python scripts/test_reasoning.py --list
    python scripts/test_reasoning.py --query 1
    python scripts/test_reasoning.py --custom "federal shutdown"
"""

import sys
import io
import json
import argparse
from pathlib import Path

# Fix Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.logging_config import setup_logging
from src.agents.reasoning_agent.run import ReasoningAgent

# Setup logging
setup_logging()


# Sample queries demonstrating different intents
SAMPLE_QUERIES = [
    {
        "id": 1,
        "intent": "current_search",
        "query": "Bitcoin price predictions",
        "description": "Current market search - simple query"
    },
    {
        "id": 2,
        "intent": "historical_opinion",
        "query": "What was the market opinion on November 1st about federal shutdown ending?",
        "description": "Historical opinion - specific date"
    },
    {
        "id": 3,
        "intent": "historical_opinion",
        "query": "What did the market think about Ukraine ceasefire on Oct 15?",
        "description": "Historical opinion - past event"
    },
    {
        "id": 4,
        "intent": "price_change",
        "query": "How did Bitcoin $100k predictions change from October to November?",
        "description": "Price change - date range"
    },
    {
        "id": 5,
        "intent": "price_change",
        "query": "How has opinion shifted on Trump 2024 from last month to now?",
        "description": "Price change - relative dates"
    },
    {
        "id": 6,
        "intent": "market_movement",
        "query": "When did the market shift on Russia ceasefire?",
        "description": "Market movement - timing question"
    },
    {
        "id": 7,
        "intent": "current_search",
        "query": "Supreme Court decisions",
        "description": "Current search - topical"
    },
    {
        "id": 8,
        "intent": "historical_opinion",
        "query": "What was opinion on November 1 about government shutdown?",
        "description": "Historical opinion - user's example"
    }
]


def list_queries():
    """List all sample queries."""
    print("\n" + "=" * 80)
    print("REASONING AGENT - SAMPLE QUERIES")
    print("=" * 80 + "\n")
    
    for q in SAMPLE_QUERIES:
        print(f"[{q['id']}] {q['intent'].upper()}")
        print(f"    Query: \"{q['query']}\"")
        print(f"    Description: {q['description']}")
        print()


def run_query(query_id: int):
    """Run a sample query by ID."""
    # Find query
    query_obj = next((q for q in SAMPLE_QUERIES if q['id'] == query_id), None)
    if not query_obj:
        print(f"[ERROR] Query ID {query_id} not found")
        return
    
    query = query_obj['query']
    expected_intent = query_obj['intent']
    
    print("\n" + "=" * 80)
    print(f"RUNNING QUERY #{query_id}")
    print("=" * 80)
    print(f"\nQuery: {query}")
    print(f"Expected Intent: {expected_intent}")
    print("\n" + "-" * 80 + "\n")
    
    # Run agent
    agent = ReasoningAgent()
    output_path = agent.run(query)
    
    # Read and display output
    with open(output_path, 'r', encoding='utf-8') as f:
        result = json.load(f)
    
    print("\n" + "=" * 80)
    print("RESULT")
    print("=" * 80 + "\n")
    
    data = result['data'][0]
    result_data = data.get('result', {})
    
    # v2.0 structure
    parsed = result_data.get('parsed', {})
    comparison_date = result_data.get('comparison_date')
    date_source = result_data.get('date_source', 'default')
    
    print(f"[TOPIC & COMPARISON]")
    print(f"   Topic: {parsed.get('topic', 'N/A')}")
    print(f"   Comparing: Today vs {comparison_date} ({date_source})")
    print()
    
    markets = result_data.get('markets', [])
    metadata = result_data.get('metadata', {})
    error = result_data.get('error')
    
    if error:
        print(f"[ERROR] {error}")
        return
    
    print(f"[MARKETS FOUND] {len(markets)}")
    if metadata.get('low_volume_count', 0) > 0:
        print(f"[LOW VOLUME] {metadata['low_volume_count']} markets flagged")
    
    # Show markets - just today vs comparison date
    for i, market in enumerate(markets[:5], 1):
        print(f"\n{i}. {market['title'][:75]}")
        
        # TODAY
        prices = market.get('prices', {})
        outcomes = market.get('outcomes', ['Yes', 'No'])
        
        print(f"   TODAY: {outcomes[0]} {prices.get(outcomes[0], 0)*100:.1f}%, {outcomes[1]} {prices.get(outcomes[1], 0)*100:.1f}%")
        
        # HISTORICAL
        hist_price = market.get('historical_price', {})
        if hist_price and hist_price.get('yes') is not None:
            print(f"   {comparison_date}: {outcomes[0]} {hist_price['yes']*100:.1f}%, {outcomes[1]} {hist_price['no']*100:.1f}%")
            
            price_change = market.get('price_change')
            if price_change:
                print(f"   Change: {price_change['yes_change']:+.1f}pp ({price_change['direction']})")
        else:
            note = market.get('historical_note', 'No historical data')
            print(f"   {comparison_date}: {note[:45]}...")
        
        # Volume
        volume = market.get('volume', 0)
        if market.get('low_volume_flag'):
            print(f"   ⚠️ {market.get('volume_note', 'Low volume')}")
        else:
            print(f"   Volume: ${volume:,.0f}")
    
    print(f"\n[SUCCESS] Output saved to: {output_path}")
    print()


def run_custom(query: str):
    """Run a custom query."""
    print("\n" + "=" * 80)
    print("RUNNING CUSTOM QUERY")
    print("=" * 80)
    print(f"\nQuery: {query}")
    print("\n" + "-" * 80 + "\n")
    
    # Run agent
    agent = ReasoningAgent()
    output_path = agent.run(query)
    
    # Read and display output
    with open(output_path, 'r', encoding='utf-8') as f:
        result = json.load(f)
    
    print("\n" + "=" * 80)
    print("RESULT")
    print("=" * 80 + "\n")
    
    data = result['data'][0]
    result_data = data.get('result', {})
    
    # v2.0 structure
    parsed = result_data.get('parsed', {})
    comparison_date = result_data.get('comparison_date')
    date_source = result_data.get('date_source', 'default')
    
    print(f"[TOPIC & COMPARISON]")
    print(f"   Topic: {parsed.get('topic', 'N/A')}")
    print(f"   Comparing: Today vs {comparison_date} ({date_source})")
    print()
    
    markets = result_data.get('markets', [])
    metadata = result_data.get('metadata', {})
    error = result_data.get('error')
    
    if error:
        print(f"[ERROR] {error}")
        return
    
    print(f"[MARKETS FOUND] {len(markets)}")
    if metadata.get('low_volume_count', 0) > 0:
        print(f"[LOW VOLUME] {metadata['low_volume_count']} markets flagged")
    print()
    
    # Show markets - just today vs comparison date
    for i, market in enumerate(markets[:5], 1):
        print(f"\n{i}. {market['title'][:75]}")
        print()
        
        # TODAY'S STATE
        prices = market.get('prices', {})
        outcomes = market.get('outcomes', ['Yes', 'No'])
        
        print(f"   TODAY:")
        yes_price = prices.get(outcomes[0], 0) * 100
        no_price = prices.get(outcomes[1], 0) * 100
        print(f"      {outcomes[0]}: {yes_price:.1f}%")
        print(f"      {outcomes[1]}: {no_price:.1f}%")
        
        # HISTORICAL STATE
        hist_price = market.get('historical_price', {})
        if hist_price and hist_price.get('yes') is not None:
            print(f"\n   {comparison_date}:")
            hist_yes = hist_price['yes'] * 100
            hist_no = hist_price['no'] * 100
            print(f"      {outcomes[0]}: {hist_yes:.1f}%")
            print(f"      {outcomes[1]}: {hist_no:.1f}%")
            
            # Change
            price_change = market.get('price_change')
            if price_change:
                change = price_change['yes_change']
                direction = price_change['direction']
                arrow = "UP" if direction == "up" else "DOWN" if direction == "down" else "FLAT"
                print(f"\n   CHANGE: {change:+.1f}pp ({arrow})")
        else:
            note = market.get('historical_note', 'No historical data')
            print(f"\n   NOTE: {note[:55]}...")
        
        # Volume
        volume = market.get('volume', 0)
        if market.get('low_volume_flag'):
            print(f"\n   WARNING: {market.get('volume_note', 'Low volume')}")
        else:
            print(f"\n   Volume: ${volume:,.0f}")
    
    print(f"\n[SUCCESS] Output saved to: {output_path}")
    print()


def main():
    parser = argparse.ArgumentParser(description='Test ReasoningAgent with complex queries')
    parser.add_argument('--list', action='store_true', help='List all sample queries')
    parser.add_argument('--query', type=int, metavar='N', help='Run sample query N')
    parser.add_argument('--custom', type=str, metavar='QUERY', help='Run custom query')
    
    args = parser.parse_args()
    
    if args.list:
        list_queries()
    elif args.query:
        run_query(args.query)
    elif args.custom:
        run_custom(args.custom)
    else:
        print("Usage:")
        print("  python scripts/test_reasoning.py --list")
        print("  python scripts/test_reasoning.py --query 1")
        print("  python scripts/test_reasoning.py --custom \"What was opinion on Nov 1?\"")


if __name__ == "__main__":
    main()

