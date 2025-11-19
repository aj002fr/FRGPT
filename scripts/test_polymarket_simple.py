"""Simple Polymarket Agent test script (API-only, no reasoning).

Usage:
    python scripts/test_polymarket_simple.py --custom "fed decision in december?"
"""

import sys
import argparse
import logging
from pathlib import Path


# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.polymarket_agent.run import PolymarketAgent  # noqa: E402
from src.core.logging_config import setup_logging  # noqa: E402


# Defaults for running via IDE "Run/Debug" without CLI args
DEFAULT_CUSTOM_QUERY = "bitcoin predictions 2025-11-01 2025-11-18"
DEFAULT_MAX_RESULTS = 10


def run_custom_query(query: str, max_results: int = 10) -> None:
    """Run a custom query using the simple API-only Polymarket agent."""
    print("\n>> Running Simple Polymarket Query (API-only):")
    print(f"   {query}")
    print(f"   Max results: {max_results}\n")

    logger = logging.getLogger(__name__)

    try:
        agent = PolymarketAgent()
        output_path = agent.run_simple(query=query, limit=max_results)

        print("[SUCCESS] Simple query completed successfully!")
        print(f"   Output: {output_path}\n")

        # Read and display results
        import json

        with open(output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        result_data = data["data"][0]
        result_payload = result_data.get("result", {})

        markets = result_payload.get("markets", [])

        print("\n[RESULTS]")
        print(f"   Query: {result_data.get('query')}")
        print(f"   Markets Found (simple API-only): {len(markets)}\n")

        if not markets:
            print("   ⚠️  No high-volume relevant markets found for this query.")
            print("   This may mean:")
            print("   - No active markets match the query keywords")
            print("   - All relevant markets are low volume or invalid")
            print("   - Try a different query or check Polymarket directly\n")
            return

        print("\n[TOP MARKETS BY VOLUME]")
        for i, market in enumerate(markets[:5], 1):
            title = market.get("title", "No title")
            url = market.get("url", "N/A")
            volume = market.get("volume", 0)
            prices = market.get("prices", {})

            current_date = market.get("current_date")
            current_price = market.get("current_price", prices)
            historical_date = market.get("historical_date")
            historical_price = market.get("historical_price")

            print(f"\n   {i}. {title}")
            print(f"      URL: {url}")
            print(f"      Volume: ${volume:,.0f}")

            # Current prices (today)
            if isinstance(current_price, dict) and current_price:
                pretty_current = ", ".join(
                    f"{k}: {float(v):.2%}"
                    for k, v in current_price.items()
                    if v is not None
                )
                if current_date:
                    print(f"      Current ({current_date}): {pretty_current}")
                else:
                    print(f"      Current prices: {pretty_current}")

            # Historical prices (past date, if available)
            if isinstance(historical_price, dict) and any(
                v is not None for v in historical_price.values()
            ):
                pretty_hist = ", ".join(
                    f"{k}: {float(v):.2%}"
                    for k, v in historical_price.items()
                    if v is not None
                )
                if historical_date:
                    print(f"      Past ({historical_date}): {pretty_hist}")
                else:
                    print(f"      Past prices: {pretty_hist}")

    except Exception as exc:  # pragma: no cover - CLI convenience
        print(f"❌ Error running simple custom query: {exc}")
        logger.exception("Simple custom query failed")


def main() -> None:
    """Main CLI entry point for simple Polymarket agent."""
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Simple Polymarket Agent (API-only, no reasoning)"
    )

    parser.add_argument(
        "--custom",
        type=str,
        metavar="QUERY",
        default=DEFAULT_CUSTOM_QUERY,
        help=(
            "Run custom query. "
            f"Default when not provided: {DEFAULT_CUSTOM_QUERY!r}"
        ),
    )

    parser.add_argument(
        "--max-results",
        type=int,
        default=DEFAULT_MAX_RESULTS,
        metavar="N",
        help=(
            "Maximum results to return "
            f"(default: {DEFAULT_MAX_RESULTS})"
        ),
    )

    args = parser.parse_args()

    run_custom_query(args.custom, args.max_results)
    


if __name__ == "__main__":
    main()


