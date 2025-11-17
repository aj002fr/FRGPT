#!/usr/bin/env python3
"""
Test Queries - Comprehensive set of queries to test on market_data.db

Run all queries or select specific ones.
"""

import sys
from pathlib import Path
import json

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.market_data_agent import MarketDataAgent
from src.agents.consumer_agent import ConsumerAgent
from src.bus.file_bus import read_json


# Define test queries
TEST_QUERIES = [
    {
        "name": "All Call Options",
        "description": "Get all call options (C suffix)",
        "template": "by_symbol",
        "params": {"symbol_pattern": "%.C"},
        "limit": 20
    },
    {
        "name": "All Put Options",
        "description": "Get all put options (P suffix)",
        "template": "by_symbol",
        "params": {"symbol_pattern": "%.P"},
        "limit": 20
    },
    {
        "name": "OZN Product - All Options",
        "description": "Get all options for OZN product",
        "template": "by_symbol",
        "params": {"symbol_pattern": "XCME.OZN.%"},
        "limit": 50
    },
    {
        "name": "OZN Call Options",
        "description": "Get call options for OZN only",
        "template": "by_symbol",
        "params": {"symbol_pattern": "XCME.OZN.%.C"},
        "limit": 30
    },
    {
        "name": "OZN Put Options",
        "description": "Get put options for OZN only",
        "template": "by_symbol",
        "params": {"symbol_pattern": "XCME.OZN.%.P"},
        "limit": 30
    },
    {
        "name": "VY3 Product - All Options",
        "description": "Get all options for VY3 product",
        "template": "by_symbol",
        "params": {"symbol_pattern": "XCME.VY3.%"},
        "limit": 50
    },
    {
        "name": "VY3 Call Options",
        "description": "Get call options for VY3 only",
        "template": "by_symbol",
        "params": {"symbol_pattern": "XCME.VY3.%.C"},
        "limit": 30
    },
    {
        "name": "Specific Date - All Data",
        "description": "Get all data from July 21, 2025",
        "template": "by_date",
        "params": {"file_date": "2025-07-21"},
        "limit": 100
    },
    {
        "name": "OZN Calls on Specific Date",
        "description": "Get OZN call options from July 21, 2025",
        "template": "by_symbol_and_date",
        "params": {
            "symbol_pattern": "XCME.OZN.%.C",
            "file_date": "2025-07-21"
        }
    },
    {
        "name": "VY3 Puts on Specific Date",
        "description": "Get VY3 put options from July 21, 2025",
        "template": "by_symbol_and_date",
        "params": {
            "symbol_pattern": "XCME.VY3.%.P",
            "file_date": "2025-07-21"
        }
    },
    {
        "name": "August 2025 Expiry (AUG25)",
        "description": "Get options expiring in August 2025",
        "template": "by_symbol",
        "params": {"symbol_pattern": "%AUG25%"},
        "limit": 50
    },
    {
        "name": "July 2025 Expiry (JUL25)",
        "description": "Get options expiring in July 2025",
        "template": "by_symbol",
        "params": {"symbol_pattern": "%JUL25%"},
        "limit": 50
    },
    {
        "name": "Small Sample - 5 Rows",
        "description": "Get just 5 rows for quick testing",
        "template": "all_valid",
        "params": {},
        "limit": 5
    },
    {
        "name": "Larger Sample - 100 Rows",
        "description": "Get 100 rows for comprehensive testing",
        "template": "all_valid",
        "params": {},
        "limit": 100
    },
    {
        "name": "Specific Columns Only",
        "description": "Get only symbol, bid, and ask columns",
        "template": "by_symbol",
        "params": {"symbol_pattern": "%.C"},
        "columns": ["symbol", "bid", "ask"],
        "limit": 10
    },
  {
    "name": "All Call Options (sample)",
    "description": "Get recent call options (symbol suffix .C) — small sample.",
    "nl_query": "Show me the latest call options (symbols ending with .C), including bid, ask, theoretical, timestamp and order_qty — return 20 rows.",
    "template": "by_symbol",
    "params": {"symbol_pattern": "%.C"},
    "columns": ["symbol", "bid", "ask", "theoretical", "timestamp", "order_qty"],
    "limit": 20
  },
  {
    "name": "All Put Options (sample)",
    "description": "Get recent put options (symbol suffix .P).",
    "nl_query": "List recent put options (symbols ending with .P) with bid, ask, theoretical and timestamp, 20 rows.",
    "template": "by_symbol",
    "params": {"symbol_pattern": "%.P"},
    "limit": 20
  },
  {
    "name": "Product Options (pattern)",
    "description": "Get all options for a product (wildcard product prefix).",
    "nl_query": "Show all options for product XCME.OZN (both calls and puts) with full fields, limited to 50 rows.",
    "template": "by_symbol",
    "params": {"symbol_pattern": "XCME.OZN.%"},
    "limit": 50
  },
  {
    "name": "Calls for Product and Date",
    "description": "OZN calls on a specific file/date.",
    "nl_query": "Get OZN call options (XCME.OZN.*.C) for file_date 2025-07-21 including bid/ask/theoretical/order_qty.",
    "template": "by_symbol_and_date",
    "params": {"symbol_pattern": "XCME.OZN.%.C", "file_date": "2025-07-21"}
  },
  {
    "name": "Puts for Product and Date",
    "description": "VY3 puts on a specific date.",
    "nl_query": "Return VY3 put options (XCME.VY3.*.P) on 2025-07-21 with bid, ask, timestamp.",
    "template": "by_symbol_and_date",
    "params": {"symbol_pattern": "XCME.VY3.%.P", "file_date": "2025-07-21"}
  },
  {
    "name": "Expiry Month Filter",
    "description": "Find options that reference an expiry token (AUG25, JUL25, etc.).",
    "nl_query": "Show options that expire in AUG25 (symbols containing AUG25), with bid/ask/theoretical — up to 50 rows.",
    "template": "by_symbol",
    "params": {"symbol_pattern": "%AUG25%"},
    "limit": 50
  },
  {
    "name": "Top-of-book (latest timestamp)",
    "description": "Get the most recent tick per symbol (top-of-book).",
    "nl_query": "For each symbol, give the latest row (latest timestamp) with bid, ask, theoretical and order_qty. Return up to 200 symbols.",
    "template": "latest_per_symbol",
    "params": {},
    "limit": 200
  },
  {
    "name": "Time range for a symbol",
    "description": "All ticks for a symbol between two timestamps.",
    "nl_query": "Show all ticks for symbol XCME.OZN.2025AUG100.C between 2025-07-20T09:30:00 and 2025-07-21T16:00:00, include timestamp, bid, ask, order_qty.",
    "template": "by_symbol_and_timerange",
    "params": {
      "symbol": "XCME.OZN.2025AUG100.C",
      "start_ts": "2025-07-20T09:30:00",
      "end_ts": "2025-07-21T16:00:00"
    }
  },
  {
    "name": "Price range filter",
    "description": "Options where bid/ask fit a price window.",
    "nl_query": "Find options with bid >= 0.10 and ask <= 1.50, return symbol, bid, ask, timestamp. Limit 100.",
    "template": "by_price_range",
    "params": {"min_bid": 0.10, "max_ask": 1.50},
    "limit": 100
  },
  {
    "name": "Large orders",
    "description": "Find rows with large order quantities.",
    "nl_query": "Return rows where order_qty >= 1000, include symbol, order_qty, bid, ask, timestamp — sort by order_qty descending.",
    "template": "by_qty_threshold",
    "params": {"min_qty": 1000},
    "limit": 200
  },
  {
    "name": "Widest bid-ask spreads",
    "description": "Top N rows with largest bid-ask spread.",
    "nl_query": "Show the 20 ticks with the largest bid-ask spread (spread = ask - bid), include spread, symbol, bid, ask, timestamp.",
    "template": "top_n_spread_widest",
    "params": {"n": 20}
  },
  {
    "name": "Narrowest bid-ask spreads",
    "description": "Top N rows with smallest spread (tightest market).",
    "nl_query": "List the 20 ticks with the narrowest bid-ask spreads, include spread, symbol, bid, ask, timestamp.",
    "template": "top_n_spread_narrowest",
    "params": {"n": 20}
  },
  {
    "name": "Missing theoretical prices",
    "description": "Rows where theoretical price is NULL or zero.",
    "nl_query": "Find rows where theoretical is NULL or = 0, return symbol, bid, ask, theoretical, timestamp — limit 200.",
    "template": "missing_theoretical",
    "params": {}
  },
  {
    "name": "Implied volatility candidates",
    "description": "Symbols where theoretical differs materially from mid-price (candidate for implied vol recalculation).",
    "nl_query": "Show options where |theoretical - mid| > 0.05 (mid = (bid+ask)/2), include symbol, bid, ask, theoretical, mid, difference, timestamp. Limit 100.",
    "template": "theoretical_vs_mid",
    "params": {"min_diff": 0.05},
    "limit": 100
  },
  {
    "name": "Aggregated volumes per symbol (day)",
    "description": "Daily aggregate of order_qty and number of ticks per symbol.",
    "nl_query": "For 2025-07-21, give daily aggregates per symbol: sum(order_qty), count(ticks), avg(bid), avg(ask). Return top 50 by volume.",
    "template": "aggregate_by_symbol_and_date",
    "params": {"file_date": "2025-07-21"},
    "limit": 50
  },
  {
    "name": "Intraday VWAP-like (volume-weighted avg price)",
    "description": "Compute VWAP-like metric using order_qty * mid-price / sum(order_qty) for a symbol & date.",
    "nl_query": "Compute VWAP for symbol XCME.VY3.2025JUL100.C on 2025-07-21 using order_qty-weighted mid-price, and return the VWAP and total volume.",
    "template": "vwap_by_symbol_and_date",
    "params": {"symbol": "XCME.VY3.2025JUL100.C", "file_date": "2025-07-21"}
  },
  {
    "name": "Latest snapshot for a product",
    "description": "Top-of-book for all symbols under a product.",
    "nl_query": "Give the latest tick per symbol for all XCME.OZN.* (top-of-book), include symbol, bid, ask, theoretical, timestamp. Limit 500.",
    "template": "latest_for_symbol_pattern",
    "params": {"symbol_pattern": "XCME.OZN.%"},
    "limit": 500
  },
  {
    "name": "Historical time series (OHLC-ish)",
    "description": "Create a simple time series (open/high/low/close) of mid price per day for a symbol.",
    "nl_query": "Build a daily OHLC of mid-price for symbol XCME.OZN.2025AUG100.C between 2025-06-01 and 2025-07-31.",
    "template": "daily_ohlc_mid",
    "params": {"symbol": "XCME.OZN.2025AUG100.C", "start_date": "2025-06-01", "end_date": "2025-07-31"}
  },
  {
    "name": "Compare two symbols at a timestamp",
    "description": "Side-by-side snapshot of two symbols at (or nearest before) a timestamp.",
    "nl_query": "At 2025-07-21T10:15:00, show bid/ask/theoretical for symbols XCME.OZN.2025AUG100.C and XCME.VY3.2025AUG100.C (closest tick at or before that time).",
    "template": "snapshot_two_symbols_at_ts",
    "params": {"symbol_a": "XCME.OZN.2025AUG100.C", "symbol_b": "XCME.VY3.2025AUG100.C", "ts": "2025-07-21T10:15:00"}
  },
  {
    "name": "Cross-product spread candidates",
    "description": "Find potential calendar or product spreads (same expiry/strike across products) where spreads are large.",
    "nl_query": "Find symbol pairs across XCME.OZN and XCME.VY3 with same expiry+strike where the mid-price difference > 0.25. Return the symbol pair, mids and difference.",
    "template": "cross_product_spread_candidates",
    "params": {"product_a": "XCME.OZN", "product_b": "XCME.VY3", "min_mid_diff": 0.25},
    "limit": 200
  },
  {
    "name": "Quick health check - sample rows",
    "description": "Return a tiny sample for schema/field checks.",
    "nl_query": "Give 5 random valid rows (any symbols) including symbol, bid, ask, theoretical, timestamp, order_qty for a quick sanity check.",
    "template": "all_valid",
    "params": {},
    "limit": 5
  },
  {
    "name": "Specific columns only",
    "description": "Return just symbol, bid and ask for quick screen.",
    "nl_query": "Show 10 rows of symbol, bid and ask only for symbols ending with .C.",
    "template": "by_symbol",
    "params": {"symbol_pattern": "%.C"},
    "columns": ["symbol", "bid", "ask"],
    "limit": 10
  },
  {
    "name": "Rows with out-of-order timestamps",
    "description": "Find symbols where timestamps go backwards (possible data quality issue).",
    "nl_query": "Detect symbols that have out-of-order timestamps (a later row with an earlier timestamp) — list symbols and the offending rows (limit 200).",
    "template": "anomalous_timestamp_order",
    "params": {},
    "limit": 200
  }






    
]


