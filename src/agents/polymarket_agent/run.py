"""
Polymarket Agent 

1. Parses natural language queries to extract topic and optional date
2. Searches Polymarket markets for the topic
3. Always returns current market state
4. Adds historical comparison (specified date OR past week)
5. Sorts by relevance then volume
6. Flags low volume markets
"""

import json
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List

from src.mcp.client import MCPClient
from src.bus.manifest import Manifest
from src.bus.file_bus import write_atomic, ensure_dir
from src.bus.schema import create_output_template
from src.servers.polymarket.schema import MAX_POLYMARKET_RESULTS
from src.servers.polymarket.search_markets import (
    call_polymarket_api,
    call_polymarket_search_api,
    parse_polymarket_response,
    parse_public_search_response,
)

from .config import (
    AGENT_NAME,
    AGENT_VERSION,
    get_workspace_path,
    OUT_DIR,
    LOGS_DIR,
    LOW_VOLUME_THRESHOLD,
    DEFAULT_LOOKBACK_DAYS,
    MAX_MARKETS_TO_RETURN,
    MAX_QUERY_LENGTH,
)

logger = logging.getLogger(__name__)


class PolymarketAgent:
    """

    1. Always show current market state
    2. Add historical comparison:
       - If date mentioned → compare with that date
       - If no date → compare with past week
    3. Sort by relevance first, then volume
    4. Flag low volume markets
    """

    def __init__(self) -> None:
        """Initialize the polymarket agent."""
        # Get workspace path
        self.workspace = get_workspace_path()

        # Initialize manifest
        self.manifest = Manifest(self.workspace)

        # Initialize MCP client
        self.client = MCPClient()

        logger.info(f"{AGENT_NAME} v{AGENT_VERSION} initialized at {self.workspace}")


    def parse_query_with_gpt(self, query: str) -> Dict[str, Any]:
        """
        Use GPT-4 to extract topic and optional date from query.

        Args:
            query: User's natural language query

        Returns:
            {
                "topic": "extracted market topic",
                "date": "YYYY-MM-DD or null",
                "confidence": 0.0-1.0
            }
        """
        import os

        try:
            # Load API key from config/keys.env or environment
            config_dir = Path(__file__).parent.parent.parent.parent / "config"
            env_file = config_dir / "keys.env"

            api_key = os.environ.get("OPENAI_API_KEY")

            # Try loading from file if not in environment
            if not api_key and env_file.exists():
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("OPENAI_API_KEY="):
                            api_key = line.split("=", 1)[1].strip()
                            break

            if not api_key:
                logger.warning(
                    "OPENAI_API_KEY not found, falling back to rule-based parsing"
                )
                return self._fallback_parse(query)

            # Import OpenAI
            try:
                from openai import OpenAI
            except ImportError:
                logger.warning(
                    "OpenAI library not installed, falling back to rule-based parsing"
                )
                return self._fallback_parse(query)

            client = OpenAI(api_key=api_key)

            # Simplified prompt - just extract topic and optional date
            today = datetime.now().strftime("%Y-%m-%d")
            prompt = f"""
You are a query parser for a prediction markets system. Extract the topic and any date mentioned.

Query: "{query}"

Extract:
1. TOPIC - The main subject (e.g., "Bitcoin price", "federal shutdown", "AI regulation")
2. DATE - Any specific past date mentioned:
   - Parse natural language: "Nov 1", "November 1st", "January 1 2025", "last week"
   - Convert to YYYY-MM-DD format
   - Return null if no specific date mentioned
   - Today is {today}

Return ONLY a JSON object:
{{
    "topic": "extracted topic",
    "date": "YYYY-MM-DD or null",
    "confidence": 0.95
}}

Examples:
1. Query: "Bitcoin price predictions"
   {{"topic": "bitcoin price", "date": null, "confidence": 0.98}}

2. Query: "What was the opinion on Jan 1 2025 about Bitcoin?"
   {{"topic": "bitcoin", "date": "2025-01-01", "confidence": 0.95}}

3. Query: "AI regulation markets"
   {{"topic": "AI regulation", "date": null, "confidence": 0.97}}

4. Query: "Federal shutdown on November 5th"
   {{"topic": "federal shutdown", "date": "2024-11-05", "confidence": 0.90}}

Now parse the query above and return ONLY the JSON.
""".strip()

            logger.info("Calling GPT-4 to parse query")
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=150,
            )

            # Extract and parse JSON response
            content = response.choices[0].message.content.strip()

            # Remove markdown code blocks if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

            parsed = json.loads(content)
            logger.info(
                "Parsed - topic: '%s', date: %s, confidence: %s",
                parsed["topic"],
                parsed.get("date"),
                parsed.get("confidence", "N/A"),
            )

            return parsed

        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.error("GPT-4 parsing failed: %s. Falling back to rule-based parsing", exc)
            return self._fallback_parse(query)

    def _fallback_parse(self, query: str) -> Dict[str, Any]:
        """
        Simple rule-based parsing as fallback.

        Args:
            query: User query

        Returns:
            Parsed query dict
        """
        # Trim overly long queries to protect downstream processing
        safe_query = query[:MAX_QUERY_LENGTH]
        return {
            "topic": safe_query,
            "date": None,
            "confidence": 0.5,
        }

    def _extract_inline_dates(
        self, query: str
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Lightweight date extraction for simple mode (no reasoning/LLM).

        Looks for ISO-like dates (YYYY-MM-DD) in the raw query text and, if two or
        more are present, treats the earliest as the comparison (past) date and the
        latest as "today"/current comparison anchor.

        Args:
            query: Full query string (may contain topic + dates).

        Returns:
            (past_date, today_date) where each element is a YYYY-MM-DD string or None.
        """
        import re

        # Find all YYYY-MM-DD substrings
        matches = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", query)
        if not matches:
            return None, None

        # Deduplicate & sort chronologically
        unique_dates = sorted(set(matches))

        if len(unique_dates) == 1:
            # Single date: treat as past date; today will be "now" implicitly
            return unique_dates[0], None

        # At least two dates: earliest = past, latest = "today" anchor
        past_date = unique_dates[0]
        today_date = unique_dates[-1]

        logger.info(
            "Inline dates detected in simple query: past_date=%s, today_date=%s",
            past_date,
            today_date,
        )
        return past_date, today_date

    # ------------------------
    # Main Run Entry Points
    # ------------------------
    def run(
        self,
        query: str,
        session_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Path:
        """
        Unified Polymarket agent entry point.

        For all callers (scripts, orchestrator, tests), this now delegates to the
        simple, API-only implementation that:
        - Uses the Gamma `/markets` endpoint sorted by volume
        - Filters by high volume and textual relevance
        - Performs basic price validation
        """
        # session_id is accepted for backwards compatibility but ignored in simple mode.
        return self.run_simple(query=query, limit=limit)

    def run_simple(
        self,
        query: str,
        limit: Optional[int] = None,
    ) -> Path:
        """
        Simple Polymarket search with no LLM reasoning.

        - Uses Polymarket Gamma `/markets` API (sorted by volume)
        - Filters by keyword relevance and high volume
        - Validation focuses on:
          1. Prices are numeric in [0, 1]
          2. Volume is non-negative and above threshold
          3. Market text (title/description/question) is keyword-relevant to query

        Intended for custom, ad-hoc queries only (no reasoning).
        """
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")

        # Generate run ID and session ID (for logging + manifest)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        session_id = self._generate_session_id(query, run_id)

        logger.info("Simple run %s: Starting with query='%s'", run_id, query)
        logger.info("Session ID: %s", session_id)

        try:
            search_limit = limit or MAX_MARKETS_TO_RETURN

            # Step 1: Fetch markets from Polymarket Gamma /public-search API
            # This endpoint already filters by the user's query string and returns
            # price + volume + clobTokenIds for all relevant markets.
            logger.info(
                "Simple Step 1: Fetching markets from Polymarket public-search API"
            )
            api_response = call_polymarket_search_api(query, limit_per_type=MAX_POLYMARKET_RESULTS)
            all_markets = parse_public_search_response(api_response, query)

            logger.info(
                "Simple search: %d markets from /public-search for query='%s'",
                len(all_markets),
                query,
            )

            if not all_markets:
                result = {
                    "query": query,
                    "parsed": None,
                    "comparison_date": None,
                    "date_source": None,
                    "markets": [],
                    "metadata": {
                        "total_markets": 0,
                        "low_volume_count": 0,
                        "search_method": "markets_volume_api_only",
                        "note": "No active markets returned from Polymarket /markets API",
                    },
                }
            else:
                # Step 2: Validate prices + volume + strict textual relevance
                logger.info("Simple Step 2: Validating prices and volume")
                validated_markets: List[Dict[str, Any]] = []

                # Basic relevance check: require at least one query keyword to appear
                # in title/description/question (case-insensitive).
                # Prefer phrase (pairwise) matches first, then fall back to single-word.
                import re

                # Build keyword list from query
                # cleaned_query = re.sub(r"[?!.,;:]", "", query.lower())
                # raw_words = [
                #     w
                #     for w in re.split(r"[\s\-]+", cleaned_query)
                #     if w and len(w) > 2
                # ]

                # # Filter out standalone year tokens (e.g. "2024", "2025") so they
                # # don't dominate relevance and match unrelated same-year markets.
                # filtered_words: list[str] = []
                # for w in raw_words:
                #     if re.fullmatch(r"\d{4}", w):
                #         try:
                #             year = int(w)
                #         except ValueError:
                #             year = None
                #         if year is not None and 1900 <= year <= 2100:
                #             # Skip pure year tokens
                #             continue
                #     filtered_words.append(w)

                # # Use filtered words when available; otherwise fall back to raw words.
                # keyword_words = filtered_words or raw_words
                # query_words_set = set(keyword_words)

                # # Build pairwise phrases (adjacent word bigrams) to check first.
                # phrases: list[str] = []
                # if len(keyword_words) >= 2:
                #     for i in range(len(keyword_words) - 1):
                #         phrase = f"{keyword_words[i]} {keyword_words[i+1]}"
                #         if phrase not in phrases:
                #             phrases.append(phrase)

                for market in all_markets:
                    prices = market.get("prices", {})
                    if not prices or not isinstance(prices, dict):
                        logger.warning(
                            "Skipping market with invalid prices: %s",
                            market.get("title", "N/A")[:60],
                        )
                        continue

                    prices_ok = True
                    for outcome, value in prices.items():
                        try:
                            v = float(value)
                        except (TypeError, ValueError):
                            prices_ok = False
                            break
                        if v < 0.0 or v > 1.0:
                            prices_ok = False
                            break
                    if not prices_ok:
                        logger.warning(
                            "Skipping market with out-of-range prices: %s",
                            market.get("title", "N/A")[:60],
                        )
                        continue

                    # Volume: require non-negative and above threshold
                    volume = market.get("volume", 0)
                    try:
                        volume_val = float(volume)
                    except (TypeError, ValueError):
                        logger.warning(
                            "Skipping market with non-numeric volume: %s (volume=%s)",
                            market.get("title", "N/A")[:60],
                            volume,
                        )
                        continue
                    if volume_val < 0:
                        logger.warning(
                            "Skipping market with negative volume: %s (volume=%s)",
                            market.get("title", "N/A")[:60],
                            volume,
                        )
                        continue
                    if volume_val < LOW_VOLUME_THRESHOLD:
                        logger.info(
                            "Skipping low-volume market: '%s' ($%s < $%s)",
                            market.get("title", "N/A")[:60],
                            f"{volume_val:,.0f}",
                            f"{LOW_VOLUME_THRESHOLD:,.0f}",
                        )
                        continue


                    validated_markets.append(market)

                logger.info(
                    "Simple search: %d markets after price and volume filters",
                    len(validated_markets),
                )

                # Step 3: Sort by volume (descending) and apply limit
                logger.info("Simple Step 3: Sorting markets by volume (descending)")
                validated_markets.sort(
                    key=lambda m: m.get("volume", 0), reverse=True
                )
                final_markets = validated_markets[:search_limit]

                # Optional: add historical comparison if dates are embedded in query.
                past_date, today_date = self._extract_inline_dates(query)
                comparison_date = past_date
                date_source: Optional[str] = None

                if comparison_date:
                    logger.info(
                        "Simple mode: adding historical comparison for %s "
                        "(parsed from inline query dates)",
                        comparison_date,
                    )
                    final_markets = self._add_historical_comparison(
                        final_markets, comparison_date
                    )
                    date_source = "inline_query_dates"

                result = {
                    "query": query,
                    "parsed": None,
                    "comparison_date": comparison_date,
                    "date_source": date_source,
                    "markets": final_markets,
                    "metadata": {
                        "total_markets": len(final_markets),
                        "low_volume_count": 0,
                        "search_method": "markets_volume_api_only",
                    },
                }

            # Step 5: Write output to file bus (reuses standard schema)
            output_path = self._write_output(result)

            # Step 6: Log run (no parsed/date info for simple mode)
            self._log_run(
                run_id,
                query,
                session_id,
                parsed={},
                comparison_date=None,
                output_path=output_path,
                status="success",
            )

            logger.info(
                "Simple run %s: Completed successfully. Output: %s",
                run_id,
                output_path,
            )
            return output_path

        except Exception as exc:
            logger.error("Simple run %s: Failed with error: %s", run_id, exc)
            self._log_run(
                run_id,
                query,
                session_id,
                parsed={},
                comparison_date=None,
                output_path=None,
                status="error",
                error=str(exc),
            )
            raise

    # ------------------------
    # Historical Comparison
    # ------------------------
    def _add_historical_comparison(
        self, markets: List[Dict[str, Any]], comparison_date: str
    ) -> List[Dict[str, Any]]:
        """
        Add historical prices to each market for comparison.

        Args:
            markets: List of market dicts
            comparison_date: Date to compare against (YYYY-MM-DD)

        Returns:
            Markets with historical price data added
        """
        from datetime import datetime, timezone
        from src.servers.polymarket.schema import (
            market_exists_on_date,
            get_token_id_for_price_history,
            validate_market_url,
        )

        for market in markets:
            # Stamp "today" date and current price snapshot for clarity
            now_utc = datetime.now(timezone.utc)
            market["current_date"] = now_utc.date().isoformat()
            market["current_price"] = market.get("prices", {})

            # Validate URL
            url = market.get("url")
            url_valid = validate_market_url(url) if url else False
            market["url_valid"] = url_valid

            if not url_valid:
                logger.warning("Market URL not accessible: %s", url)
                market["url_note"] = "URL may be incorrect or market removed"

            # Check if market existed on comparison date
            created_at = market.get("created_at")
            existed_on_date = market_exists_on_date(created_at, comparison_date)

            if not existed_on_date:
                logger.info(
                    "Market '%s' was created after %s",
                    market.get("title", "N/A")[:50],
                    comparison_date,
                )
                market["historical_price"] = {"yes": None, "no": None}
                market["historical_date"] = comparison_date
                market["historical_note"] = (
                    "Market created after "
                    f"{comparison_date}. Creation: {created_at[:10] if created_at else 'Unknown'}"
                )
                market["price_change"] = None
                continue

            # Get token ID
            token_id = get_token_id_for_price_history(market)

            if not token_id:
                logger.warning(
                    "No token ID for: %s", market.get("title", "N/A")[:50]
                )
                market["historical_price"] = {"yes": None, "no": None}
                market["historical_note"] = "Token ID not available"
                market["price_change"] = None
                continue

            # Fetch historical price
            try:
                history = self.client.call_tool(
                    "get_market_price_history",
                    {
                        "market_id": token_id,
                        "date": comparison_date,
                    },
                )

                historical_price = history.get("price")
                market["historical_price"] = historical_price
                market["historical_date"] = comparison_date
                market["historical_note"] = history.get("note")

                # Calculate price change
                current_yes = market.get("prices", {}).get("Yes", 0)
                historical_yes = historical_price.get("yes") if historical_price else None

                if historical_yes is not None and current_yes is not None:
                    change = (current_yes - historical_yes) * 100  # percentage points
                    market["price_change"] = {
                        "yes_change": round(change, 2),
                        "direction": "up"
                        if change > 0
                        else ("down" if change < 0 else "unchanged"),
                    }
                    logger.info(
                        "Price change for '%s': %+0.2fpp",
                        market.get("title", "N/A")[:40],
                        change,
                    )
                else:
                    market["price_change"] = None

            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.warning(
                    "Failed to get historical price for %s: %s", token_id, exc
                )
                market["historical_price"] = {"yes": None, "no": None}
                market["historical_note"] = (
                    f"Historical data unavailable: {str(exc)}"
                )
                market["price_change"] = None

        return markets

    # ------------------------
    # Sorting & Volume Flags
    # ------------------------
    def _sort_markets(self, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sort markets by relevance score first, then by volume.

        Args:
            markets: List of market dicts

        Returns:
            Sorted markets
        """

        def sort_key(market: Dict[str, Any]) -> Any:
            # Primary: relevance score (higher is better)
            # Secondary: volume (higher is better)
            relevance = market.get("relevance_score", 0)
            volume = market.get("volume", 0)
            return (-relevance, -volume)

        sorted_markets = sorted(markets, key=sort_key)

        logger.info("Sorted %d markets by relevance and volume", len(sorted_markets))
        return sorted_markets

    def _flag_low_volume(self, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Flag markets with volume below threshold.

        Args:
            markets: List of market dicts

        Returns:
            Markets with low_volume_flag added
        """
        for market in markets:
            volume = market.get("volume", 0)

            if volume < LOW_VOLUME_THRESHOLD:
                market["low_volume_flag"] = True
                market["volume_note"] = (
                    f"⚠️ Low volume (${volume:,.0f} < ${LOW_VOLUME_THRESHOLD:,.0f})"
                )
                logger.info(
                    "Flagged low volume: '%s' ($%s)",
                    market.get("title", "N/A")[:40],
                    f"{volume:,.0f}",
                )
            else:
                market["low_volume_flag"] = False
                market["volume_note"] = f"Volume: ${volume:,.0f}"

        return markets

    # ------------------------
    # Output & Logging
    # ------------------------
    def _generate_session_id(self, query: str, run_id: str) -> str:
        """Generate deterministic session ID."""
        hash_input = f"{run_id}_{query}"
        hash_obj = hashlib.sha256(hash_input.encode())
        return f"{run_id}_{hash_obj.hexdigest()[:6]}"

    def _write_output(self, result: Dict[str, Any]) -> Path:
        """Write result to file bus."""
        # Get next output path
        output_path = self.manifest.get_next_filepath(subdir=OUT_DIR)

        # Prepare data
        data = [
            {
                "query": result.get("query"),
                "parsed": result.get("parsed"),
                "comparison_date": result.get("comparison_date"),
                "date_source": result.get("date_source"),
                "result": result,
            }
        ]

        # Create output with template
        output_data = create_output_template(
            data=data,
            query=f"Market query: {result.get('query', 'N/A')}",
            agent_name=AGENT_NAME,
            version=AGENT_VERSION,
        )

        # Add custom metadata
        output_data["metadata"]["comparison_date"] = result.get("comparison_date")
        output_data["metadata"]["date_source"] = result.get("date_source")
        output_data["metadata"]["low_volume_threshold"] = LOW_VOLUME_THRESHOLD

        # Write atomically
        logger.info("Writing output to %s", output_path)
        write_atomic(output_path, output_data)

        return output_path

    def _log_run(
        self,
        run_id: str,
        query: str,
        session_id: str,
        parsed: Dict[str, Any],
        comparison_date: Optional[str],
        output_path: Optional[Path],
        status: str,
        error: Optional[str] = None,
    ) -> None:
        """Log run metadata."""
        log_data: Dict[str, Any] = {
            "run_id": run_id,
            "query": query,
            "session_id": session_id,
            "parsed_topic": parsed.get("topic"),
            "parsed_date": parsed.get("date"),
            "comparison_date": comparison_date,
            "output_path": str(output_path) if output_path else None,
            "status": status,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": AGENT_NAME,
            "version": AGENT_VERSION,
        }

        log_file = self.workspace / LOGS_DIR / f"{run_id}.json"
        ensure_dir(log_file.parent)
        write_atomic(log_file, log_data)

        logger.info("Run log: %s", log_file)

    # ------------------------
    # Stats
    # ------------------------
    def get_stats(self) -> Dict[str, Any]:
        """Get agent statistics."""
        return self.manifest.get_stats()

