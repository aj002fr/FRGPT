#!/usr/bin/env python3
"""
X filtered-stream -> Telegram macro bot (separate always-on service).
"""

from __future__ import annotations

import argparse
import asyncio
import html
import logging
import os
import random
import sys
from pathlib import Path
from collections import deque
from typing import Awaitable, Callable, Dict, List, Optional, Tuple, TypeVar

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import get_api_key
from scripts.telegram_bot_config import ALLOWED_CHAT_IDS, BOT_TOKEN
from src.core import x_api_client
from src.core.x_macro_store import (
    MODE_ALL,
    MODE_GATED,
    add_handle,
    add_keyword,
    get_tweet_label,
    list_approved_but_unforwarded,
    list_handles,
    list_keywords,
    normalize_handle,
    insert_tweet_label,
    mark_forwarded,
    remove_handle,
    remove_keyword,
    seed_defaults,
    update_tweet_label,
)
from src.core.x_rules import TAG_PREFIX, build_account_rules, build_gated_rule_objects


logger = logging.getLogger(__name__)
T = TypeVar("T")


def _load_macro_chat_ids() -> List[int]:
    raw = os.getenv("X_MACRO_CHAT_ID") or os.getenv("X_MACRO_CHAT_IDS") or ""
    if not raw.strip():
        return ALLOWED_CHAT_IDS
    return [int(cid.strip()) for cid in raw.split(",") if cid.strip()]


MACRO_CHAT_IDS = _load_macro_chat_ids()
MACRO_PUBLIC_CHAT_ID = os.getenv("X_MACRO_PUBLIC_CHAT_ID")
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

SCORE_DESCRIPTIONS = {
    -5: "max deescalatory, full resolution imminent, do not fade",
    -4: "very strongly deescalatory, room for things to go wrong but very unlikely",
    -3: "strongly deescalatory, an argument could exist to fade but with a lot of caution",
    -2: "deescalatory, but requires further news to strengthen",
    -1: "minor deescalation",
    0: "noisy",
    1: "minor escalation",
    2: "escalatory, but requires further news",
    3: "strongly escalatory, an argument could exist to fade but with a lot of caution",
    4: "very strongly escalatory, still possible for things to resolve but it looks like it will get worse first",
    5: "max escalatory, war has once again broken out",
}


def is_authorized(chat_id: int) -> bool:
    if not MACRO_CHAT_IDS or MACRO_CHAT_IDS == [0]:
        return False
    return chat_id in MACRO_CHAT_IDS


def _format_status() -> str:
    handles = list_handles()
    keywords = list_keywords()
    all_count = sum(1 for h in handles if h.mode == MODE_ALL)
    gated_count = sum(1 for h in handles if h.mode == MODE_GATED)
    return (
        "<b>X Macro Stream Status</b>\n"
        f"- Handles: {len(handles)} (all={all_count}, gated={gated_count})\n"
        f"- Keywords: {len(keywords)}\n"
    )


def _build_rules_snapshot() -> Tuple[List[x_api_client.StreamRule], List[x_api_client.StreamRule]]:
    handles = list_handles()
    keywords = list_keywords()
    all_handles = [h.handle for h in handles if h.mode == MODE_ALL]
    gated_handles = [h.handle for h in handles if h.mode == MODE_GATED]

    account_rules = [
        x_api_client.StreamRule(value=r.value, tag=r.tag)
        for r in build_account_rules(all_handles)
    ]
    gated_rules = [
        x_api_client.StreamRule(value=r.value, tag=r.tag)
        for r in build_gated_rule_objects(gated_handles, keywords)
    ]
    return account_rules, gated_rules


def reconcile_rules(bearer_token: str) -> Dict[str, int]:
    existing = x_api_client.list_stream_rules(bearer_token)
    desired_all, desired_gated = _build_rules_snapshot()
    desired = desired_all + desired_gated

    existing_prefixed = [r for r in existing if r.tag and r.tag.startswith(TAG_PREFIX)]
    existing_set = {(r.value, r.tag) for r in existing_prefixed}
    desired_set = {(r.value, r.tag) for r in desired}

    to_delete = [r.rule_id for r in existing_prefixed if (r.value, r.tag) not in desired_set]
    to_add = [r for r in desired if (r.value, r.tag) not in existing_set]

    if to_delete:
        x_api_client.delete_stream_rules(to_delete, bearer_token)
    if to_add:
        x_api_client.add_stream_rules(to_add, bearer_token)

    return {"deleted": len([rid for rid in to_delete if rid]), "added": len(to_add)}


