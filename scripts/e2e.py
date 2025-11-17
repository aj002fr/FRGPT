#!/usr/bin/env python3
"""
E2E Script - Run full pipeline: produce → consume

Demonstrates the complete flow of the code-mode MCP system.
"""

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.market_data_agent import MarketDataAgent
from src.agents.consumer_agent import ConsumerAgent
from src.bus.file_bus import read_json


def main():
    """Run E2E pipeline."""
    print("="*80)
    print("CODE-MODE MCP - END-TO-END PIPELINE")
    print("="*80)
    
    # Step 1: Producer
    print("\n[STEP 1] Running Market Data Agent (Producer)")
    print("-"*80)
    
    producer = MarketDataAgent()
    
    print("Query 1: Call options (%.C)")
    output1 = producer.run(
        template="by_symbol",
        params={"symbol_pattern": "%.C"},
        limit=10
    )
    print(f"  [OK] Output: {output1}")
    
    print("\nQuery 2: Put options (%.P)")
    output2 = producer.run(
        template="by_symbol",
        params={"symbol_pattern": "%.P"},
        limit=10
    )
    print(f"  [OK] Output: {output2}")
    
    print(f"\n  Producer stats: {producer.get_stats()}")
    
    # Step 2: Consumer
    print("\n[STEP 2] Running Consumer Agent")
    print("-"*80)
    
    consumer = ConsumerAgent()
    
    print(f"Processing: {output1}")
    consumer_output1 = consumer.run(output1)
    print(f"  [OK] Output: {consumer_output1}")
    
    print(f"\nProcessing: {output2}")
    consumer_output2 = consumer.run(output2)
    print(f"  [OK] Output: {consumer_output2}")
    
    print(f"\n  Consumer stats: {consumer.get_stats()}")
    
    # Step 3: Display Results
    print("\n[STEP 3] Results")
    print("-"*80)
    
    print("\nProducer Output 1:")
    data1 = read_json(output1)
    print(f"  Rows: {data1['metadata']['row_count']}")
    print(f"  Query: {data1['metadata']['query']}")
    print(f"  Sample: {data1['data'][0] if data1['data'] else 'N/A'}")
    
    print("\nConsumer Output 1:")
    consumer_data1 = read_json(consumer_output1)
    stats1 = consumer_data1['data'][0]['statistics']
    print(f"  Statistics: {stats1}")
    
    print("\n" + "="*80)
    print("[SUCCESS] E2E PIPELINE COMPLETED SUCCESSFULLY")
    print("="*80)
    
    print("\nGenerated artifacts:")
    print(f"  Producer: workspace/agents/market-data-agent/out/")
    print(f"  Consumer: workspace/agents/consumer-agent/out/")
    print(f"  Logs: workspace/agents/*/logs/")
    
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

