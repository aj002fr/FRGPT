#!/usr/bin/env python3
"""
Bloomberg email news -> Telegram macro approval workflow.

This is a collector/sender service only. Callback polling stays in
scripts/telegram_bot.py to avoid Telegram getUpdates conflicts.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import html
import logging
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, TypeVar

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import get_api_key
from scripts.telegram_bot_config import BOT_TOKEN
from scripts.x_macro_stream_bot import (
    MACRO_CHAT_IDS,
    MACRO_PUBLIC_CHAT_ID,
    SCORE_DESCRIPTIONS,
    is_authorized,
)
from src.core import graph_mail_client, outlook_mail_client
from src.core.bbg_news_store import (
    get_latest_received_at,
    get_news_label,
    get_news_label_by_graph_id,
    init_db,
    insert_news_label,
    list_approved_but_unforwarded,
    mark_forwarded,
    set_review_message_id,
    update_news_label,
)


logger = logging.getLogger(__name__)
T = TypeVar("T")

ERROR_ALREADY_EXISTS = 183
_INSTANCE_MUTEX_HANDLE = None

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
DEFAULT_BODY_PREVIEW_CHARS = 2800
DEFAULT_WATERMARK_PATH = Path(
    os.getenv(
        "BBG_NEWS_WATERMARK_PATH",
        str(Path.home() / ".market_data" / "bbg_news_watermark.txt"),
    )
)


def _read_config(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value:
        return value.strip()
    try:
        return get_api_key(name).strip()
    except Exception:
        return default


def _acquire_single_instance_lock() -> bool:
    """Return False when another Bloomberg news collector is already running."""

    global _INSTANCE_MUTEX_HANDLE
    mutex_name = "Global\\MarketDataPullerBloombergNewsCollector"
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        raise ctypes.WinError()
    _INSTANCE_MUTEX_HANDLE = handle
    return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def _load_senders() -> List[str]:
    raw = _read_config("BBG_NEWS_SENDERS", "") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _mail_source() -> str:
    return (_read_config("BBG_MAIL_SOURCE", "graph") or "graph").strip().lower()


def _escape_html(text: str) -> str:
    return html.escape(text or "")


def _format_score(score: int) -> str:
    return f"+{score}" if score > 0 else str(score)


def _format_score_line(score: int) -> str:
    description = SCORE_DESCRIPTIONS[score]
    return f"<i>Score: {_format_score(score)} - {_escape_html(description)}</i>"


def _format_user_label(user) -> str:
    if user is None:
        return "unknown"
    if getattr(user, "username", None):
        return f"@{user.username}"
    return user.full_name or "unknown"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n...[truncated]"


def _build_action_keyboard(news_id: int) -> InlineKeyboardMarkup:
    negative_and_zero = [-5, -4, -3, -2, -1, 0]
    positive = [1, 2, 3, 4, 5]
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(str(score), callback_data=f"bbgnews_score:{news_id}:{score}")
                for score in negative_and_zero
            ],
            [
                InlineKeyboardButton(f"+{score}", callback_data=f"bbgnews_score:{news_id}:{score}")
                for score in positive
            ],
            [
                InlineKeyboardButton("Approve (no score)", callback_data=f"bbgnews_approve:{news_id}"),
                InlineKeyboardButton("Reject", callback_data=f"bbgnews_reject:{news_id}"),
            ],
        ]
    )


def _format_news_message(record: dict) -> str:
    subject = str(record.get("subject") or "(no subject)")
    received = str(record.get("received_at") or record.get("received") or "")

    lines = [
        f"<b>{_escape_html(subject)}</b>",
    ]
    if received:
        lines.append(f"<i>{_escape_html(received)}</i>")
    return "\n".join(lines)


def _news_body_from_record_or_message(record: Optional[dict], query) -> str:
    if record:
        return _format_news_message(record)
    return query.message.text_html or query.message.text or ""


async def _send_with_retry(
    send_coro_factory: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await send_coro_factory()
        except (TimedOut, NetworkError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning("Telegram send timed out; retrying in %.1fs", delay)
                await asyncio.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("Telegram send retry loop exhausted without an exception")


async def _forward_news_to_public(
    bot: Bot,
    *,
    original_text: str,
    score: Optional[int],
) -> Optional[int]:
    if not MACRO_PUBLIC_CHAT_ID:
        return None

    forward_text = original_text
    score_line = None
    if score is not None:
        score_line = _format_score_line(score)
        annotated_text = f"{original_text}\n\n{score_line}"
        if len(annotated_text) <= TELEGRAM_MAX_MESSAGE_LENGTH:
            forward_text = annotated_text

    forwarded = await _send_with_retry(
        lambda: bot.send_message(
            chat_id=int(MACRO_PUBLIC_CHAT_ID),
            text=forward_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    )

    if score_line and forward_text == original_text:
        try:
            await _send_with_retry(
                lambda: bot.send_message(
                    chat_id=int(MACRO_PUBLIC_CHAT_ID),
                    text=score_line,
                    parse_mode="HTML",
                    reply_to_message_id=forwarded.message_id,
                    disable_web_page_preview=True,
                )
            )
        except Exception:
            logger.exception("Forwarded Bloomberg news but failed to send score reply")

    return forwarded.message_id


async def handle_bbg_news_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.message:
        return

    if not is_authorized(query.message.chat.id):
        await query.answer("Not authorized", show_alert=True)
        return

    parts = query.data.split(":", 2)
    if len(parts) < 2:
        await query.answer("Invalid action", show_alert=True)
        return

    action = parts[0]
    try:
        news_id = int(parts[1])
    except ValueError:
        await query.answer("Invalid news id", show_alert=True)
        return

    score: Optional[int] = None
    if action == "bbgnews_score":
        if len(parts) != 3:
            await query.answer("Invalid score", show_alert=True)
            return
        try:
            score = int(parts[2])
        except ValueError:
            await query.answer("Invalid score", show_alert=True)
            return
        if score not in SCORE_DESCRIPTIONS:
            await query.answer("Invalid score", show_alert=True)
            return
    elif action not in ("bbgnews_approve", "bbgnews_reject"):
        await query.answer("Invalid action", show_alert=True)
        return

    label = "rejected" if action == "bbgnews_reject" else "approved"
    existing = get_news_label(news_id)
    if not existing:
        logger.error("Bloomberg news label row missing for id=%s", news_id)
        await query.answer("Bloomberg news row missing", show_alert=True)
        await query.message.edit_text(
            (query.message.text_html or query.message.text or "")
            + "\n\n<b>Bloomberg news row missing.</b>",
            parse_mode="HTML",
            reply_markup=query.message.reply_markup,
        )
        return

    existing_label = existing.get("label")
    existing_forwarded_id = existing.get("forwarded_message_id")
    retrying_unforwarded_approval = (
        existing_label == "approved"
        and not existing_forwarded_id
        and label == "approved"
        and bool(MACRO_PUBLIC_CHAT_ID)
    )
    if existing_label and not retrying_unforwarded_approval:
        await query.answer("Already labeled", show_alert=True)
        return

    await query.answer("Working...")

    if retrying_unforwarded_approval and existing.get("score") is not None:
        score = int(existing["score"])

    original_text = _news_body_from_record_or_message(existing, query)
    user_label = _escape_html(_format_user_label(query.from_user))
    status_label = label.title()
    if score is not None:
        status_label = f"{status_label} (score={_format_score(score)})"
    status_line = f"\n\n<b>{status_label} by {user_label}</b>"
    new_text = original_text + status_line

    forwarded_id = None
    if label == "approved" and MACRO_PUBLIC_CHAT_ID:
        try:
            forwarded_id = await _forward_news_to_public(
                context.bot,
                original_text=original_text,
                score=score,
            )
        except Exception as exc:
            logger.error("Failed to forward Bloomberg news after retries: %s", exc)
            fail_text = (
                original_text
                + "\n\n<b>Forward failed (Telegram timeout) - click any score/approve button again to retry.</b>"
            )
            try:
                await query.message.edit_text(fail_text, parse_mode="HTML", reply_markup=query.message.reply_markup)
            except Exception:
                logger.exception("Failed to edit Bloomberg news message after forward failure")
            return

    if not retrying_unforwarded_approval:
        labeled = update_news_label(
            news_id,
            label,
            query.from_user.id if query.from_user else None,
            score=score,
        )
        if not labeled:
            logger.warning("Bloomberg news label changed before callback commit for id=%s", news_id)
            if forwarded_id:
                mark_forwarded(news_id, forwarded_id)
            return

    if forwarded_id:
        mark_forwarded(news_id, forwarded_id)

    try:
        await query.message.edit_text(new_text, parse_mode="HTML", reply_markup=None)
    except Exception as exc:
        logger.error("Failed to edit Bloomberg news review message: %s", exc)


async def cmd_bbg_retry_forwards(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not is_authorized(update.effective_chat.id):
        return
    if not MACRO_PUBLIC_CHAT_ID:
        await update.message.reply_text("X_MACRO_PUBLIC_CHAT_ID is not configured.")
        return

    limit = 50
    if context.args:
        try:
            limit = max(1, min(int(context.args[0]), 200))
        except ValueError:
            await update.message.reply_text("Usage: /bbg_retry_forwards [limit]")
            return

    rows = list_approved_but_unforwarded(limit=limit)
    if not rows:
        await update.message.reply_text("No approved-but-unforwarded Bloomberg news items found.")
        return

    forwarded = 0
    failed: List[str] = []
    for row in rows:
        news_id = int(row.get("id") or 0)
        original_text = _format_news_message(row)
        if not news_id or not original_text:
            failed.append(str(news_id or "(missing id)"))
            continue
        try:
            score = row.get("score")
            forwarded_id = await _forward_news_to_public(
                context.bot,
                original_text=original_text,
                score=int(score) if score is not None else None,
            )
            if forwarded_id:
                mark_forwarded(news_id, forwarded_id)
                forwarded += 1
        except Exception:
            logger.exception("Failed to retry Bloomberg news forward for id=%s", news_id)
            failed.append(str(news_id))

    await update.message.reply_text(
        "Retried Bloomberg news forwards.\n"
        f"- candidates: {len(rows)}\n"
        f"- forwarded: {forwarded}\n"
        f"- still failing: {len(failed)}"
    )


def _read_watermark() -> Optional[str]:
    try:
        if DEFAULT_WATERMARK_PATH.exists():
            value = DEFAULT_WATERMARK_PATH.read_text(encoding="utf-8").strip()
            return value or None
    except Exception:
        logger.exception("Failed to read Bloomberg news watermark")
    return None


def _write_watermark(value: str) -> None:
    try:
        DEFAULT_WATERMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = DEFAULT_WATERMARK_PATH.with_suffix(DEFAULT_WATERMARK_PATH.suffix + ".tmp")
        tmp_path.write_text(value, encoding="utf-8")
        tmp_path.replace(DEFAULT_WATERMARK_PATH)
    except Exception:
        logger.exception("Failed to write Bloomberg news watermark")


def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def _to_mail_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_since(args: argparse.Namespace) -> str:
    if args.since:
        parsed = _parse_dt(args.since)
        if parsed:
            return _to_mail_datetime(parsed)
        return args.since

    watermark = _read_watermark() or get_latest_received_at()
    parsed = _parse_dt(watermark or "")
    if parsed:
        return _to_mail_datetime(parsed - timedelta(seconds=1))

    lookback = max(1, int(args.lookback_minutes))
    return _to_mail_datetime(datetime.now(timezone.utc) - timedelta(minutes=lookback))


def _list_new_messages(mailbox: str, senders: List[str], since: str, top: int) -> List[dict]:
    source = _mail_source()
    if source in ("outlook", "outlook_com", "outlook-com", "desktop", "local_outlook"):
        return outlook_mail_client.list_new_messages(mailbox, senders, since, top)
    if source in ("graph", "msgraph", "microsoft_graph"):
        return graph_mail_client.list_new_messages(mailbox, senders, since, top)
    raise RuntimeError(f"Unsupported BBG_MAIL_SOURCE={source!r}")


async def _process_message(
    bot: Optional[Bot],
    message: dict,
    *,
    dry_run: bool = False,
) -> int:
    graph_id = str(message.get("graph_id") or "")
    if not graph_id:
        return 0
    if dry_run:
        logger.info(
            "DRY RUN Bloomberg news graph_id=%s sender=%s subject=%s",
            graph_id,
            message.get("from"),
            message.get("subject"),
        )
        return 0

    existing = get_news_label_by_graph_id(graph_id)
    if existing and (existing.get("telegram_message_id") or existing.get("label")):
        return 0

    news_id = insert_news_label(
        graph_id=graph_id,
        sender=str(message.get("from") or ""),
        subject=str(message.get("subject") or ""),
        body=str(message.get("body_preview") or ""),
        web_link=str(message.get("web_link") or ""),
        received_at=str(message.get("received") or ""),
        telegram_message_id=None,
    )
    record = get_news_label(news_id) or {}
    text = _format_news_message(record)

    sent_count = 0
    if bot:
        for chat_id in MACRO_CHAT_IDS:
            sent = await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=_build_action_keyboard(news_id),
            )
            set_review_message_id(news_id, sent.message_id)
            sent_count += 1

    received = str(record.get("received_at") or message.get("received") or "")
    if received:
        _write_watermark(received)
    return sent_count


async def poll_once(bot: Optional[Bot], args: argparse.Namespace) -> int:
    init_db()
    mailbox = _read_config("BBG_MAILBOX")
    if not mailbox:
        raise RuntimeError("BBG_MAILBOX is not configured")
    senders = _load_senders()
    if not senders:
        raise RuntimeError("BBG_NEWS_SENDERS is not configured")

    since = _resolve_since(args)
    messages = await asyncio.to_thread(_list_new_messages, mailbox, senders, since, int(args.top))

    sent_count = 0
    max_received = since
    for message in messages:
        sent_count += await _process_message(bot, message, dry_run=args.dry_run)
        received = str(message.get("received") or "")
        if received and received > max_received:
            max_received = received

    if max_received and not args.dry_run:
        _write_watermark(max_received)
    return sent_count


async def _safety_poll_loop(bot: Bot, args: argparse.Namespace) -> None:
    interval = int(args.safety_poll_seconds or 0)
    if interval <= 0:
        return
    while True:
        await asyncio.sleep(max(5, interval))
        try:
            sent = await poll_once(bot, args)
            logger.info("Bloomberg news safety poll complete; sent=%s", sent)
        except Exception:
            logger.exception("Bloomberg news safety poll failed")


async def run_event_service(args: argparse.Namespace) -> None:
    if _mail_source() not in ("outlook", "outlook_com", "outlook-com", "desktop", "local_outlook"):
        raise RuntimeError("Event mode requires BBG_MAIL_SOURCE=outlook_com")
    if args.dry_run:
        await poll_once(None, args)
        return

    mailbox = _read_config("BBG_MAILBOX")
    if not mailbox:
        raise RuntimeError("BBG_MAILBOX is not configured")
    senders = _load_senders()
    if not senders:
        raise RuntimeError("BBG_NEWS_SENDERS is not configured")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue()
    error_queue: asyncio.Queue[BaseException] = asyncio.Queue()
    stop_event = threading.Event()

    def enqueue_message(message: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, message)

    def listener_target() -> None:
        try:
            outlook_mail_client.watch_inbox(senders, enqueue_message, stop_event)
        except BaseException as exc:
            loop.call_soon_threadsafe(error_queue.put_nowait, exc)

    async with Bot(token=BOT_TOKEN) as bot:
        startup_sent = await poll_once(bot, args)
        logger.info("Bloomberg news startup catch-up complete; sent=%s", startup_sent)

        listener = threading.Thread(
            target=listener_target,
            name="bbg-news-outlook-itemadd",
            daemon=True,
        )
        listener.start()
        logger.info("Bloomberg news event listener thread started")

        safety_task = None
        if int(args.safety_poll_seconds or 0) > 0:
            safety_task = asyncio.create_task(_safety_poll_loop(bot, args))

        try:
            while True:
                try:
                    error = error_queue.get_nowait()
                except asyncio.QueueEmpty:
                    error = None
                if error:
                    raise RuntimeError(f"Bloomberg news Outlook event listener failed: {error}") from error

                try:
                    message = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                try:
                    sent = await _process_message(bot, message)
                    logger.info("Bloomberg news event processed; sent=%s", sent)
                except Exception:
                    logger.exception("Failed to process Bloomberg news event")
        finally:
            stop_event.set()
            if safety_task:
                safety_task.cancel()
                try:
                    await safety_task
                except asyncio.CancelledError:
                    pass
            listener.join(timeout=5)


async def run_poll_service(args: argparse.Namespace) -> None:
    if args.dry_run:
        await poll_once(None, args)
        return

    async with Bot(token=BOT_TOKEN) as bot:
        while True:
            try:
                sent = await poll_once(bot, args)
                logger.info("Bloomberg news poll complete; sent=%s", sent)
            except Exception:
                logger.exception("Bloomberg news poll failed")
            if args.once:
                return
            await asyncio.sleep(max(5, int(args.poll_seconds)))


async def run_service(args: argparse.Namespace) -> None:
    if args.mode == "event":
        await run_event_service(args)
        return
    await run_poll_service(args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bloomberg email news -> Telegram approval workflow.")
    parser.add_argument(
        "--mode",
        choices=("poll", "event"),
        default=os.getenv("BBG_NEWS_MODE", "poll").strip().lower(),
        help="Collector mode: interval polling or Outlook ItemAdd event push.",
    )
    parser.add_argument("--once", action="store_true", help="Poll once and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and log candidates without DB or Telegram writes.")
    parser.add_argument("--since", help="Message received-time lower bound, e.g. 2026-06-10T12:00:00Z.")
    parser.add_argument("--lookback-minutes", type=int, default=60, help="Initial lookback when no watermark exists.")
    parser.add_argument("--top", type=int, default=50, help="Max Graph messages to fetch per poll.")
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=int(os.getenv("BBG_NEWS_POLL_SECONDS", "60")),
        help="Polling interval in seconds for loop mode.",
    )
    parser.add_argument(
        "--safety-poll-seconds",
        type=int,
        default=int(os.getenv("BBG_NEWS_SAFETY_POLL_SECONDS", "0")),
        help="Optional slow backup poll interval in event mode; 0 disables it.",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()
    if not _acquire_single_instance_lock():
        logger.error("Another Bloomberg news collector is already running; exiting.")
        raise SystemExit(2)
    asyncio.run(run_service(args))


if __name__ == "__main__":
    main()