def run_query(agent, query_config, query_num):
    """Run a single query."""
    print(f"\n{'='*80}")
    print(f"Query {query_num}: {query_config['name']}")
    print(f"{'='*80}")
    print(f"Description: {query_config['description']}")
    print(f"Template: {query_config['template']}")
    print(f"Params: {json.dumps(query_config['params'], indent=2)}")
    if 'columns' in query_config:
        print(f"Columns: {query_config['columns']}")
    if 'limit' in query_config:
        print(f"Limit: {query_config['limit']}")
    
    try:
        # Run query
        output_path = agent.run(
            template=query_config['template'],
            params=query_config.get('params'),
            columns=query_config.get('columns'),
            limit=query_config.get('limit')
        )
        
        # Read output
        output_data = read_json(output_path)
        metadata = output_data['metadata']
        
        print(f"\n[SUCCESS]")
        print(f"  Output file: {output_path.name}")
        print(f"  Rows returned: {metadata['row_count']}")
        print(f"  SQL executed: {metadata['query']}")
        
        # Show sample data
        if output_data['data']:
            print(f"\n  Sample (first row):")
            first_row = output_data['data'][0]
            for key, value in first_row.items():
                print(f"    {key}: {value}")
        
        return True, output_path
        
    except Exception as e:
        print(f"\n[FAILED]")
        print(f"  Error: {e}")
        return False, None


