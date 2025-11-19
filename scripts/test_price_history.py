"""CLI script to test Polymarket historical price fetching for a query.

Flow:
  1. Uses the simple Polymarket agent to find markets for your query.
  2. Derives token IDs using the same schema helper as the agent.
  3. Calls get_market_price_history for a past date and for today.

Usage (from project root):
    python scripts/test_price_history.py --query "bitcoin predictions" --past-date 2024-11-01
"""

import sys
import json
import argparse
from datetime import date as _date
from pathlib import Path


# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.mcp import MCPClient  # noqa: E402
from src.core.logging_config import setup_logging  # noqa: E402
from src.agents.polymarket_agent.run import PolymarketAgent  # noqa: E402
from src.servers.polymarket.schema import (  # noqa: E402
    get_token_id_for_price_history,
)


# Defaults for running via IDE "Run/Debug" without CLI args
DEFAULT_QUERY = "bitcoin predictions"
DEFAULT_PAST_DATE = "2024-11-01"
DEFAULT_TODAY_DATE = None  # Use today's date if None


def main() -> None:
    """Run query, then fetch historical prices for top markets."""
    setup_logging()

    parser = argparse.ArgumentParser(
        description=(
            "Test Polymarket price history: search markets for a query, then "
            "fetch historical prices for a past date and for today"
        )
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help=(
            "Natural language query, e.g. 'bitcoin predictions'. "
            f"Default when not provided: {DEFAULT_QUERY!r}"
        ),
    )
    parser.add_argument(
        "--past-date",
        default=DEFAULT_PAST_DATE,
        help=(
            "Past date in YYYY-MM-DD format to fetch history for. "
            f"Default when not provided: {DEFAULT_PAST_DATE!r}"
        ),
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=3,
        help="Maximum number of markets to test (default: 3)",
    )
    parser.add_argument(
        "--today-date",
        default=DEFAULT_TODAY_DATE,
        help=(
            "Override 'today' date in YYYY-MM-DD format (optional). "
            "If omitted, uses the current system date."
        ),
    )

    args = parser.parse_args()

    # Step 1: run simple Polymarket search
    agent = PolymarketAgent()
    output_path = agent.run_simple(query=args.query, limit=args.max_results)

    with open(output_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    result_data = payload["data"][0]
    result_payload = result_data.get("result", {})
    markets = result_payload.get("markets", [])

    print("\n=== Simple Polymarket search ===\n")
    print(f"Query: {result_data.get('query')}")
    print(f"Markets returned: {len(markets)}")

    if not markets:
        print("No markets found for this query; cannot test price history.")
        return

    client = MCPClient()
    today_str = args.today_date or _date.today().isoformat()

    print("\n=== Historical prices ===\n")

    for i, market in enumerate(markets[: args.max_results], 1):
        title = market.get("title", "No title")
        url = market.get("url", "N/A")
        token_id = get_token_id_for_price_history(market)

        print(f"\n[{i}] {title}")
        print(f"    URL: {url}")
        print(f"    Token ID for price history: {token_id}")

        if not token_id:
            print("    Skipping: no token ID available for price history.")
            continue

        for label, target_date in (
            ("past", args.past_date),
            ("today", today_str),
        ):
            try:
                history = client.call_tool(
                    "get_market_price_history",
                    {
                        "market_id": token_id,
                        "date": target_date,
                    },
                )
                price = history.get("price")
                data_points = history.get("data_points")
                note = history.get("note")

                print(f"    {label} date {target_date}:")
                print(f"        price: {price}")
                print(f"        data_points: {data_points}")
                print(f"        note: {note}")
            except Exception as exc:  # pragma: no cover - CLI convenience
                print(f"    {label} date {target_date}: ERROR fetching history: {exc}")


if __name__ == "__main__":
    main()