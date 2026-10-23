#!/usr/bin/env python3
"""Pull Treasury coupon auction results from FiscalData into local CSVs."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from auction_data.process_auction_files import process_raw_file
from src.core.telegram_notifier import load_telegram_chat_id, send_telegram_message
from src.servers.auctiondata.alert_formatter import format_auction_alert
from src.servers.auctiondata.csv_writer import merge_auction_into_csv
from src.servers.auctiondata.fiscal_data_client import fetch_auctions_for_term, fetch_recent_auctions
from src.servers.auctiondata.schema import (
    AUCTION_DATA_DIR,
    AUCTION_TERM_MAP,
    PROJECT_ROOT as SCHEMA_PROJECT_ROOT,
    AuctionTermConfig,
    get_term_config,
)
from src.servers.auctiondata.state import load_state, mark_alerted, save_state, was_alerted


LOGGER = logging.getLogger("auction_data_puller")
NY_TZ = ZoneInfo("America/New_York")
ECONOMIC_EVENTS_DB = SCHEMA_PROJECT_ROOT / "workspace" / "economic_events.db"
PRE_EVENT_LEAD_SECONDS = 10 * 60
PRE_EVENT_INTERVAL_SECONDS = 30
RESULT_WINDOW_SECONDS = 30 * 60
IDLE_MAX_SLEEP_SECONDS = 5 * 60


@dataclass(frozen=True)
class PollResult:
    new_rows: int
    captured_event_keys: set[str]


@dataclass(frozen=True)
class AuctionCalendarEvent:
    event_name: str
    event_time: datetime
    event_key: str


@dataclass(frozen=True)
class PollSchedule:
    state: str
    sleep_seconds: int
    reason: str
    event: AuctionCalendarEvent | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pull Treasury auction results from FiscalData")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one poll and exit")
    mode.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=60, help="Normal loop polling interval in seconds")
    parser.add_argument("--fast-interval", type=int, default=10, help="Auction-window loop interval in seconds")
    parser.add_argument("--days-back", type=int, default=14, help="Lookback for normal polling")
    parser.add_argument("--backfill", action="store_true", help="Fetch full available history and seed CSV/state")
    parser.add_argument("--dry-run", action="store_true", help="Log intended writes/alerts without side effects")
    parser.add_argument("--send-alerts", action="store_true", help="Allow Telegram alerts during --backfill")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _record_term_config(record: dict) -> AuctionTermConfig | None:
    return get_term_config(str(record.get("security_type", "")), str(record.get("security_term", "")))


def _fetch_backfill_records(terms: Iterable[AuctionTermConfig]) -> list[dict]:
    rows: list[dict] = []
    for term in terms:
        LOGGER.info("Fetching full history for %s %s", term.security_type, term.security_term)
        rows.extend(fetch_auctions_for_term(term, start_date=None, page_size=100))
    return rows


def _send_alert(csv_row: dict[str, str], term_label: str, dry_run: bool) -> bool:
    message = format_auction_alert(csv_row, term_label)
    if dry_run:
        LOGGER.info("DRY RUN alert:\n%s", message)
        return True

    chat_id = load_telegram_chat_id("AUCTION_ALERT_CHAT_ID", "TELEGRAM_CHAT_ID")
    result = send_telegram_message(message, chat_id=chat_id, parse_mode="HTML")
    if not result.get("success"):
        LOGGER.error("Telegram alert failed: %s", result.get("error"))
        return False
    return True


def result_fields_available(csv_row: dict[str, str]) -> bool:
    """True only once Treasury has published competitive auction results."""

    result_fields = (
        "Bid-to-Cover Ratio",
        "High Yield",
        "Competitive Accepted",
        "Primary Dealer Accepted",
        "Direct Bidder Accepted",
        "Indirect Bidder Accepted",
    )
    return any(str(csv_row.get(field, "")).strip() for field in result_fields)


def _event_key_for_record(record: dict) -> str | None:
    term_config = _record_term_config(record)
    auction_date = str(record.get("auction_date", "")).strip()
    if term_config is None or not auction_date:
        return None
    return f"{term_config.security_type}|{term_config.security_term}|{auction_date[:10]}"


def process_records(records: list[dict], *, alert: bool, dry_run: bool) -> PollResult:
    state = load_state()
    touched_raw_files: set[Path] = set()
    added_count = 0
    captured_event_keys: set[str] = set()

    for record in records:
        term_config = _record_term_config(record)
        if term_config is None:
            continue

        csv_path = AUCTION_DATA_DIR / term_config.csv_filename
        cusip = str(record.get("cusip", "")).strip()
        if not cusip:
            LOGGER.warning("Skipping auction row with no CUSIP: %s", record)
            continue

        if dry_run:
            LOGGER.info("DRY RUN merge %s into %s", cusip, csv_path)
            csv_row = {}
            added = True
        else:
            merge = merge_auction_into_csv(csv_path, record)
            csv_row = merge.row
            added = merge.added
            if added or merge.updated:
                touched_raw_files.add(csv_path)
                added_count += int(added)
                action = "Added" if added else "Updated"
                LOGGER.info("%s %s in %s", action, cusip, csv_path.name)
            else:
                LOGGER.debug("CUSIP %s already present in %s", cusip, csv_path.name)

        is_result = bool(csv_row) and result_fields_available(csv_row)
        if is_result:
            event_key = _event_key_for_record(record)
            if event_key:
                captured_event_keys.add(event_key)
        should_alert = alert and is_result and not was_alerted(cusip, state)
        if should_alert:
            if not csv_row:
                # In dry-run mode build the row through the merge path only in logs; alerts are best-effort.
                LOGGER.info("DRY RUN would alert for %s %s", term_config.label, cusip)
            elif _send_alert(csv_row, term_config.label, dry_run=dry_run):
                mark_alerted(cusip, state)
        elif not alert and is_result:
            mark_alerted(cusip, state)

    if not dry_run:
        for raw_path in sorted(touched_raw_files):
            LOGGER.info("Regenerating processed CSV for %s", raw_path.name)
            process_raw_file(raw_path)
        save_state(state)

    return PollResult(new_rows=added_count, captured_event_keys=captured_event_keys)


def _parse_event_time(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw[:10], "%Y-%m-%d").replace(hour=13)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NY_TZ)
    return parsed.astimezone(NY_TZ)


def _term_config_for_event_name(event_name: str) -> AuctionTermConfig | None:
    normalized = event_name.lower()
    for term in AUCTION_TERM_MAP:
        if term.security_term.lower() in normalized and term.security_type.lower() in normalized:
            return term
    return None


def _event_key_for_calendar_event(event_name: str, event_time: datetime) -> str | None:
    term_config = _term_config_for_event_name(event_name)
    if term_config is None:
        return None
    return f"{term_config.security_type}|{term_config.security_term}|{event_time.date().isoformat()}"


def get_auction_calendar_events(db_path: Path = ECONOMIC_EVENTS_DB) -> list[AuctionCalendarEvent]:
    """Load today/tomorrow US coupon auction events from economic_events.db."""

    if not db_path.exists():
        return []
    now_et = datetime.now(NY_TZ)
    today = now_et.date()
    tomorrow = today + timedelta(days=1)
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT event_name, event_date
                FROM economic_events
                WHERE country LIKE '%United States%'
                  AND event_name LIKE '%-Year %Auction%'
                  AND substr(event_date, 1, 10) BETWEEN ? AND ?
                ORDER BY event_date ASC
                """,
                (today.isoformat(), tomorrow.isoformat()),
            ).fetchall()
    except sqlite3.Error as exc:
        LOGGER.debug("Could not inspect auction calendar window: %s", exc)
        return []

    events: list[AuctionCalendarEvent] = []
    for event_name, event_date in rows:
        event_time = _parse_event_time(event_date)
        if event_time is None:
            continue
        event_key = _event_key_for_calendar_event(str(event_name), event_time)
        if event_key is None:
            continue
        events.append(AuctionCalendarEvent(event_name=str(event_name), event_time=event_time, event_key=event_key))
    return events