def _escape_html(text: str) -> str:
    return html.escape(text or "")


def _format_tweet_message(payload: Dict) -> Optional[str]:
    data = payload.get("data")
    if not data:
        return None
    includes = payload.get("includes") or {}
    users = {u.get("id"): u for u in includes.get("users", [])}
    author_id = data.get("author_id")
    user = users.get(author_id) or {}
    username = user.get("username") or author_id or "unknown"

    text = _escape_html(data.get("text", ""))
    created_at = data.get("created_at", "")
    tweet_id = data.get("id", "")
    url = f"https://x.com/{username}/status/{tweet_id}" if tweet_id else ""

    lines = [
        f"<b>@{_escape_html(username)}</b>",
        text,
    ]
    if created_at:
        lines.append(f"<i>{_escape_html(created_at)}</i>")
    if url:
        lines.append(url)
    return "\n".join(lines)


def _build_action_keyboard(tweet_id: str) -> InlineKeyboardMarkup:
    negative_and_zero = [-5, -4, -3, -2, -1, 0]
    positive = [1, 2, 3, 4, 5]
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(str(score), callback_data=f"tweet_score:{tweet_id}:{score}")
                for score in negative_and_zero
            ],
            [
                InlineKeyboardButton(f"+{score}", callback_data=f"tweet_score:{tweet_id}:{score}")
                for score in positive
            ],
            [
                InlineKeyboardButton("✅ Approve (no score)", callback_data=f"tweet_approve:{tweet_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"tweet_reject:{tweet_id}"),
            ],
        ]
    )


def _format_score(score: int) -> str:
    return f"+{score}" if score > 0 else str(score)


def _format_score_line(score: int) -> str:
    description = SCORE_DESCRIPTIONS[score]
    return f"<i>Score: {_format_score(score)} — {_escape_html(description)}</i>"


def _format_user_label(user) -> str:
    if user is None:
        return "unknown"
    if getattr(user, "username", None):
        return f"@{user.username}"
    return user.full_name or "unknown"


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


def _format_stored_tweet_message(record: dict) -> Optional[str]:
    username = record.get("username") or "unknown"
    text = record.get("text") or ""
    tweet_url = record.get("tweet_url") or ""
    if not text and not tweet_url:
        return None
    lines = [
        f"<b>@{_escape_html(str(username))}</b>",
        _escape_html(str(text)),
    ]
    if tweet_url:
        lines.append(_escape_html(str(tweet_url)))
    return "\n".join(lines)


def _tweet_body_from_record_or_message(record: Optional[dict], query) -> str:
    if record:
        stored_text = _format_stored_tweet_message(record)
        if stored_text:
            return stored_text
    return query.message.text_html or query.message.text or ""


