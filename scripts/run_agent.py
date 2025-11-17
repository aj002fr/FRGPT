#!/usr/bin/env python3
"""
Run single agent with CLI arguments.

Usage:
    python scripts/run_agent.py producer --template by_symbol --params '{"symbol_pattern": "%.C"}'
    python scripts/run_agent.py consumer --input workspace/agents/market-data-agent/out/000001.json
"""

import sys
import json
import argparse
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.market_data_agent import MarketDataAgent
from src.agents.consumer_agent import ConsumerAgent


def run_producer(args):
    """Run producer agent."""
    print(f"Running Market Data Agent (Producer)")
    print(f"  Template: {args.template}")
    print(f"  Params: {args.params}")
    
    agent = MarketDataAgent()
    
    # Parse params
    params = json.loads(args.params) if args.params else None
    columns = json.loads(args.columns) if args.columns else None
    
    # Run
    output_path = agent.run(
        template=args.template,
        params=params,
        columns=columns,
        limit=args.limit
    )
    
    print(f"\n✓ Success!")
    print(f"  Output: {output_path}")
    print(f"  Stats: {agent.get_stats()}")
    
    return 0


def run_consumer(args):
    """Run consumer agent."""
    print(f"Running Consumer Agent")
    print(f"  Input: {args.input}")
    
    agent = ConsumerAgent()
    
    # Run
    output_path = agent.run(Path(args.input))
    
    print(f"\n✓ Success!")
    print(f"  Output: {output_path}")
    print(f"  Stats: {agent.get_stats()}")
    
    return 0


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Run market data agents")
    subparsers = parser.add_subparsers(dest="agent", help="Agent to run")
    
    # Producer subcommand
    producer_parser = subparsers.add_parser("producer", help="Run producer agent")
    producer_parser.add_argument("--template", default="all_valid", help="Query template")
    producer_parser.add_argument("--params", help="Query params as JSON")
    producer_parser.add_argument("--columns", help="Columns as JSON list")
    producer_parser.add_argument("--limit", type=int, help="Row limit")
    
    # Consumer subcommand
    consumer_parser = subparsers.add_parser("consumer", help="Run consumer agent")
    consumer_parser.add_argument("--input", required=True, help="Input file path")
    
    args = parser.parse_args()
    
    if not args.agent:
        parser.print_help()
        return 1
    
    try:
        if args.agent == "producer":
            return run_producer(args)
        elif args.agent == "consumer":
            return run_consumer(args)
    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())


