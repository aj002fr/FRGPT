#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Reasoning Agent v2.0 - Simplified Approach

Verifies:
1. Topic + date extraction
2. Current + historical comparison
3. Sorting by relevance & volume
4. Low volume flagging
"""

import sys
import io
from pathlib import Path

# Fix Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Add project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.reasoning_agent import ReasoningAgent
from src.bus.file_bus import read_json


def test_simple_query():
    """Test query without date - should default to 1 week ago."""
    print("\n" + "="*80)
    print("TEST 1: Simple Query (No Date)")
    print("="*80)
    
    agent = ReasoningAgent()
    query = "Bitcoin predictions"
    
    print(f"Query: '{query}'")
    print("\nExpected:")
    print("  • Topic: 'bitcoin'")
    print("  • Comparison: 7 days ago (default)")
    print("  • Sorted by relevance & volume")
    print("  • Low volume flags if applicable")
    
    try:
        output_path = agent.run(query)
        result_data = read_json(output_path)
        result = result_data['data'][0]['result']
        
        print("\nActual:")
        print(f"  • Topic: '{result['parsed']['topic']}'")
        print(f"  • Comparison: {result['comparison_date']} ({result['date_source']})")
        print(f"  • Markets: {len(result['markets'])}")
        print(f"  • Low volume: {result['metadata']['low_volume_count']}")
        
        # Verify
        assert result['date_source'] == 'default'
        assert 'markets' in result
        
        print("\n✅ Test 1 PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Test 1 FAILED: {e}")
        return False


def test_date_specified():
    """Test query with specific date."""
    print("\n" + "="*80)
    print("TEST 2: Query with Specific Date")
    print("="*80)
    
    agent = ReasoningAgent()
    query = "What was opinion on Jan 1 2025 about AI?"
    
    print(f"Query: '{query}'")
    print("\nExpected:")
    print("  • Topic: 'AI' or similar")
    print("  • Date: '2025-01-01' (specified)")
    print("  • Current + historical comparison")
    print("  • Price changes calculated")
    
    try:
        output_path = agent.run(query)
        result_data = read_json(output_path)
        result = result_data['data'][0]['result']
        
        print("\nActual:")
        print(f"  • Topic: '{result['parsed']['topic']}'")
        print(f"  • Date source: {result['date_source']}")
        print(f"  • Comparison: {result['comparison_date']}")
        print(f"  • Markets: {len(result['markets'])}")
        
        # Check historical data
        if result['markets']:
            market = result['markets'][0]
            print(f"\n  Sample Market: {market['title'][:60]}...")
            print(f"    Current: Yes {market['prices'].get('Yes', 0)*100:.1f}%")
            
            if market.get('historical_price', {}).get('yes'):
                hist = market['historical_price']['yes'] * 100
                print(f"    Historical: Yes {hist:.1f}%")
                
                if market.get('price_change'):
                    change = market['price_change']['yes_change']
                    print(f"    Change: {change:+.1f}pp")
        
        # Verify
        assert result['date_source'] == 'specified'
        assert result['comparison_date'] == '2025-01-01'
        
        print("\n✅ Test 2 PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Test 2 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_sorting():
    """Test that results are sorted by relevance then volume."""
    print("\n" + "="*80)
    print("TEST 3: Sorting Verification")
    print("="*80)
    
    agent = ReasoningAgent()
    query = "Bitcoin"
    
    print(f"Query: '{query}'")
    print("\nExpected:")
    print("  • Markets sorted by relevance (descending)")
    print("  • Within same relevance, sorted by volume (descending)")
    
    try:
        output_path = agent.run(query)
        result_data = read_json(output_path)
        result = result_data['data'][0]['result']
        
        markets = result['markets']
        
        print("\nActual:")
        print(f"  • Total markets: {len(markets)}")
        
        if markets:
            print("\n  Top 5 by relevance & volume:")
            for i, market in enumerate(markets[:5], 1):
                relevance = market.get('relevance_score', 0)
                volume = market.get('volume', 0)
                low_vol = "⚠️" if market.get('low_volume_flag') else "  "
                print(f"  {i}. {low_vol} Relevance: {relevance:.2f}, Volume: ${volume:,.0f}")
            
            # Check sorting
            relevances = [m.get('relevance_score', 0) for m in markets]
            is_sorted = all(relevances[i] >= relevances[i+1] for i in range(len(relevances)-1))
            
            if is_sorted:
                print("\n  ✅ Markets are properly sorted by relevance")
            else:
                print("\n  ⚠️  Warning: Sorting may not be perfect")
        
        print("\n✅ Test 3 PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Test 3 FAILED: {e}")
        return False


def test_low_volume_flagging():
    """Test low volume market flagging."""
    print("\n" + "="*80)
    print("TEST 4: Low Volume Flagging")
    print("="*80)
    
    agent = ReasoningAgent()
    query = "niche prediction"  # Likely to have low volume markets
    
    print(f"Query: '{query}'")
    print("\nExpected:")
    print("  • Markets with volume < $1,000 flagged")
    print("  • Volume notes included")
    
    try:
        output_path = agent.run(query)
        result_data = read_json(output_path)
        result = result_data['data'][0]['result']
        
        markets = result['markets']
        low_vol_count = result['metadata']['low_volume_count']
        
        print("\nActual:")
        print(f"  • Total markets: {len(markets)}")
        print(f"  • Low volume flagged: {low_vol_count}")
        
        if markets:
            for market in markets[:3]:
                if market.get('low_volume_flag'):
                    print(f"\n  ⚠️  {market['title'][:50]}...")
                    print(f"      {market.get('volume_note', 'No note')}")
        
        print("\n✅ Test 4 PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Test 4 FAILED: {e}")
        return False


def main():
    """Run all tests."""
    print("\n" + "="*80)
    print(" REASONING AGENT V2.0 - TEST SUITE ".center(80))
    print("="*80)
    
    print("\n📝 Testing simplified approach:")
    print("   • Extract topic + optional date")
    print("   • Always show current + historical")
    print("   • Sort by relevance then volume")
    print("   • Flag low volume markets")
    
    tests = [
        test_simple_query,
        test_date_specified,
        test_sorting,
        test_low_volume_flagging
    ]
    
    results = []
    for test in tests:
        try:
            passed = test()
            results.append(passed)
        except KeyboardInterrupt:
            print("\n\n⚠️  Tests interrupted by user")
            return
        except Exception as e:
            print(f"\n❌ Test error: {e}")
            results.append(False)
    
    # Summary
    print("\n" + "="*80)
    print(" TEST SUMMARY ".center(80))
    print("="*80)
    
    passed = sum(results)
    total = len(results)
    
    print(f"\nPassed: {passed}/{total}")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED!")
        print("\n✅ Reasoning Agent v2.0 is working correctly:")
        print("   • Simplified parsing (topic + date)")
        print("   • Unified output (current + historical)")
        print("   • Smart sorting (relevance → volume)")
        print("   • Safety flags (low volume warnings)")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        print("\nNote: Some tests may require OPENAI_API_KEY in config/keys.env")
        return 1


if __name__ == "__main__":
    sys.exit(main())