def main():
    """Run all test queries."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Test queries on market_data.db")
    parser.add_argument("--query", type=int, help="Run specific query number (1-based)")
    parser.add_argument("--list", action="store_true", help="List available queries")
    parser.add_argument("--consumer", action="store_true", help="Run consumer on outputs")
    args = parser.parse_args()
    
    # List queries
    if args.list:
        print("="*80)
        print("AVAILABLE TEST QUERIES")
        print("="*80)
        for i, query in enumerate(TEST_QUERIES, 1):
            print(f"\n{i}. {query['name']}")
            print(f"   {query['description']}")
            print(f"   Template: {query['template']}")
            if 'params' in query:
                print(f"   Params: {query['params']}")
        print(f"\nTotal: {len(TEST_QUERIES)} queries")
        return 0
    
    print("="*80)
    print("MARKET DATA QUERIES - TESTING ON market_data.db")
    print("="*80)
    
    # Initialize agent
    agent = MarketDataAgent()
    
    # Run specific query or all
    if args.query:
        query_num = args.query
        if query_num < 1 or query_num > len(TEST_QUERIES):
            print(f"Error: Query number must be between 1 and {len(TEST_QUERIES)}")
            return 1
        
        queries_to_run = [TEST_QUERIES[query_num - 1]]
        start_num = query_num
    else:
        queries_to_run = TEST_QUERIES
        start_num = 1
    
    # Run queries
    results = []
    output_paths = []
    
    for i, query_config in enumerate(queries_to_run, start_num):
        success, output_path = run_query(agent, query_config, i)
        results.append(success)
        if output_path:
            output_paths.append(output_path)
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    
    total = len(results)
    succeeded = sum(results)
    failed = total - succeeded
    
    print(f"\nTotal queries: {total}")
    print(f"Succeeded: {succeeded}")
    print(f"Failed: {failed}")
    
    # Agent stats
    stats = agent.get_stats()
    print(f"\nAgent Statistics:")
    print(f"  Total runs: {stats['total_runs']}")
    print(f"  Next output ID: {stats['next_id']}")
    
    print(f"\nGenerated Files:")
    print(f"  Outputs: workspace/agents/market-data-agent/out/")
    print(f"  Logs: workspace/agents/market-data-agent/logs/")
    
    # Run consumer if requested
    if args.consumer and output_paths:
        print(f"\n{'='*80}")
        print("RUNNING CONSUMER ON ALL OUTPUTS")
        print(f"{'='*80}")
        
        consumer = ConsumerAgent()
        
        for output_path in output_paths:
            print(f"\nProcessing: {output_path.name}")
            try:
                consumer_output = consumer.run(output_path)
                
                # Show statistics
                consumer_data = read_json(consumer_output)
                stats = consumer_data['data'][0]['statistics']
                
                print(f"  [OK] Output: {consumer_output.name}")
                print(f"  Statistics:")
                print(f"    Total records: {stats['total_records']}")
                if 'bid_avg' in stats:
                    print(f"    Avg bid: {stats['bid_avg']:.6f}")
                if 'ask_avg' in stats:
                    print(f"    Avg ask: {stats['ask_avg']:.6f}")
                
            except Exception as e:
                print(f"  [FAILED] {e}")
        
        consumer_stats = consumer.get_stats()
        print(f"\nConsumer Statistics:")
        print(f"  Total runs: {consumer_stats['total_runs']}")
        print(f"  Outputs: workspace/agents/consumer-agent/out/")
    
    print(f"\n{'='*80}")
    
    return 0 if all(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