async def _forward_tweet_to_public(
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
            logger.exception("Forwarded tweet but failed to send score reply")

    return forwarded.message_id


async def handle_tweet_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    tweet_id = parts[1]
    score: Optional[int] = None
    if action == "tweet_score":
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
    elif action not in ("tweet_approve", "tweet_reject"):
        await query.answer("Invalid action", show_alert=True)
        return

    label = "rejected" if action == "tweet_reject" else "approved"
    existing = get_tweet_label(tweet_id)
    if not existing:
        logger.error("Tweet label row missing for tweet_id=%s", tweet_id)
        await query.answer("Tweet label row missing", show_alert=True)
        await query.message.edit_text(
            (query.message.text_html or query.message.text or "") + "\n\n<b>Tweet label row missing.</b>",
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

    original_text = _tweet_body_from_record_or_message(existing, query)
    user_label = _escape_html(_format_user_label(query.from_user))
    status_label = label.title()
    if score is not None:
        status_label = f"{status_label} (score={_format_score(score)})"
    status_line = f"\n\n<b>{status_label} by {user_label}</b>"
    new_text = original_text + status_line

    forwarded_id = None
    if label == "approved" and MACRO_PUBLIC_CHAT_ID:
        try:
            forwarded_id = await _forward_tweet_to_public(
                context.bot,
                original_text=original_text,
                score=score,
            )
        except Exception as exc:
            logger.error("Failed to forward tweet to public group after retries: %s", exc)
            fail_text = (
                original_text
                + "\n\n<b>Forward failed (Telegram timeout) - click any score/approve button again to retry.</b>"
            )
            try:
                await query.message.edit_text(fail_text, parse_mode="HTML", reply_markup=query.message.reply_markup)
            except Exception:
                logger.exception("Failed to edit original message after tweet forward failure")
            return

    if not retrying_unforwarded_approval:
        labeled = update_tweet_label(
            tweet_id,
            label,
            query.from_user.id if query.from_user else None,
            score=score,
        )
        if not labeled:
            logger.warning("Tweet label changed before callback commit for tweet_id=%s", tweet_id)
            if forwarded_id:
                mark_forwarded(tweet_id, forwarded_id)
            return

    if forwarded_id:
        mark_forwarded(tweet_id, forwarded_id)

    try:
        await query.message.edit_text(new_text, parse_mode="HTML", reply_markup=None)
    except Exception as exc:
        logger.error("Failed to edit original message: %s", exc)


async def _validate_handles_and_notify(bot: Bot, bearer_token: str) -> None:
    handles = [h.handle for h in list_handles()]
    if not handles:
        return
    _, missing = x_api_client.resolve_usernames(handles, bearer_token)
    if not missing:
        return
    msg = (
        "<b>Missing X handles detected</b>\n"
        + "\n".join(f"- {h}" for h in missing)
    )
    for chat_id in MACRO_CHAT_IDS:
        try:
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
        except Exception as exc:
            logger.error("Failed to send missing-handle alert: %s", exc)


async def cmd_x_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(_format_status(), parse_mode="HTML")


async def cmd_x_handles_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not is_authorized(update.effective_chat.id):
        return
    handles = list_handles()
    if not handles:
        await update.message.reply_text("No handles configured.")
        return
    lines = ["<b>Handles</b>"]
    for h in handles:
        lines.append(f"- @{h.handle} (tier {h.tier}, {h.mode})")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_x_keywords_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not is_authorized(update.effective_chat.id):
        return
    keywords = list_keywords()
    if not keywords:
        await update.message.reply_text("No keywords configured.")
        return
    lines = ["<b>Keywords</b>"] + [f"- {k}" for k in keywords]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_x_follow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /x_follow <handle> [all|gated] [tier]")
        return
    handle = normalize_handle(context.args[0])
    mode = MODE_GATED
    tier = 2
    if len(context.args) >= 2:
        if context.args[1].lower() in (MODE_ALL, MODE_GATED):
            mode = context.args[1].lower()
            tier = 0 if mode == MODE_ALL else 2
    if len(context.args) >= 3:
        try:
            tier = int(context.args[2])
        except ValueError:
            pass
    add_handle(handle, tier=tier, mode=mode)
    await update.message.reply_text(f"Added @{handle} as {mode} (tier {tier}).")


async def cmd_x_unfollow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /x_unfollow <handle>")
        return
    handle = normalize_handle(context.args[0])
    removed = remove_handle(handle)
    await update.message.reply_text("Removed." if removed else "Handle not found.")


async def cmd_x_keywords_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /x_keywords_add <keyword_or_phrase>")
        return
    keyword = " ".join(context.args)
    add_keyword(keyword)
    await update.message.reply_text(f"Added keyword: {keyword}")


async def cmd_x_keywords_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /x_keywords_remove <keyword_or_phrase>")
        return
    keyword = " ".join(context.args)
    removed = remove_keyword(keyword)
    await update.message.reply_text("Removed." if removed else "Keyword not found.")


async def cmd_x_rebuild_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not is_authorized(update.effective_chat.id):
        return
    bearer_token = get_api_key("X_BEARER_TOKEN")
    result = await asyncio.to_thread(reconcile_rules, bearer_token)
    await update.message.reply_text(
        f"Rules reconciled. Added={result['added']} Deleted={result['deleted']}"
    )


async def cmd_x_retry_forwards(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            await update.message.reply_text("Usage: /x_retry_forwards [limit]")
            return

    rows = list_approved_but_unforwarded(limit=limit)
    if not rows:
        await update.message.reply_text("No approved-but-unforwarded X tweets found.")
        return

    forwarded = 0
    failed: List[str] = []
    for row in rows:
        tweet_id = str(row.get("tweet_id") or "")
        original_text = _format_stored_tweet_message(row)
        if not tweet_id or not original_text:
            failed.append(tweet_id or "(missing tweet_id)")
            continue
        try:
            score = row.get("score")
            forwarded_id = await _forward_tweet_to_public(
                context.bot,
                original_text=original_text,
                score=int(score) if score is not None else None,
            )
            if forwarded_id:
                mark_forwarded(tweet_id, forwarded_id)
                forwarded += 1
        except Exception:
            logger.exception("Failed to retry X tweet forward for tweet_id=%s", tweet_id)
            failed.append(tweet_id)

    await update.message.reply_text(
        "Retried X tweet forwards.\n"
        f"- candidates: {len(rows)}\n"
        f"- forwarded: {forwarded}\n"
        f"- still failing: {len(failed)}"
    )


async def stream_worker(bot: Bot, bearer_token: str) -> None:
    params = {
        "tweet.fields": "created_at,author_id,text,lang,conversation_id",
        "expansions": "author_id",
        "user.fields": "id,name,username,verified",
    }
    seen_ids = set()
    seen_queue = deque(maxlen=2000)
    backoff = 1.0

    while True:
        try:
            with x_api_client.open_stream(bearer_token, params=params) as response:
                logger.info("Connected to X filtered stream.")
                backoff = 1.0
                for payload in x_api_client.iter_stream_events(response):
                    data = payload.get("data") or {}
                    tweet_id = data.get("id")
                    if tweet_id:
                        if tweet_id in seen_ids:
                            continue
                        seen_ids.add(tweet_id)
                        seen_queue.append(tweet_id)
                        if len(seen_queue) == seen_queue.maxlen and len(seen_ids) > seen_queue.maxlen:
                            seen_ids = set(seen_queue)

                    message = _format_tweet_message(payload)
                    if not message:
                        continue
                    username = data.get("author_id")
                    includes = payload.get("includes") or {}
                    users = {u.get("id"): u for u in includes.get("users", [])}
                    user = users.get(data.get("author_id")) or {}
                    username = user.get("username") or data.get("author_id")
                    tweet_url = f"https://x.com/{username}/status/{tweet_id}" if tweet_id else None

                    for chat_id in MACRO_CHAT_IDS:
                        try:
                            sent = await bot.send_message(
                                chat_id=chat_id,
                                text=message,
                                parse_mode="HTML",
                                disable_web_page_preview=True,
                                reply_markup=_build_action_keyboard(tweet_id) if tweet_id else None,
                            )
                            if tweet_id:
                                insert_tweet_label(
                                    tweet_id=tweet_id,
                                    username=username,
                                    text=data.get("text"),
                                    tweet_url=tweet_url,
                                    telegram_message_id=sent.message_id,
                                )
                        except Exception as exc:
                            logger.error("Failed to send Telegram message: %s", exc)
        except Exception as exc:
            logger.warning("Stream error: %s", exc)

        sleep_for = min(backoff, 60.0) + random.uniform(0, 0.5)
        logger.info("Reconnecting in %.1fs", sleep_for)
        await asyncio.sleep(sleep_for)
        backoff = min(backoff * 2.0, 60.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="X filtered-stream -> Telegram macro bot.")
    parser.add_argument("--no-validate", action="store_true", help="Skip handle validation on startup.")
    parser.add_argument("--no-reconcile", action="store_true", help="Skip rule reconcile on startup.")
    return parser.parse_args()


async def run_service(args: argparse.Namespace) -> None:
    seed_defaults()
    bearer_token = get_api_key("X_BEARER_TOKEN")
    # Use the consolidated Telegram bot token so callback actions are handled by
    # the same polling bot process in scripts/telegram_bot.py.
    async with Bot(token=BOT_TOKEN) as bot:
        if not args.no_reconcile:
            await asyncio.to_thread(reconcile_rules, bearer_token)
        if not args.no_validate:
            await _validate_handles_and_notify(bot, bearer_token)
        await stream_worker(bot, bearer_token)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()
    asyncio.run(run_service(args))


if __name__ == "__main__":
    main()
