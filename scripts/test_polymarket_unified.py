"""
Test script for unified Polymarket search tool with historical price API.

This demonstrates the API-based workflow:
1. Search for markets using a query (get current prices + token IDs)
2. For each market, fetch historical price from Polymarket CLOB API
3. Calculate price change (delta and percentage)
4. Display results with price movement analysis
"""

import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.servers.polymarket.unified_search import search_polymarket_with_history
import json


def test_unified_search():
    """Test the unified search tool that fetches historical prices from API."""
    
    print("=" * 80)
    print("UNIFIED POLYMARKET SEARCH TEST")
    print("Current Prices vs Historical Prices (from API)")
    print("=" * 80)
    
    # Test query
    query = "bitcoin 2025 predictions"
    limit = 3
    days_back = 7  # Compare with 1 week ago
    
    print(f"\n📊 Query: {query}")
    print(f"📊 Limit: {limit}")
    print(f"📊 Comparison: {days_back} days ago")
    print("\n" + "-" * 80)
    
    try:
        # Call unified tool - does everything:
        # 1. Searches markets via API (current prices + token IDs)
        # 2. For each market, fetches historical price using token ID from CLOB API
        # 3. Calculates price change (delta and percentage)
        result = search_polymarket_with_history(
            query=query,
            limit=limit,
            days_back=days_back
        )
        
        # Display results
        print("\n✅ PRICE ANALYSIS COMPLETE\n")
        
        # Market data with price changes
        markets = result['markets']
        metadata = result['metadata']
        
        print(f"🔍 MARKETS FOUND: {len(markets)}")
        print(f"📅 Current date: {metadata['current_date']}")
        print(f"📅 Historical date: {metadata['historical_date']}")
        print("=" * 80)
        
        for i, market in enumerate(markets, 1):
            print(f"\n{i}. {market['title'][:80]}")
            print(f"   Market ID: {market['market_id']}")
            print(f"   Token IDs: {market.get('clob_token_ids', ['N/A'])[:2]}")
            print(f"   URL: {market.get('url', 'N/A')}")
            
            # Current price
            current = market['current_price']
            print(f"\n   📈 CURRENT ({metadata['current_date']})")
            yes_key = 'yes' if 'yes' in current else 'Yes'
            no_key = 'no' if 'no' in current else 'No'
            print(f"      {yes_key}: {current.get(yes_key, 0):.2%}  |  {no_key}: {current.get(no_key, 0):.2%}")
            
            # Historical price
            if market.get('historical_price'):
                historical = market['historical_price']
                print(f"\n   📜 HISTORICAL ({metadata['historical_date']})")
                print(f"      yes: {historical['yes']:.2%}  |  no: {historical['no']:.2%}")
                
                # Price change
                if market.get('price_change'):
                    change = market['price_change']
                    direction_emoji = {"up": "🟢 ↑", "down": "🔴 ↓", "stable": "⚪ →"}
                    emoji = direction_emoji.get(change['direction'], "⚪")
                    
                    print(f"\n   {emoji} CHANGE")
                    print(f"      yes: {change['yes']:+.2%} ({change['yes_percent']:+.1f}%)")
                    print(f"      no:  {change['no']:+.2%} ({change['no_percent']:+.1f}%)")
                    print(f"      Direction: {change['direction'].upper()}")
                    print(f"      Data points: {market.get('data_points', 'N/A')}")
                    
                    # Interpretation
                    if change['direction'] == 'up':
                        print(f"      💡 Market sentiment has INCREASED")
                    elif change['direction'] == 'down':
                        print(f"      💡 Market sentiment has DECREASED")
                    else:
                        print(f"      💡 Market sentiment is STABLE")
            else:
                print(f"\n   ⚠️  No historical data available")
                if market.get('note'):
                    print(f"      Note: {market['note']}")
                if market.get('error'):
                    print(f"      Error: {market['error']}")
            
            # Volume
            if market.get('volume'):
                print(f"\n   💰 Volume: ${market['volume']:,.0f}")
            
            print()
        
        # Summary
        print("=" * 80)
        print(f"🔗 SESSION ID: {metadata['session_id']}")
        print(f"⏰ TIMESTAMP: {metadata['timestamp']}")
        print("=" * 80)
        
        print("\n✅ Test completed successfully!")
        print(f"\n💡 HOW IT WORKS:")
        print(f"   1. Query searches Polymarket /public-search API")
        print(f"   2. Each market includes token IDs (clob_token_ids)")
        print(f"   3. For each token ID, fetch historical price from CLOB API:")
        print(f"      GET https://clob.polymarket.com/prices-history?market=<token_id>&...")
        print(f"   4. Calculate price change (current - historical)")
        print(f"   5. Show sentiment movement (up/down/stable)")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


def test_custom_date():
    """Test with a custom historical date."""
    
    print("\n\n" + "=" * 80)
    print("CUSTOM DATE COMPARISON TEST")
    print("=" * 80)
    
    query = "trump 2024"
    historical_date = "2024-11-01"  # Specific date
    
    print(f"\n📊 Query: {query}")
    print(f"📅 Historical date: {historical_date}")
    print("\n" + "-" * 80)
    
    try:
        result = search_polymarket_with_history(
            query=query,
            limit=2,
            historical_date=historical_date
        )
        
        markets = result['markets']
        metadata = result['metadata']
        
        print(f"\n✅ Found {len(markets)} markets")
        print(f"📅 Comparing: {metadata['current_date']} vs {metadata['historical_date']}")
        
        for market in markets:
            print(f"\n📊 {market['title'][:60]}")
            if market.get('price_change'):
                change = market['price_change']
                print(f"   Change: {change['yes']:+.2%} ({change['direction']})")
            else:
                print(f"   No historical data available")
        
        print("\n✅ Custom date test completed!")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


if __name__ == "__main__":
    print("\n🔧 NOTE: This uses the Polymarket CLOB API endpoint:")
    print("   https://clob.polymarket.com/prices-history")
    print("   If it fails, the API might be rate-limiting or temporarily unavailable.\n")
    
    success1 = test_unified_search()
    success2 = test_custom_date()
    
    sys.exit(0 if (success1 and success2) else 1)
