#!/usr/bin/env python3
"""
Real-time Iran/Israel/Lebanon Telegram headline listener.

Runs a persistent Telethon client against the same distribution chat used by
``scrape_iran_headlines.py``. On startup it performs an incremental catch-up
from ``MAX(timestamp) - overlap`` and then inserts matching new messages into
``workspace/iran_headlines.db`` as they arrive.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.scrape_iran_headlines import (  # noqa: E402
    CHAT_ID,
    DB_PATH,
    SESSION_PATH,
    TELEGRAM_API_HASH,
    TELEGRAM_API_ID,
    auto_since_from_db,
    init_db,
    process_telegram_message,
)

LOG_DIR = PROJECT_ROOT / "workspace" / "logs"
SHARE_LOG_PATH = LOG_DIR / "iran_headlines_stream.log"
LOCAL_STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".market_data"))) / "market_data_puller"
LOCAL_LOG_DIR = LOCAL_STATE_DIR / "logs"
STATUS_DIR = LOCAL_STATE_DIR / "status"
LOCAL_LOG_PATH = LOCAL_LOG_DIR / "iran_headlines_stream.log"
HEARTBEAT_MARKER = STATUS_DIR / "iran_headlines_stream_heartbeat.txt"
LAST_MESSAGE_MARKER = STATUS_DIR / "iran_headlines_stream_last_message.txt"
ERROR_MARKER = STATUS_DIR / "iran_headlines_stream_last_error.txt"
SUCCESS_MARKER = STATUS_DIR / "iran_headlines_stream_last_success.txt"
LOCK_PATH = STATUS_DIR / "iran_headlines_stream.lock"


class DualFileHandler(logging.Handler):
    """Log to a local file and best-effort shared workspace log."""

    def __init__(self, local_path: Path, shared_path: Path) -> None:
        super().__init__()
        self.local_path = local_path
        self.shared_path = shared_path
        self.shared_available = True
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.shared_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.shared_available = False

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        self._append(self.local_path, msg)
        if self.shared_available:
            try:
                self._append(self.shared_path, msg)
            except OSError:
                self.shared_available = False
                self._append(
                    self.local_path,
                    f"{datetime.now(timezone.utc).isoformat()} - WARNING - Shared stream log unavailable: {self.shared_path}",
                )

    @staticmethod
    def _append(path: Path, msg: str) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")


logger = logging.getLogger("iran_headlines_stream")


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)

    file_handler = DualFileHandler(LOCAL_LOG_PATH, SHARE_LOG_PATH)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)


def write_marker(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


@contextmanager
def single_instance_lock(lock_path: Path) -> Iterator[None]:
    """Cross-platform advisory lock held for the lifetime of the process."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+")
    acquired = False
    try:
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError as exc:
                raise RuntimeError(f"another iran_headlines_stream instance is already running ({lock_path})") from exc
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise RuntimeError(f"another iran_headlines_stream instance is already running ({lock_path})") from exc
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"pid={os.getpid()}\nstarted_at={datetime.now(timezone.utc).isoformat()}\n")
        lock_file.flush()
        yield
    finally:
        try:
            if acquired and os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            elif acquired:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


async def catch_up(client: object, db_path: Path) -> int:
    """Backfill messages since the DB watermark before live listening."""

    since = auto_since_from_db(db_path)
    if since is None:
        logger.info("No DB watermark found; catch-up will scan recent chat history.")
    else:
        logger.info("Catch-up from DB watermark overlap: %s", since.isoformat())

    conn = init_db(db_path)
    inserted = 0
    scanned = 0
    try:
        async for message in client.iter_messages(CHAT_ID, limit=None):
            scanned += 1
            if since is not None and message.date.astimezone(timezone.utc) <= since:
                break
            inserted_now, source, status = process_telegram_message(conn, message)
            if inserted_now:
                inserted += 1
                if source:
                    logger.info("Catch-up inserted source=%s msg_id=%s", source, message.id)
            elif status not in ("no_text", "not_topic", "noise", "duplicate"):
                logger.debug("Catch-up skipped msg_id=%s status=%s", message.id, status)
            if scanned % 500 == 0:
                conn.commit()
        conn.commit()
    finally:
        conn.close()

    logger.info("Catch-up complete: scanned=%s inserted=%s db=%s", scanned, inserted, db_path)
    return inserted


async def heartbeat_loop(interval_seconds: int) -> None:
    while True:
        now = datetime.now(timezone.utc).isoformat()
        write_marker(HEARTBEAT_MARKER, f"{now}\npid={os.getpid()}\n")
        await asyncio.sleep(interval_seconds)


async def run_listener_once(args: argparse.Namespace) -> None:
    from telethon import TelegramClient, events

    Path(SESSION_PATH).parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(SESSION_PATH, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    conn: Optional[sqlite3.Connection] = None
    heartbeat_task: Optional[asyncio.Task] = None

    try:
        await client.start()
        logger.info("Connected to Telegram chat=%s session=%s", CHAT_ID, SESSION_PATH)
        await catch_up(client, args.db)
        if args.catch_up_only:
            logger.info("Catch-up-only requested; exiting before live listener starts.")
            return

        conn = init_db(args.db)
        heartbeat_task = asyncio.create_task(heartbeat_loop(args.heartbeat_seconds))

        @client.on(events.NewMessage(chats=CHAT_ID))
        async def on_new_message(event: object) -> None:
            message = event.message
            inserted, source, status = process_telegram_message(conn, message)
            conn.commit()
            if inserted:
                ts = message.date.astimezone(timezone.utc).isoformat()
                write_marker(
                    LAST_MESSAGE_MARKER,
                    f"{ts}\nmessage_id={message.id}\nsource={source or ''}\n",
                )
                if ERROR_MARKER.exists():
                    ERROR_MARKER.unlink()
                write_marker(SUCCESS_MARKER, f"{datetime.now(timezone.utc).isoformat()}\nlast_message_id={message.id}\n")
                logger.info("Inserted live headline source=%s msg_id=%s", source, message.id)
            elif status == "duplicate":
                logger.debug("Duplicate live headline msg_id=%s", message.id)
            else:
                logger.debug("Skipped live message msg_id=%s status=%s", getattr(message, "id", None), status)

        logger.info("Live listener started; waiting for new messages.")
        await client.run_until_disconnected()
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        if conn is not None:
            conn.close()
        await client.disconnect()


async def supervise(args: argparse.Namespace) -> None:
    attempts = 0
    while True:
        try:
            await run_listener_once(args)
            return
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            attempts += 1
            now = datetime.now(timezone.utc).isoformat()
            write_marker(ERROR_MARKER, f"{now}\nerror={exc!r}\nattempts={attempts}\n")
            logger.exception("Listener crashed (attempt %s); reconnecting after %ss", attempts, args.reconnect_seconds)
            await asyncio.sleep(args.reconnect_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real-time Iran/Israel Telegram headline listener.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path (default: {DB_PATH})")
    parser.add_argument(
        "--catch-up-only",
        action="store_true",
        help="Run startup catch-up and exit before registering the live listener.",
    )
    parser.add_argument("--heartbeat-seconds", type=int, default=30, help="Heartbeat marker cadence.")
    parser.add_argument("--reconnect-seconds", type=int, default=15, help="Delay before reconnect after hard failure.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)
    with single_instance_lock(LOCK_PATH):
        logger.info("Starting Iran headlines stream db=%s", args.db)
        asyncio.run(supervise(args))


if __name__ == "__main__":
    main()