def get_next_poll_delay(
    captured_event_keys: set[str],
    *,
    interval: int,
    fast_interval: int,
    db_path: Path = ECONOMIC_EVENTS_DB,
    now_et: datetime | None = None,
) -> PollSchedule:
    """Return the next sleep interval and scheduler state."""

    now_et = now_et or datetime.now(NY_TZ)
    events = get_auction_calendar_events(db_path)
    if not events:
        return PollSchedule(
            state="fallback",
            sleep_seconds=max(1, interval),
            reason="no auction calendar events found; using fixed interval",
        )

    future_or_active = [
        event
        for event in events
        if event.event_time + timedelta(seconds=RESULT_WINDOW_SECONDS) >= now_et
        and event.event_key not in captured_event_keys
    ]
    if not future_or_active:
        return PollSchedule(
            state="idle",
            sleep_seconds=IDLE_MAX_SLEEP_SECONDS,
            reason="no uncaptured auction events in today/tomorrow window",
        )

    event = min(future_or_active, key=lambda item: item.event_time)
    seconds_to_event = int((event.event_time - now_et).total_seconds())

    if seconds_to_event > PRE_EVENT_LEAD_SECONDS:
        sleep_seconds = min(seconds_to_event - PRE_EVENT_LEAD_SECONDS, IDLE_MAX_SLEEP_SECONDS)
        return PollSchedule(
            state="idle",
            sleep_seconds=max(1, sleep_seconds),
            reason=f"next auction is {event.event_name} at {event.event_time.isoformat()}",
            event=event,
        )

    if seconds_to_event > 0:
        return PollSchedule(
            state="pre-event",
            sleep_seconds=PRE_EVENT_INTERVAL_SECONDS,
            reason=f"within {PRE_EVENT_LEAD_SECONDS // 60} minutes before {event.event_name}",
            event=event,
        )

    seconds_since_event = int((now_et - event.event_time).total_seconds())
    if seconds_since_event <= RESULT_WINDOW_SECONDS:
        return PollSchedule(
            state="result-window",
            sleep_seconds=max(1, fast_interval),
            reason=f"waiting for {event.event_name} results",
            event=event,
        )

    captured_event_keys.add(event.event_key)
    return PollSchedule(
        state="cooldown",
        sleep_seconds=IDLE_MAX_SLEEP_SECONDS,
        reason=f"result window elapsed for {event.event_name}",
        event=event,
    )


