"""
Near-live Polymarket probability tracker (polling-first).

Resolves a natural language query to a market and polls the CLOB midpoint
price for the selected token(s), storing results in SQLite.
"""

from __future__ import annotations

import argparse
import logging
import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.servers.polymarket.poll_midpoint import (
    ensure_polling_tables,
    fetch_midpoint,
    get_default_db_path,
    insert_timeseries_row,
    mark_market_inactive,
    upsert_tracked_market,
    utc_now_iso,
)
from src.servers.polymarket.search_markets import (
    call_polymarket_search_api,
    parse_public_search_response,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("polymarket_probability_tracker")


@dataclass
class TokenState:
    token_id: str
    condition_id: Optional[str]
    market_slug: Optional[str]
    last_mid: Optional[float] = None
    last_write_ts: float = 0.0
    next_allowed_ts: float = 0.0
    backoff_s: float = 0.0
    not_found_count: int = 0


def resolve_markets(query: str, max_markets: int) -> List[Dict]:
    api_response = call_polymarket_search_api(query, limit_per_type=max_markets)
    markets = parse_public_search_response(api_response, query)
    return markets[:max_markets]


def build_token_states(
    conn: sqlite3.Connection,
    markets: List[Dict],
    track_no_token: bool,
) -> List[TokenState]:
    states: List[TokenState] = []
    for market in markets:
        condition_id = market.get("market_id")
        market_slug = market.get("slug")
        title = market.get("title")
        clob_token_ids = market.get("clob_token_ids") or []

        yes_token_id = clob_token_ids[0] if len(clob_token_ids) > 0 else None
        no_token_id = clob_token_ids[1] if len(clob_token_ids) > 1 else None

        if condition_id:
            upsert_tracked_market(
                conn=conn,
                condition_id=condition_id,
                market_slug=market_slug,
                title=title,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
            )

        if yes_token_id:
            states.append(
                TokenState(
                    token_id=yes_token_id,
                    condition_id=condition_id,
                    market_slug=market_slug,
                )
            )
        if track_no_token and no_token_id:
            states.append(
                TokenState(
                    token_id=no_token_id,
                    condition_id=condition_id,
                    market_slug=market_slug,
                )
            )
    return states


def load_active_tracked_token_states(
    conn: sqlite3.Connection,
    track_no_token: bool,
) -> List[TokenState]:
    """Load active tracked markets from DB (for Telegram-managed workflows)."""
    states: List[TokenState] = []
    cursor = conn.cursor()
    rows = cursor.execute(
        """
        SELECT condition_id, market_slug, yes_token_id, no_token_id
        FROM tracked_markets
        WHERE active = 1
        """
    ).fetchall()
    for row in rows:
        condition_id = row[0]
        market_slug = row[1]
        yes_token_id = row[2]
        no_token_id = row[3]
        if yes_token_id:
            states.append(
                TokenState(
                    token_id=yes_token_id,
                    condition_id=condition_id,
                    market_slug=market_slug,
                )
            )
        if track_no_token and no_token_id:
            states.append(
                TokenState(
                    token_id=no_token_id,
                    condition_id=condition_id,
                    market_slug=market_slug,
                )
            )
    return states


def should_insert(
    state: TokenState,
    mid: float,
    epsilon: float,
    max_gap_s: int,
    now_ts: float,
) -> bool:
    if state.last_mid is None:
        return True
    if abs(mid - state.last_mid) >= epsilon:
        return True
    if now_ts - state.last_write_ts >= max_gap_s:
        return True
    return False


def poll_loop(
    conn: sqlite3.Connection,
    token_states: List[TokenState],
    poll_interval_s: int,
    epsilon: float,
    max_gap_s: int,
    max_not_found: int,
    reresolve_every_mins: int,
    query: Optional[str],
    max_markets: int,
    track_no_token: bool,
) -> None:
    next_reresolve_ts = time.time() + (reresolve_every_mins * 60) if reresolve_every_mins > 0 else None

    while token_states:
        cycle_start = time.time()

        if next_reresolve_ts and query and time.time() >= next_reresolve_ts:
            logger.info("Re-resolving query for new markets: %s", query)
            markets = resolve_markets(query, max_markets=max_markets)
            new_states = build_token_states(conn, markets, track_no_token=track_no_token)
            existing_tokens = {state.token_id for state in token_states}
            for state in new_states:
                if state.token_id not in existing_tokens:
                    logger.info("Adding newly resolved token: %s", state.token_id)
                    token_states.append(state)
                    existing_tokens.add(state.token_id)
            next_reresolve_ts = time.time() + (reresolve_every_mins * 60)

        for state in list(token_states):
            now_ts = time.time()
            if now_ts < state.next_allowed_ts:
                continue

            mid, status = fetch_midpoint(state.token_id)
            if mid is None:
                if status == 404:
                    state.not_found_count += 1
                    if state.not_found_count >= max_not_found:
                        logger.warning(
                            "Token %s returned 404 %d times; marking inactive",
                            state.token_id,
                            state.not_found_count,
                        )
                        if state.condition_id:
                            mark_market_inactive(conn, state.condition_id)
                        token_states.remove(state)
                    continue

                if status in {429, 500, 502, 503, 504} or status is None:
                    state.backoff_s = 2.0 if state.backoff_s == 0 else min(state.backoff_s * 2, 60.0)
                    jitter = random.uniform(0.0, 1.0)
                    state.next_allowed_ts = now_ts + state.backoff_s + jitter
                continue

            state.not_found_count = 0
            state.backoff_s = 0.0
            state.next_allowed_ts = 0.0

            if should_insert(state, mid, epsilon=epsilon, max_gap_s=max_gap_s, now_ts=now_ts):
                insert_timeseries_row(
                    conn=conn,
                    token_id=state.token_id,
                    condition_id=state.condition_id,
                    market_slug=state.market_slug,
                    observed_ts=utc_now_iso(),
                    mid=mid,
                    best_bid=None,
                    best_ask=None,
                    source="rest_midpoint_poll",
                    raw_payload={"mid": mid},
                )
                state.last_mid = mid
                state.last_write_ts = now_ts

            time.sleep(0.2 + random.uniform(0.0, 0.2))

        elapsed = time.time() - cycle_start
        sleep_for = max(0.0, poll_interval_s - elapsed)
        if sleep_for:
            time.sleep(sleep_for)


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket probability tracker (polling-first).")
    parser.add_argument("--query", type=str, help="Natural language market query")
    parser.add_argument("--token-id", type=str, help="Explicit token_id to track (CLOB asset id)")
    parser.add_argument("--condition-id", type=str, help="Optional condition id for tracking metadata")
    parser.add_argument("--market-slug", type=str, help="Optional slug for tracking metadata")
    parser.add_argument("--title", type=str, help="Optional title for tracking metadata")
    parser.add_argument("--track-no-token", action="store_true", help="Also track NO token if available")
    parser.add_argument("--max-markets", type=int, default=5, help="Max markets to track from query")
    parser.add_argument("--poll-interval-s", type=int, default=60, help="Polling interval in seconds")
    parser.add_argument("--epsilon", type=float, default=0.0005, help="Min change to record")
    parser.add_argument("--max-gap-s", type=int, default=600, help="Max seconds between inserts")
    parser.add_argument("--max-not-found", type=int, default=3, help="404s before stop")
    parser.add_argument("--reresolve-every-mins", type=int, default=0, help="Re-resolve query cadence (0=off)")
    parser.add_argument("--db-path", type=str, help="SQLite DB path override")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else get_default_db_path()
    conn = sqlite3.connect(db_path)
    ensure_polling_tables(conn)

    token_states: List[TokenState] = []

    if args.token_id:
        if args.condition_id:
            upsert_tracked_market(
                conn=conn,
                condition_id=args.condition_id,
                market_slug=args.market_slug,
                title=args.title,
                yes_token_id=args.token_id,
                no_token_id=None,
            )
        token_states.append(
            TokenState(
                token_id=args.token_id,
                condition_id=args.condition_id,
                market_slug=args.market_slug,
            )
        )
    else:
        if args.query:
            markets = resolve_markets(args.query, max_markets=args.max_markets)
            token_states = build_token_states(conn, markets, track_no_token=args.track_no_token)
        else:
            # No CLI market input: use markets already selected via Telegram and stored in DB.
            token_states = load_active_tracked_token_states(conn, track_no_token=args.track_no_token)
            if not token_states:
                logger.info(
                    "No active tracked markets found in DB. "
                    "Add markets from Telegram first, then run this script again."
                )
                return

    logger.info("Tracking %d token(s) on %s", len(token_states), db_path)

    poll_loop(
        conn=conn,
        token_states=token_states,
        poll_interval_s=args.poll_interval_s,
        epsilon=args.epsilon,
        max_gap_s=args.max_gap_s,
        max_not_found=args.max_not_found,
        reresolve_every_mins=args.reresolve_every_mins,
        query=args.query,
        max_markets=args.max_markets,
        track_no_token=args.track_no_token,
    )


if __name__ == "__main__":
    main()