def run_once(days_back: int, dry_run: bool) -> int:
    records = fetch_recent_auctions(days_back=days_back)
    return process_records(records, alert=True, dry_run=dry_run).new_rows


def run_backfill(dry_run: bool, send_alerts: bool) -> int:
    records = _fetch_backfill_records(AUCTION_TERM_MAP)
    result = process_records(records, alert=send_alerts, dry_run=dry_run)
    if not dry_run:
        for term in AUCTION_TERM_MAP:
            raw_path = AUCTION_DATA_DIR / term.csv_filename
            if raw_path.exists():
                LOGGER.info("Backfill final refresh for %s", raw_path.name)
                process_raw_file(raw_path)
    return result.new_rows


def run_loop(interval: int, fast_interval: int, days_back: int, dry_run: bool) -> None:
    LOGGER.info("Starting auction puller loop")
    captured_event_keys: set[str] = set()
    last_schedule_state: str | None = None
    last_schedule_reason: str | None = None
    while True:
        schedule = get_next_poll_delay(captured_event_keys, interval=interval, fast_interval=fast_interval)
        should_poll = schedule.state in {"pre-event", "result-window", "fallback"}

        if schedule.state != last_schedule_state or schedule.reason != last_schedule_reason:
            LOGGER.info(
                "Scheduler state=%s sleep=%ss reason=%s",
                schedule.state,
                schedule.sleep_seconds,
                schedule.reason,
            )
            last_schedule_state = schedule.state
            last_schedule_reason = schedule.reason

        try:
            if should_poll:
                records = fetch_recent_auctions(days_back=days_back)
                result = process_records(records, alert=True, dry_run=dry_run)
                if result.captured_event_keys:
                    captured_event_keys.update(result.captured_event_keys)
                    LOGGER.info("Captured auction result event keys: %s", sorted(result.captured_event_keys))
                LOGGER.info("Poll complete: %s new auction row(s)", result.new_rows)
            else:
                LOGGER.debug("Skipping poll while scheduler state=%s", schedule.state)
        except Exception:
            LOGGER.exception("Auction poll failed")
        time.sleep(max(1, schedule.sleep_seconds))


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)

    if args.backfill:
        added = run_backfill(dry_run=args.dry_run, send_alerts=args.send_alerts)
        LOGGER.info("Backfill complete: %s new auction row(s)", added)
        return 0

    if args.loop:
        run_loop(args.interval, args.fast_interval, args.days_back, args.dry_run)
        return 0

    added = run_once(days_back=args.days_back, dry_run=args.dry_run)
    LOGGER.info("Run complete: %s new auction row(s)", added)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

