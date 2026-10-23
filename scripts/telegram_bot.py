"""
Telegram Bot for Market Data Orchestrator.

Receives natural language queries via Telegram, runs them through the
OrchestratorAgent, and returns the answer, plots, and JSON output.

Usage:
    1. Set BOT_TOKEN and ALLOWED_CHAT_IDS in telegram_bot_config.py
    2. Run: python scripts/telegram_bot.py
    3. Send queries to your bot on Telegram
"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

FOMC_STEP2_DIR = project_root / "workspace" / "fed" / "fomc conference" / "sentiment_step2"

from telegram import Update, MessageEntity
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest

from src.agents.orchestrator_agent import OrchestratorAgent
from src.core.logging_config import setup_logging
from src.core.telegram_feedback_store import (
    ALLOWED_FEEDBACK_LABELS,
    get_run_id_for_telegram_message,
    insert_telegram_feedback,
    log_telegram_run_message,
    read_orchestrator_output_path,
    read_run_final_answer,
    read_run_query_text,
)
from scripts.telegram_bot_config import (
    BOT_TOKEN,
    ALLOWED_CHAT_IDS,
    MAX_MESSAGE_LENGTH,
    ORCHESTRATOR_TIMEOUT,
    SEND_PLOTS,
    SEND_JSON_OUTPUT,
    PLOTS_DIR,
)
from scripts import x_macro_stream_bot as xmacro
from scripts import bbg_news_stream_bot as bbgnews
from scripts import polymarket_stream_bot as pmmacro

# Setup logging
setup_logging(level=logging.INFO)
logger = logging.getLogger(__name__)

RUN_ID_PATTERN = re.compile(r"\bRun ID:\s*([0-9]{8}_[0-9]{6}_[0-9a-fA-F]{6})\b")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log uncaught Telegram handler errors with the update payload."""
    error = context.error
    exc_info = (type(error), error, error.__traceback__) if error else None
    logger.error("Unhandled Telegram update error: update=%r error=%r", update, error, exc_info=exc_info)


async def send_file_to_chat(chat_id: str, file_path: Path, caption: str = None) -> bool:
    """
    Send a file to a Telegram chat without needing an Update context.
    
    This is useful for automated triggers (e.g., BBG Excel Watcher) that need
    to send files without a user command.
    
    Args:
        chat_id: The Telegram chat ID to send to
        file_path: Path to the file to send
        caption: Optional caption for the file
        
    Returns:
        True if sent successfully, False otherwise
    """
    from telegram import Bot
    
    try:
        bot = Bot(token=BOT_TOKEN)
        
        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            return False
        
        # Generate caption if not provided
        if caption is None:
            caption = f"📊 {file_path.stem}"
        
        async with bot:
            await bot.send_document(
                chat_id=chat_id,
                document=open(file_path, 'rb'),
                filename=file_path.name,
                caption=caption
            )
        
        logger.info(f"Sent file to chat {chat_id}: {file_path.name}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send file to Telegram: {e}")
        return False


async def send_message_to_chat(chat_id: str, message: str) -> bool:
    """
    Send a text message to a Telegram chat without needing an Update context.
    
    Args:
        chat_id: The Telegram chat ID to send to
        message: The text message to send
        
    Returns:
        True if sent successfully, False otherwise
    """
    from telegram import Bot
    
    try:
        bot = Bot(token=BOT_TOKEN)
        
        async with bot:
            await bot.send_message(chat_id=chat_id, text=message)
        
        logger.info(f"Sent message to chat {chat_id}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send message to Telegram: {e}")
        return False


def _extract_run_id_from_result(result: dict) -> str | None:
    """Best-effort run_id extraction across known result shapes."""
    if not isinstance(result, dict):
        return None

    meta = result.get("metadata")
    if isinstance(meta, dict):
        rid = meta.get("run_id")
        if rid:
            return str(rid)
        orchestration = meta.get("orchestration")
        if isinstance(orchestration, dict) and orchestration.get("run_id"):
            return str(orchestration["run_id"])

    data = result.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            first_meta = first.get("metadata")
            if isinstance(first_meta, dict) and first_meta.get("run_id"):
                return str(first_meta["run_id"])

    return None


def _extract_run_id_from_message_text(text: str | None) -> str | None:
    """Parse a run_id from a bot message that includes 'Run ID: ...'."""
    if not text:
        return None
    m = RUN_ID_PATTERN.search(text)
    if not m:
        return None
    return m.group(1)


# Maximum characters for previous answer in follow-up context
MAX_CONTEXT_ANSWER_LENGTH = 500


def _build_followup_context(run_id: str, new_query: str) -> str | None:
    """Build an enriched query with previous conversation context.
    
    Args:
        run_id: The run_id of the previous conversation turn.
        new_query: The new follow-up query from the user.
        
    Returns:
        Enriched query string with context prepended, or None if context
        could not be loaded (caller should use original query).
    """
    prev_query = read_run_query_text(run_id)
    prev_answer = read_run_final_answer(run_id)
    
    if not prev_query and not prev_answer:
        logger.warning("Could not load context for run_id=%s", run_id)
        return None
    
    # Truncate long answers to avoid overwhelming the planner
    if prev_answer and len(prev_answer) > MAX_CONTEXT_ANSWER_LENGTH:
        prev_answer = prev_answer[:MAX_CONTEXT_ANSWER_LENGTH] + "..."
    
    context_parts = ["=== CONVERSATION CONTEXT ==="]
    if prev_query:
        context_parts.append(f"Previous Query: {prev_query}")
    if prev_answer:
        context_parts.append(f"Previous Answer: {prev_answer}")
    context_parts.append("================================")
    context_parts.append("")
    context_parts.append(f"Follow-up Question: {new_query}")
    
    return "\n".join(context_parts)


def is_authorized(chat_id: int) -> bool:
    """Check if the chat ID is authorized to use the bot."""
    if not ALLOWED_CHAT_IDS or ALLOWED_CHAT_IDS == [0]:
        logger.warning("ALLOWED_CHAT_IDS not configured - rejecting all requests")
        return False
    return chat_id in ALLOWED_CHAT_IDS


def split_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list:
    """Split a long message into chunks that fit Telegram's limit."""
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        
        # Find a good split point (newline or space)
        split_idx = text.rfind('\n', 0, max_length)
        if split_idx == -1:
            split_idx = text.rfind(' ', 0, max_length)
        if split_idx == -1:
            split_idx = max_length
        
        chunks.append(text[:split_idx])
        text = text[split_idx:].lstrip()
    
    return chunks


def get_new_plots(before_time: datetime, plots_dir: Path) -> list:
    """Get plot files created after a given time (PNG and SVG)."""
    new_plots = []
    
    if not plots_dir.exists():
        return new_plots
    
    # Support both PNG and SVG formats
    for pattern in ["*.png", "*.svg"]:
        for plot_file in plots_dir.glob(pattern):
            if plot_file.stat().st_mtime > before_time.timestamp():
                new_plots.append(plot_file)
    
    return new_plots


def _load_fomc_step2_result(date_str: str) -> dict | None:
    """Load Step 2 sentiment result JSON for a given date (YYYY-MM-DD)."""
    if not FOMC_STEP2_DIR.exists():
        return None

    matches = sorted(FOMC_STEP2_DIR.glob(f"{date_str}_*.json"))
    if not matches:
        return None

    with open(matches[0], "r", encoding="utf-8") as f:
        return json.load(f)


def _format_fomc_alerts(result: dict, max_alerts: int) -> str:
    """Format Step 2 alerts into a Telegram-friendly message."""
    date = result.get("date", "unknown")
    alerts = [c for c in result.get("chunks", []) if c.get("alert")]

    if not alerts:
        return f"FOMC Sentiment Alerts ({date})\nNo alerts (no extremes or meaningful changes)."

    lines = [f"FOMC Sentiment Alerts ({date})", ""]
    for chunk in alerts[:max_alerts]:
        minute = int((chunk.get("start_s", 0.0) or 0.0) // 60)
        label = (chunk.get("calibrated_label") or "unknown").upper()
        delta = chunk.get("price_delta_ticks")
        reason = chunk.get("alert_reason") or "signal"
        text = (chunk.get("text") or "").replace("\n", " ")
        snippet = text[:90] + ("..." if len(text) > 90 else "")

        delta_str = f"{delta:+.2f}t" if isinstance(delta, (int, float)) else "N/A"
        lines.append(f"{minute:>3}m | {label:<13} | {delta_str:>8} | {reason}")
        if snippet:
            lines.append(f"  {snippet}")

    if len(alerts) > max_alerts:
        lines.append("")
        lines.append(f"... and {len(alerts) - max_alerts} more alerts")

    return "\n".join(lines)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not is_authorized(update.effective_chat.id):
        await update.message.reply_text("Unauthorized. This bot is private.")
        return
    
    await update.message.reply_text(
        "Market Data Orchestrator Bot\n\n"
        "Send me a natural language query about market data, events, or analytics.\n\n"
        "Examples:\n"
        "- Give me the event distribution one hour post NFP release\n"
        "- What is the volatility distribution for TY futures?\n"
        "- Show me JOLTS Job Openings surprise correlation with price\n\n"
        "I'll run the analysis and send you the results, plots, and data."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    if not is_authorized(update.effective_chat.id):
        await update.message.reply_text(
            f"Unauthorized.\nYour Chat ID: {update.effective_chat.id}"
        )
        return
    
    bot_username = context.bot.username or "bot"
    await update.message.reply_text(
        "Commands:\n"
        "/start - Welcome message\n"
        "/help - Show this help\n"
        "/data - Data sources & knowledge cutoff\n"
        "/status - Check bot status\n"
        "/query <question> - Run an analysis query\n"
        "/pre_event <event> [consensus] [cons_std] - Pre-event B/E analysis\n"
        "/post_event <event> [actual] [consensus] [cons_std] - Post-event B/E analysis\n"
        "/live_pre_event <cons_std> <fwd_be> - Start LIVE dashboard (local network)\n"
        "/live_pre_event_stop - Stop live dashboard\n"
        "/fomc_alerts <YYYY-MM-DD> [max_alerts] - FOMC sentiment alerts (Step 2)\n"
        "/feedback <label> <optional comment> - Reply to a bot answer and submit feedback\n\n"
        "Date Tagging:\n"
        "/tag - Show existing tags, then add a new one\n"
        "/tags [date] [#hashtag] - View tags\n"
        "/tagstats - Tag statistics by hashtag\n"
        "/untag <id> - Delete a tag\n\n"
        "Tag examples:\n"
        "  /tag → shows existing tags\n"
        "  /tag jan 10 8:30am #nfp NFP beat, TY sold off\n"
        "  /tags #nfp → filter by hashtag\n"
        "  /tags jan 10 → filter by date\n\n"
        "Pre-event examples:\n"
        "  /pre_event NFP\n"
        "  /pre_event NFP 180 25\n\n"
        "Post-event examples:\n"
        "  /post_event NFP\n"
        "  /post_event NFP 256 165 25\n\n"
        "Live dashboard example:\n"
        "  /live_pre_event 25 5.0  → Returns network URL (same WiFi)\n\n"
        f"In groups, use:\n"
        f"  /query <your question>\n"
        f"  @{bot_username} <your question>\n\n"
        "In DMs, just send any text message.\n\n"
        "Feedback labels:\n"
        f"  {', '.join(ALLOWED_FEEDBACK_LABELS)}\n\n"
        "X Macro Stream Commands (integrated in this bot):\n"
        "  /x_status, /x_follow, /x_unfollow,\n"
        "  /x_keywords_add, /x_keywords_remove,\n"
        "  /x_keywords_list, /x_handles_list, /x_rebuild_rules\n\n"
        "Polymarket Tracking Commands:\n"
        "  /pm_track <query>, /pm_list, /pm_untrack <index>"
    )


def _get_data_coverage() -> dict:
    """Query databases to get actual data coverage dates and statistics."""
    import sqlite3
    
    result = {
        "market_data": {"min": None, "max": None, "symbols": [], "row_count": 0, "error": None},
        "economic_events": {"min": None, "max": None, "event_types": [], "row_count": 0, "error": None},
    }
    
    # Query PostgreSQL for market data coverage
    try:
        import psycopg2
        from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD
        
        conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            database=PG_DATABASE,
            user=PG_USER,
            password=PG_PASSWORD,
        )
        cursor = conn.cursor()
        
        # Get date range
        cursor.execute("SELECT MIN(ts_event), MAX(ts_event) FROM ohlcv_1m")
        row = cursor.fetchone()
        if row and row[0] and row[1]:
            result["market_data"]["min"] = row[0].strftime("%b %Y")
            result["market_data"]["max"] = row[1].strftime("%b %Y")
        
        # Get unique symbols (base symbols like TY, ES, etc.)
        cursor.execute("""
            SELECT DISTINCT SUBSTRING(symbol FROM 1 FOR 2) as base_symbol 
            FROM ohlcv_1m 
            ORDER BY base_symbol
        """)
        symbols = [r[0] for r in cursor.fetchall() if r[0]]
        result["market_data"]["symbols"] = symbols[:10]  # Limit to 10
        
        # Get approximate row count
        cursor.execute("SELECT reltuples::bigint FROM pg_class WHERE relname = 'ohlcv_1m'")
        row = cursor.fetchone()
        if row:
            result["market_data"]["row_count"] = int(row[0])
        
        cursor.close()
        conn.close()
    except Exception as e:
        result["market_data"]["error"] = str(e)
        logger.warning(f"Failed to query PostgreSQL for data coverage: {e}")
    
    # Query SQLite for economic events coverage
    try:
        db_path = project_root / "workspace" / "economic_events.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            
            # Get date range
            cursor.execute("SELECT MIN(event_date), MAX(event_date) FROM economic_events")
            row = cursor.fetchone()
            if row and row[0] and row[1]:
                min_date = datetime.fromisoformat(row[0][:10])
                max_date = datetime.fromisoformat(row[1][:10])
                result["economic_events"]["min"] = min_date.strftime("%b %Y")
                result["economic_events"]["max"] = max_date.strftime("%b %Y")
            
            # Get key macro events with counts
            # Use exact names or patterns that match US macro data
            key_event_patterns = [
                'Non Farm Payrolls',  # NFP
                'CPI',
                'Inflation Rate',
                'JOLTs',
                'JOLTS',
                'FOMC',
                'Fed Interest Rate',
                'Retail Sales',
                'GDP Growth',
                'GDP Price Index',
                'PPI',
                'ISM Manufacturing PMI',
                'ISM Services PMI',
                'Unemployment Rate',
            ]
            key_events_found = []
            seen_names = set()
            for ke in key_event_patterns:
                cursor.execute(
                    "SELECT event_name, COUNT(*) as cnt FROM economic_events WHERE event_name LIKE ? GROUP BY event_name ORDER BY cnt DESC LIMIT 3",
                    (f"%{ke}%",)
                )
                rows = cursor.fetchall()
                for r in rows:
                    if r[0] and r[0] not in seen_names:
                        key_events_found.append((r[0], r[1]))
                        seen_names.add(r[0])
            
            # Sort by count descending
            key_events_found.sort(key=lambda x: -x[1])
            result["economic_events"]["key_events"] = key_events_found[:12]
            
            # Get total unique event types count
            cursor.execute("SELECT COUNT(DISTINCT event_name) FROM economic_events")
            row = cursor.fetchone()
            result["economic_events"]["total_event_types"] = row[0] if row else 0
            
            # Get row count
            cursor.execute("SELECT COUNT(*) FROM economic_events")
            row = cursor.fetchone()
            if row:
                result["economic_events"]["row_count"] = row[0]
            
            cursor.close()
            conn.close()
        else:
            result["economic_events"]["error"] = "Database not found"
    except Exception as e:
        result["economic_events"]["error"] = str(e)
        logger.warning(f"Failed to query economic_events.db for data coverage: {e}")
    
    return result


async def data_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /data command - show data sources and knowledge cutoff."""
    if not is_authorized(update.effective_chat.id):
        await update.message.reply_text(
            f"Unauthorized.\nYour Chat ID: {update.effective_chat.id}"
        )
        return
    
    # Get actual data coverage from databases
    loop = asyncio.get_event_loop()
    coverage = await loop.run_in_executor(None, _get_data_coverage)
    
    # Format market data section
    md = coverage["market_data"]
    if md["min"] and md["max"]:
        market_range = f"{md['min']} - {md['max']}"
    elif md["error"]:
        market_range = f"(unavailable: {md['error'][:30]})"
    else:
        market_range = "(unknown)"
    
    symbols_str = ", ".join(md.get("symbols", [])) or "None found"
    market_rows = md.get("row_count", 0)
    market_rows_str = f"{market_rows:,}" if market_rows else "Unknown"
    
    # Format events section
    ev = coverage["economic_events"]
    if ev["min"] and ev["max"]:
        events_range = f"{ev['min']} - {ev['max']}"
    elif ev["error"]:
        events_range = f"(unavailable: {ev['error'][:30]})"
    else:
        events_range = "(unknown)"
    
    key_events = ev.get("key_events", [])
    total_event_types = ev.get("total_event_types", 0)
    events_rows = ev.get("row_count", 0)
    events_rows_str = f"{events_rows:,}" if events_rows else "Unknown"
    
    # Format key events with counts
    if key_events:
        event_list = ", ".join(f"{name}" for name, cnt in key_events[:8])
        if total_event_types > 8:
            event_list += f" (+{total_event_types - 8} more)"
    else:
        event_list = "None found"
    
    msg = (
        "DATA SOURCES\n\n"
        
        "MARKET DATA (PostgreSQL)\n"
        f"  Symbols: {symbols_str}\n"
        f"  Date Range: {market_range}\n"
        f"  Records: {market_rows_str}\n"
        f"  Resolution: 1-minute OHLCV bars\n\n"
        
        "ECONOMIC EVENTS (SQLite)\n"
        f"  Events: {event_list}\n"
        f"  Date Range: {events_range}\n"
        f"  Records: {events_rows_str}\n"
        f"  Fields: actual, forecast, previous\n\n"
        
        "POLYMARKET (Real-time API)\n"
        f"  Current prices + 90-day history\n\n"
        
        "COMMANDS\n"
        "  /query <question> - Run analysis\n"
        "  /pre_event <event> - Pre-event B/E analysis\n"
        "  /post_event <event> - Post-event B/E analysis\n"
        "  /live_pre_event <cons_std> <fwd_be> - LIVE dashboard (local network)\n\n"
        
        "EXAMPLE QUERIES\n"
        "  'Show TY chart on last NFP day'\n"
        "  'What was TY return after Dec CPI?'\n"
        "  'Plot surprise distribution for JOLTS'"
    )
    await update.message.reply_text(msg)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    if not is_authorized(update.effective_chat.id):
        await update.message.reply_text(
            f"Unauthorized.\n"
            f"Your Chat ID: {update.effective_chat.id}\n"
            f"Authorized Chat IDs: {ALLOWED_CHAT_IDS}"
        )
        return
    
    await update.message.reply_text(
        "Bot Status: Online\n"
        f"Authorized Chat IDs: {ALLOWED_CHAT_IDS}\n"
        f"Your Chat ID: {update.effective_chat.id}\n"
        f"Orchestrator Timeout: {ORCHESTRATOR_TIMEOUT}s"
    )


async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /feedback command.

    Usage (reply-to required):
      /feedback <label> <optional comment...>
    """
    chat_id = update.effective_chat.id

    if not is_authorized(chat_id):
        await update.message.reply_text(
            f"Unauthorized.\nYour Chat ID: {chat_id}"
        )
        return

    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text(
            "To submit feedback, **reply to a bot answer message** and send:\n"
            "/feedback <label> <optional comment>\n\n"
            f"Allowed labels: {', '.join(ALLOWED_FEEDBACK_LABELS)}",
            parse_mode="Markdown",
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "/feedback <label> <optional comment>\n\n"
            f"Allowed labels: {', '.join(ALLOWED_FEEDBACK_LABELS)}"
        )
        return

    label = (context.args[0] or "").strip().lower()
    comment = " ".join(context.args[1:]).strip() if len(context.args) > 1 else ""
    comment = comment or None

    if label not in set(ALLOWED_FEEDBACK_LABELS):
        await update.message.reply_text(
            "Invalid label.\n\n"
            "Usage:\n"
            "/feedback <label> <optional comment>\n\n"
            f"Allowed labels: {', '.join(ALLOWED_FEEDBACK_LABELS)}"
        )
        return

    reply_to = update.message.reply_to_message
    reply_to_message_id = reply_to.message_id

    run_id = get_run_id_for_telegram_message(
        chat_id=chat_id,
        message_id=reply_to_message_id,
    )
    if not run_id:
        # Fallback: if the user replied to the bot answer chunk that contains
        # "Run ID: ...", parse it directly so feedback still works even if
        # message→run mapping wasn't logged for some reason.
        run_id = _extract_run_id_from_message_text(reply_to.text or reply_to.caption)
        if run_id:
            try:
                log_telegram_run_message(
                    run_id=str(run_id),
                    chat_id=int(chat_id),
                    message_id=int(reply_to_message_id),
                    message_kind="answer_chunk",
                )
            except Exception as e:
                logger.warning(f"Failed to backfill telegram_run_message: {e}")
        else:
            await update.message.reply_text(
                "I couldn't find the run associated with that message.\n\n"
                "Please reply to the bot's *answer text* message that includes the line:\n"
                "`Run ID: ...`\n\n"
                "Tip: don't reply to the 'Processing your query...' message."
            )
            return

    query_text = read_run_query_text(run_id) or "(unknown)"
    answer_text = (reply_to.text or reply_to.caption or "").strip() or "(no text)"
    output_path = read_orchestrator_output_path(run_id)

    from_user = update.effective_user
    from_user_id = from_user.id if from_user else 0
    from_username = from_user.username if from_user else None

    feedback_id = insert_telegram_feedback(
        run_id=run_id,
        chat_id=chat_id,
        from_user_id=from_user_id,
        from_username=from_username,
        reply_to_message_id=reply_to_message_id,
        label=label,
        comment=comment,
        query_text=query_text,
        answer_text=answer_text,
        output_path=output_path,
    )

    await update.message.reply_text(
        f"Feedback saved.\n"
        f"- id: {feedback_id}\n"
        f"- run_id: {run_id}\n"
        f"- label: {label}"
    )


async def query_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /query command - main way to run queries in groups."""
    chat_id = update.effective_chat.id
    
    # Authorization check
    if not is_authorized(chat_id):
        logger.warning(f"Unauthorized access attempt from chat_id: {chat_id}")
        await update.message.reply_text(
            f"Unauthorized.\nYour Chat ID: {chat_id}"
        )
        return
    
    # Get query from command arguments
    if not context.args:
        await update.message.reply_text(
            "Usage: /query <your question>\n\n"
            "Example:\n"
            "/query Give me the event distribution one hour post NFP release\n\n"
            "Tip: Reply to a previous bot answer with /query to ask a follow-up!"
        )
        return
    
    query = ' '.join(context.args)
    
    # Check if this is a reply to a previous bot message (follow-up query)
    reply_to = update.message.reply_to_message
    if reply_to:
        # Try to find the run_id for the replied-to message
        run_id = get_run_id_for_telegram_message(
            chat_id=chat_id,
            message_id=reply_to.message_id,
        )
        if not run_id:
            # Fallback: parse run_id from message text if present
            run_id = _extract_run_id_from_message_text(reply_to.text or reply_to.caption)
        
        if run_id:
            # Build enriched query with conversation context
            enriched_query = _build_followup_context(run_id, query)
            if enriched_query:
                logger.info(f"Follow-up /query detected for run_id={run_id}")
                query = enriched_query
    
    # Process the query (reuse handle_query logic)
    await _process_query(update, query)


async def _process_query(update: Update, query: str) -> None:
    """Process a query and send results. Shared by handle_query and query_command."""
    chat_id = update.effective_chat.id
    
    logger.info(f"Received query from {chat_id}: {query[:100]}...")
    
    # Send processing message
    processing_msg = await update.message.reply_text(
        f"Processing your query...\n\n"
        f"Query: {query[:200]}{'...' if len(query) > 200 else ''}\n\n"
        f"This may take 1-2 minutes."
    )
    
    # Track time for finding new plots
    start_time = datetime.now()
    plots_dir = project_root / PLOTS_DIR
    
    try:
        # Initialize and run orchestrator
        agent = OrchestratorAgent()
        
        # Run orchestration in executor to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: agent.run(query=query, skip_validation=True)
        )
        
        # Extract answer
        answer = result.get('answer', 'No answer generated.')
        metadata = result.get('metadata', {})
        output_path = result.get('output_path')
        run_id = _extract_run_id_from_result(result)
        
        # Build response text
        response_parts = []
        response_parts.append(f"Query: {query}\n")
        response_parts.append("-" * 40)
        response_parts.append(f"\n{answer}\n")
        response_parts.append("-" * 40)
        response_parts.append(f"\nRun ID: {run_id or 'N/A'}")
        response_parts.append(f"Duration: {metadata.get('duration_ms', 0):.1f}ms")
        response_parts.append(f"Tasks: {metadata.get('successful_tasks', 0)}/{metadata.get('total_tasks', 0)} successful")
        
        response_text = '\n'.join(response_parts)
        
        # Delete processing message
        await processing_msg.delete()
        
        # Send answer (split if too long)
        for chunk in split_message(response_text):
            sent_msg = await update.message.reply_text(chunk)
            if run_id:
                try:
                    log_telegram_run_message(
                        run_id=str(run_id),
                        chat_id=int(chat_id),
                        message_id=int(sent_msg.message_id),
                        message_kind="answer_chunk",
                    )
                except Exception as e:
                    logger.warning(f"Failed to log telegram_run_message: {e}")
        
        # Send plots if enabled
        if SEND_PLOTS:
            new_plots = get_new_plots(start_time, plots_dir)
            
            # Also check for plots in result metadata (plot_paths, svg_path)
            result_plots = []
            for worker in result.get('worker_outputs', []):
                output_data = worker.get('output_data', {})
                if isinstance(output_data, dict):
                    for tool_result in output_data.values():
                        if isinstance(tool_result, dict):
                            # Check for plot_paths array
                            paths = tool_result.get('plot_paths', [])
                            result_plots.extend(paths)
                            # Check for svg_path (from generate_line_chart, etc.)
                            svg_path = tool_result.get('svg_path')
                            if svg_path:
                                result_plots.append(svg_path)
            
            # Combine and deduplicate
            all_plots = set()
            for p in new_plots:
                all_plots.add(str(p))
            for p in result_plots:
                if Path(p).exists():
                    all_plots.add(p)
            
            # Send each plot (PNG as photo, SVG as document)
            for plot_path in sorted(all_plots):
                plot_file = Path(plot_path)
                if not plot_file.exists():
                    continue
                
                suffix = plot_file.suffix.lower()
                try:
                    if suffix == '.png':
                        # Send PNG as inline photo
                        sent_plot = await update.message.reply_photo(
                            photo=open(plot_file, 'rb'),
                            caption=plot_file.name
                        )
                        logger.info(f"Sent plot (photo): {plot_file.name}")
                        if run_id:
                            try:
                                log_telegram_run_message(
                                    run_id=str(run_id),
                                    chat_id=int(chat_id),
                                    message_id=int(sent_plot.message_id),
                                    message_kind="plot_png",
                                )
                            except Exception as e:
                                logger.warning(f"Failed to log telegram_run_message: {e}")
                    elif suffix == '.svg':
                        # Send SVG as document (Telegram doesn't display SVG inline)
                        sent_plot = await update.message.reply_document(
                            document=open(plot_file, 'rb'),
                            filename=plot_file.name,
                            caption=f"📊 Chart: {plot_file.stem}"
                        )
                        logger.info(f"Sent plot (document): {plot_file.name}")
                        if run_id:
                            try:
                                log_telegram_run_message(
                                    run_id=str(run_id),
                                    chat_id=int(chat_id),
                                    message_id=int(sent_plot.message_id),
                                    message_kind="plot_svg",
                                )
                            except Exception as e:
                                logger.warning(f"Failed to log telegram_run_message: {e}")
                except Exception as e:
                    logger.error(f"Failed to send plot {plot_file}: {e}")
        
        # Send JSON output if enabled
        if SEND_JSON_OUTPUT and output_path:
            output_file = Path(output_path)
            if output_file.exists():
                try:
                    sent_json = await update.message.reply_document(
                        document=open(output_file, 'rb'),
                        filename=output_file.name,
                        caption="Full output data (JSON)"
                    )
                    logger.info(f"Sent JSON output: {output_file.name}")
                    if run_id:
                        try:
                            log_telegram_run_message(
                                run_id=str(run_id),
                                chat_id=int(chat_id),
                                message_id=int(sent_json.message_id),
                                message_kind="json_output",
                            )
                        except Exception as e:
                            logger.warning(f"Failed to log telegram_run_message: {e}")
                except Exception as e:
                    logger.error(f"Failed to send JSON output: {e}")
        
        logger.info(f"Query completed successfully for {chat_id}")
        
    except Exception as e:
        logger.error(f"Query failed: {e}", exc_info=True)
        
        # Delete processing message
        try:
            await processing_msg.delete()
        except:
            pass
        
        await update.message.reply_text(
            f"Error processing query:\n\n{str(e)}\n\n"
            "Please try again or check the logs."
        )


def is_bot_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the bot is @mentioned in the message using entities."""
    message = update.message
    if not message or not message.entities:
        return False
    
    bot_username = context.bot.username
    if not bot_username:
        return False
    
    for entity in message.entities:
        if entity.type == MessageEntity.MENTION:
            # Extract the mentioned username from the message text
            mention = message.text[entity.offset:entity.offset + entity.length]
            if mention.lower() == f"@{bot_username.lower()}":
                return True
    return False


async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages as orchestrator queries."""
    chat_id = update.effective_chat.id
    message_text = update.message.text.strip()
    
    if not message_text:
        return
    
    # Check if this is a group chat
    is_group = update.effective_chat.type in ["group", "supergroup"]
    
    # In groups, only respond if bot is @mentioned
    if is_group:
        bot_username = context.bot.username
        # Check using both entity detection and text search for reliability
        bot_mentioned = is_bot_mentioned(update, context)
        text_has_mention = bot_username and f"@{bot_username}" in message_text
        
        if bot_mentioned or text_has_mention:
            # Remove the @mention from the query
            if bot_username:
                query = message_text.replace(f"@{bot_username}", "").strip()
            else:
                query = message_text
        else:
            # Bot not mentioned, ignore
            return
    else:
        # DM - use full message as query
        query = message_text
    
    # Authorization check
    if not is_authorized(chat_id):
        logger.warning(f"Unauthorized access attempt from chat_id: {chat_id}")
        await update.message.reply_text(f"Unauthorized.\nYour Chat ID: {chat_id}")
        return
    
    if not query:
        await update.message.reply_text("Please include a query after mentioning me.")
        return
    
    # Check if this is a reply to a previous bot message (follow-up query)
    reply_to = update.message.reply_to_message
    if reply_to:
        # Try to find the run_id for the replied-to message
        run_id = get_run_id_for_telegram_message(
            chat_id=chat_id,
            message_id=reply_to.message_id,
        )
        if not run_id:
            # Fallback: parse run_id from message text if present
            run_id = _extract_run_id_from_message_text(reply_to.text or reply_to.caption)
        
        if run_id:
            # Build enriched query with conversation context
            enriched_query = _build_followup_context(run_id, query)
            if enriched_query:
                logger.info(f"Follow-up query detected for run_id={run_id}")
                query = enriched_query
    
    await _process_query(update, query)


async def pre_event_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /pre_event command - generate pre-event analysis dashboard.
    
    Usage:
        /pre_event NFP
        /pre_event NFP 180  (with consensus)
        /pre_event NFP 180 25  (with consensus and std dev)
    """
    chat_id = update.effective_chat.id
    
    # Authorization check
    if not is_authorized(chat_id):
        logger.warning(f"Unauthorized /pre_event attempt from chat_id: {chat_id}")
        await update.message.reply_text(f"Unauthorized.\nYour Chat ID: {chat_id}")
        return
    
    # Parse arguments
    if not context.args:
        await update.message.reply_text(
            "Usage: /pre_event <event_name> [consensus] [cons_std]\n\n"
            "Examples:\n"
            "  /pre_event NFP\n"
            "  /pre_event NFP 180\n"
            "  /pre_event NFP 180 25\n\n"
            "This generates an interactive dashboard for pre-event B/E analysis.\n\n"
            "Features:\n"
            "• Box plots of B/E by consensus std dev buckets\n"
            "• Adjustable end time (3pm → 8:30am)\n"
            "• Price paths for events in your bucket\n"
            "• Click events to see details"
        )
        return
    
    # Parse: event_name [consensus] [cons_std]
    event_name = context.args[0].upper()
    consensus = None
    cons_std = None
    
    if len(context.args) >= 2:
        try:
            consensus = float(context.args[1])
        except ValueError:
            pass
    
    if len(context.args) >= 3:
        try:
            cons_std = float(context.args[2])
        except ValueError:
            pass
    
    logger.info(f"Pre-event request from {chat_id}: event='{event_name}', consensus={consensus}, cons_std={cons_std}")
    
    # Send processing message
    processing_msg = await update.message.reply_text(
        f"📊 Building pre-event analysis dashboard...\n\n"
        f"Event: {event_name}\n"
        f"Consensus: {consensus or 'Not set'}\n"
        f"Cons Std Dev: {cons_std or 'Not set'}\n\n"
        f"Fetching price data for all historical events...\n"
        f"This may take 1-2 minutes."
    )
    
    try:
        # Import dashboard builder
        from scripts.preevent_boxplot_dashboard import build_preevent_dashboard
        
        # Run dashboard builder in executor to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: build_preevent_dashboard(
                event_name=event_name,
                consensus=consensus,
                cons_std=cons_std,
            )
        )
        
        # Delete processing message
        await processing_msg.delete()
        
        if not result.get("success"):
            await update.message.reply_text(
                f"❌ Could not build dashboard:\n\n{result.get('error', 'Unknown error')}\n\n"
                f"Tips:\n"
                f"• Currently only NFP is supported\n"
                f"• Check the event name spelling"
            )
            return
        
        # Send success message
        html_path = result.get("html_path")
        event_count = result.get("event_count", 0)
        cons_std_range = result.get("cons_std_range", "N/A")
        
        summary = (
            f"📊 Pre-Event Analysis Dashboard Ready\n\n"
            f"Event: {result.get('event_name')}\n"
            f"Historical Events: {event_count}\n"
            f"Cons Std Dev Range: {cons_std_range}\n\n"
            f"💡 Open the HTML file in a browser to:\n"
            f"• Set your consensus std dev value\n"
            f"• Adjust the end time (3pm → 8:30am)\n"
            f"• Configure bucket width\n"
            f"• View B/E distribution by bucket\n"
            f"• Click on price paths to see event details"
        )
        
        await update.message.reply_text(summary)
        
        # Send HTML file
        if html_path and Path(html_path).exists():
            await update.message.reply_document(
                document=open(html_path, 'rb'),
                filename=Path(html_path).name,
                caption=f"🎯 Pre-Event B/E Analysis - {result.get('event_name')}"
            )
            logger.info(f"Sent pre-event dashboard: {html_path}")
        
    except Exception as e:
        logger.error(f"Pre-event command failed: {e}", exc_info=True)
        
        # Delete processing message
        try:
            await processing_msg.delete()
        except:
            pass
        
        await update.message.reply_text(
            f"❌ Error building dashboard:\n\n{str(e)}\n\n"
            f"Please try again or check the logs."
        )


async def fomc_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /fomc_alerts command - send Step 2 sentiment alerts for a date."""
    chat_id = update.effective_chat.id

    if not is_authorized(chat_id):
        await update.message.reply_text(f"Unauthorized.\nYour Chat ID: {chat_id}")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /fomc_alerts <YYYY-MM-DD> [max_alerts]\n"
            "Example: /fomc_alerts 2024-03-20 10"
        )
        return

    date_str = context.args[0]
    max_alerts = 12
    if len(context.args) > 1:
        try:
            max_alerts = int(context.args[1])
        except ValueError:
            await update.message.reply_text("Invalid max_alerts value. Must be an integer.")
            return

    result = _load_fomc_step2_result(date_str)
    if not result:
        await update.message.reply_text(
            f"No Step 2 sentiment file found for {date_str}.\n"
            "Run: python scripts/fomc_press_conference_sentiment_step2.py"
        )
        return

    message = _format_fomc_alerts(result, max_alerts=max_alerts)
    for chunk in split_message(message):
        await update.message.reply_text(chunk)


async def post_event_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /post_event command - generate post-event analysis dashboard.
    
    Usage:
        /post_event NFP
        /post_event NFP 256  (with actual)
        /post_event NFP 256 165  (with actual and consensus)
        /post_event NFP 256 165 25  (with actual, consensus, and cons_std)
    """
    chat_id = update.effective_chat.id
    
    # Authorization check
    if not is_authorized(chat_id):
        logger.warning(f"Unauthorized /post_event attempt from chat_id: {chat_id}")
        await update.message.reply_text(f"Unauthorized.\nYour Chat ID: {chat_id}")
        return
    
    # Parse arguments
    if not context.args:
        await update.message.reply_text(
            "Usage: /post_event <event_name> [actual] [consensus] [cons_std]\n\n"
            "Examples:\n"
            "  /post_event NFP\n"
            "  /post_event NFP 256\n"
            "  /post_event NFP 256 165\n"
            "  /post_event NFP 256 165 25\n\n"
            "This generates an interactive dashboard for post-event B/E analysis.\n\n"
            "Features:\n"
            "• Box plots of B/E by surprise percentile buckets\n"
            "• Adjustable end time (8:30am → 3pm)\n"
            "• Price paths for events in your bucket\n"
            "• Click events to see details\n\n"
            "Standardized Surprise = (Actual - Consensus) / Cons_Std_Dev\n"
            "The dashboard calculates your percentile vs history."
        )
        return
    
    # Parse: event_name [actual] [consensus] [cons_std]
    event_name = context.args[0].upper()
    actual = None
    consensus = None
    cons_std = None
    
    if len(context.args) >= 2:
        try:
            actual = float(context.args[1])
        except ValueError:
            pass
    
    if len(context.args) >= 3:
        try:
            consensus = float(context.args[2])
        except ValueError:
            pass
    
    if len(context.args) >= 4:
        try:
            cons_std = float(context.args[3])
        except ValueError:
            pass
    
    logger.info(f"Post-event request from {chat_id}: event='{event_name}', actual={actual}, consensus={consensus}, cons_std={cons_std}")
    
    # Send processing message
    processing_msg = await update.message.reply_text(
        f"📊 Building post-event analysis dashboard...\n\n"
        f"Event: {event_name}\n"
        f"Actual: {actual or 'Not set'}\n"
        f"Consensus: {consensus or 'Not set'}\n"
        f"Cons Std Dev: {cons_std or 'Not set (will use default)'}\n\n"
        f"Fetching post-event price data (8:30am → 3pm)...\n"
        f"This may take 1-2 minutes."
    )
    
    try:
        # Import dashboard builder
        from scripts.postevent_boxplot_dashboard import build_postevent_dashboard
        
        # Run dashboard builder in executor to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: build_postevent_dashboard(
                event_name=event_name,
                actual=actual,
                consensus=consensus,
                cons_std=cons_std,
            )
        )
        
        # Delete processing message
        await processing_msg.delete()
        
        if not result.get("success"):
            await update.message.reply_text(
                f"❌ Could not build dashboard:\n\n{result.get('error', 'Unknown error')}\n\n"
                f"Tips:\n"
                f"• Currently only NFP is supported\n"
                f"• Check the event name spelling"
            )
            return
        
        # Send success message
        html_path = result.get("html_path")
        event_count = result.get("event_count", 0)
        surprise_range = result.get("surprise_range", "N/A")
        user_surprise = result.get("user_surprise")
        user_surp_pct = result.get("user_surp_pct")
        
        summary = (
            f"📊 Post-Event Analysis Dashboard Ready\n\n"
            f"Event: {result.get('event_name')}\n"
            f"Historical Events: {event_count}\n"
            f"Surprise Range: {surprise_range}\n"
        )
        
        if user_surprise is not None and user_surp_pct is not None:
            summary += (
                f"\nYour Input:\n"
                f"  Surprise: {user_surprise:.0f}K\n"
                f"  Percentile: {user_surp_pct*100:.1f}%\n"
            )
        
        summary += (
            f"\n💡 Open the HTML file in a browser to:\n"
            f"• Enter actual and consensus values\n"
            f"• Adjust the end time (8:30am → 3pm)\n"
            f"• Configure bucket width\n"
            f"• View B/E distribution by surprise percentile\n"
            f"• Click on price paths to see event details"
        )
        
        await update.message.reply_text(summary)
        
        # Send HTML file
        if html_path and Path(html_path).exists():
            await update.message.reply_document(
                document=open(html_path, 'rb'),
                filename=Path(html_path).name,
                caption=f"🎯 Post-Event B/E Analysis - {result.get('event_name')}"
            )
            logger.info(f"Sent post-event dashboard: {html_path}")
        
    except Exception as e:
        logger.error(f"Post-event command failed: {e}", exc_info=True)
        
        # Delete processing message
        try:
            await processing_msg.delete()
        except:
            pass
        
        await update.message.reply_text(
            f"❌ Error building dashboard:\n\n{str(e)}\n\n"
            f"Please try again or check the logs."
        )


async def live_pre_event_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /live_pre_event command - start live pre-event dashboard on local network.
    
    Usage:
        /live_pre_event <cons_std> <fwd_be>
        
    Example:
        /live_pre_event 25 5.0
        
    Returns a local network URL that anyone on the same network can access.
    """
    chat_id = update.effective_chat.id
    
    # Authorization check
    if not is_authorized(chat_id):
        logger.warning(f"Unauthorized /live_pre_event attempt from chat_id: {chat_id}")
        await update.message.reply_text(f"Unauthorized.\nYour Chat ID: {chat_id}")
        return
    
    # Parse arguments
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /live_pre_event <cons_std> <fwd_be>\n\n"
            "Parameters:\n"
            "  cons_std - Consensus standard deviation (e.g., 25)\n"
            "  fwd_be   - Forward breakeven in bps (e.g., 5.0)\n\n"
            "Example:\n"
            "  /live_pre_event 25 5.0\n\n"
            "This starts a LIVE dashboard with:\n"
            "• Real-time TY price tracking from 3pm\n"
            "• Statistical envelope overlay (P5-P95)\n"
            "• Auto-updating B/E multiplier\n"
            "• Network URL (anyone on same WiFi can access)\n\n"
            "To stop: /live_pre_event_stop"
        )
        return
    
    # Parse cons_std and fwd_be
    try:
        cons_std = float(context.args[0])
    except ValueError:
        await update.message.reply_text(f"❌ Invalid cons_std: '{context.args[0]}'\nMust be a number (e.g., 25)")
        return
    
    try:
        fwd_be = float(context.args[1])
    except ValueError:
        await update.message.reply_text(f"❌ Invalid fwd_be: '{context.args[1]}'\nMust be a number (e.g., 5.0)")
        return
    
    logger.info(f"Live pre-event request from {chat_id}: cons_std={cons_std}, fwd_be={fwd_be}")
    
    # Send processing message
    processing_msg = await update.message.reply_text(
        f"🔴 Starting LIVE Pre-Event Dashboard...\n\n"
        f"Cons Std Dev: {cons_std}\n"
        f"FWD B/E: {fwd_be} bps\n\n"
        f"Building dashboard and opening tunnel...\n"
        f"This may take 30-60 seconds."
    )
    
    try:
        # Import dashboard builder
        from scripts.live_preevent_dashboard import (
            build_live_preevent_dashboard,
            start_live_server_local,
        )
        
        # Run dashboard builder in executor to avoid blocking
        loop = asyncio.get_event_loop()
        
        # Step 1: Build the dashboard
        result = await loop.run_in_executor(
            None,
            lambda: build_live_preevent_dashboard(
                event_name="NFP",
                cons_std=cons_std,
                fwd_be=fwd_be,
            )
        )
        
        if not result.get("success"):
            await processing_msg.delete()
            await update.message.reply_text(
                f"❌ Could not build dashboard:\n\n{result.get('error', 'Unknown error')}"
            )
            return
        
        html_path = result.get("html_path")
        event_count = result.get("event_count", 0)
        
        # Step 2: Start server on local network
        server_result = await loop.run_in_executor(
            None,
            lambda: start_live_server_local(html_path)
        )
        
        # Delete processing message
        await processing_msg.delete()
        
        if not server_result.get("success"):
            error = server_result.get("error", "Unknown error")
            await update.message.reply_text(
                f"❌ Dashboard built but server failed:\n\n{error}"
            )
            return
        
        # Success! Send the network URL
        network_url = server_result.get("network_url")
        local_url = server_result.get("local_url")
        
        await update.message.reply_text(
            f"🔴 LIVE Pre-Event Dashboard Running\n\n"
            f"📊 NETWORK URL (share with anyone on same WiFi):\n{network_url}\n\n"
            f"Parameters:\n"
            f"  • Cons Std Dev: {cons_std}\n"
            f"  • FWD B/E: {fwd_be} bps\n"
            f"  • Historical Events: {event_count}\n\n"
            f"Features:\n"
            f"  • Real-time TY price from 3pm\n"
            f"  • Statistical envelope (mean, P5-P95)\n"
            f"  • Live B/E multiplier calculation\n"
            f"  • Red dot on boxplot showing current position\n\n"
            f"⚠️ Dashboard stays live until bot restarts or /live_pre_event_stop"
        )
        
        logger.info(f"Live dashboard started: {network_url}")
        
    except Exception as e:
        logger.error(f"Live pre-event command failed: {e}", exc_info=True)
        
        try:
            await processing_msg.delete()
        except:
            pass
        
        await update.message.reply_text(
            f"❌ Error starting live dashboard:\n\n{str(e)}\n\n"
            f"Please try again or check the logs."
        )


async def live_pre_event_stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /live_pre_event_stop command - stop the live dashboard tunnel."""
    chat_id = update.effective_chat.id
    
    # Authorization check
    if not is_authorized(chat_id):
        logger.warning(f"Unauthorized /live_pre_event_stop attempt from chat_id: {chat_id}")
        await update.message.reply_text(f"Unauthorized.\nYour Chat ID: {chat_id}")
        return
    
    try:
        from scripts.live_preevent_dashboard import stop_live_server_tunnel
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, stop_live_server_tunnel)
        
        await update.message.reply_text(
            "✅ Live Pre-Event Dashboard stopped.\n\n"
            "The public URL is no longer accessible."
        )
        logger.info(f"Live dashboard stopped by {chat_id}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error stopping dashboard: {e}")


async def live_pre_event_2025_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /live_pre_event_2025 command - start live pre-event dashboard with 2025 data only.
    
    Usage:
        /live_pre_event_2025 <fwd_be>
        
    Example:
        /live_pre_event_2025 6.0
        
    Returns a local network URL that anyone on the same network can access.
    Uses only 2025 NFP data - ALL events in a single bucket (no bucket separation).
    """
    chat_id = update.effective_chat.id
    
    # Authorization check
    if not is_authorized(chat_id):
        logger.warning(f"Unauthorized /live_pre_event_2025 attempt from chat_id: {chat_id}")
        await update.message.reply_text(f"Unauthorized.\nYour Chat ID: {chat_id}")
        return
    
    # Parse arguments
    if len(context.args) < 1:
        await update.message.reply_text(
            "Usage: /live_pre_event_2025 <fwd_be>\n\n"
            "Parameters:\n"
            "  fwd_be - Forward breakeven in bps (e.g., 6.0)\n\n"
            "Example:\n"
            "  /live_pre_event_2025 6.0\n\n"
            "This starts a LIVE dashboard with:\n"
            "• 2025 NFP events only (all in one bucket)\n"
            "• No bucket separation by cons std dev\n"
            "• Real-time TY price tracking from 3pm\n"
            "• Network URL (anyone on same WiFi can access)"
        )
        return
    
    # Parse fwd_be
    try:
        fwd_be = float(context.args[0])
    except ValueError:
        await update.message.reply_text(f"❌ Invalid fwd_be: '{context.args[0]}'\nMust be a number (e.g., 6.0)")
        return
    
    logger.info(f"Live pre-event 2025 request from {chat_id}: fwd_be={fwd_be}")
    
    # Send processing message
    processing_msg = await update.message.reply_text(
        f"🔴 Starting LIVE Pre-Event Dashboard (2025 Only)...\n\n"
        f"FWD B/E: {fwd_be} bps\n"
        f"Data: All 2025 events (single bucket)\n\n"
        f"Building dashboard...\n"
        f"This may take 15-30 seconds."
    )
    
    try:
        # Import 2025 dashboard builder
        from scripts.live_preevent_dashboard_2025 import (
            build_live_preevent_dashboard_2025,
            start_live_server_local_2025,
            get_local_network_ip,
            LIVE_SERVER_PORT_2025,
        )
        
        # Run dashboard builder in executor to avoid blocking
        loop = asyncio.get_event_loop()
        
        # Step 1: Build the dashboard (no cons_std - single bucket)
        result = await loop.run_in_executor(
            None,
            lambda: build_live_preevent_dashboard_2025(fwd_be=fwd_be)
        )
        
        if not result.get("success"):
            await processing_msg.delete()
            await update.message.reply_text(
                f"❌ Could not build dashboard:\n\n{result.get('error', 'Unknown error')}"
            )
            return
        
        html_path = result.get("html_path")
        event_count = result.get("event_count", 0)
        
        # Step 2: Start server on local network (runs in background)
        import threading
        
        local_ip = get_local_network_ip()
        local_url = f"http://{local_ip}:{LIVE_SERVER_PORT_2025}/"
        
        # Start server in background thread
        def run_server():
            start_live_server_local_2025(html_path)
        
        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
        
        # Wait a moment for server to start
        await asyncio.sleep(2)
        
        # Delete processing message
        await processing_msg.delete()
        
        # Send success message with URL
        await update.message.reply_text(
            f"🔴 LIVE Pre-Event Dashboard (2025 Only) is running!\n\n"
            f"📊 Events: {event_count} (all 2025 in single bucket)\n"
            f"💹 FWD B/E: {fwd_be} bps\n\n"
            f"🌐 Network URL (open on any device on same WiFi):\n"
            f"{local_url}\n\n"
            f"Features:\n"
            f"• Real-time TY price tracking\n"
            f"• All 2025 events (no bucket filtering)\n"
            f"• Live B/E multiplier\n"
            f"• Updates every 5 seconds\n\n"
            f"Note: URL only works on same network."
        )
        logger.info(f"Live 2025 dashboard started for {chat_id}: {local_url}")
        
    except Exception as e:
        logger.error(f"Error in live_pre_event_2025_command: {e}")
        await processing_msg.delete()
        await update.message.reply_text(
            f"❌ Error starting dashboard:\n\n{str(e)}\n\n"
            f"Please try again or check the logs."
        )


# =============================================================================
# Date Tag Commands
# =============================================================================

async def tag_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /tag command - add a date tag with flexible date/time parsing.
    
    Usage:
        /tag <date/time> <#hashtags> <comment>
        
    Examples:
        /tag jan 10 8:30am #nfp NFP beat expectations
        /tag yesterday 2pm #fed Powell was hawkish
        /tag last friday #liquidity Thin markets
    """
    chat_id = update.effective_chat.id
    
    if not is_authorized(chat_id):
        logger.warning(f"Unauthorized access attempt from chat_id: {chat_id}")
        await update.message.reply_text(f"Unauthorized.\nYour Chat ID: {chat_id}")
        return
    
    if not context.args:
        # Show existing hashtags from DB
        from src.core.date_tags_store import get_tag_stats
        
        stats = get_tag_stats()
        existing_hashtags = stats.get('hashtag_counts', [])
        
        # Build message
        lines = ["📝 *Add a date tag*\n"]
        
        if existing_hashtags:
            lines.append("*Existing Tags:*")
            for tag, count in existing_hashtags[:15]:
                lines.append(f"`#{tag}` ({count})")
            lines.append("")
        else:
            lines.append("_No tags yet. Create your first one!_\n")
        
        lines.extend([
            "*Usage:*",
            "`/tag <date> #tag <comment>`",
            "",
            "*Examples:*",
            "• `/tag jan 10 8:30am #nfp NFP beat, TY sold off`",
            "• `/tag yesterday 2pm #fed Powell hawkish`",
            "• `/tag friday #liquidity Thin markets`",
            "",
            "_Pick an existing tag or create a new one!_",
            "_Dates: jan 10, yesterday, friday, 3 days ago_",
            "_Times: 8:30am, 2pm, noon (optional)_",
        ])
        
        await update.message.reply_text('\n'.join(lines), parse_mode="Markdown")
        return
    
    raw_input = ' '.join(context.args)
    username = update.effective_user.username
    
    # Import here to avoid circular import
    from src.core.date_tags_store import add_tag
    
    result = add_tag(raw_input, created_by=username)
    
    if not result.get('success'):
        await update.message.reply_text(f"❌ {result.get('error', 'Unknown error')}")
        return
    
    # Format response
    hashtags_str = ' '.join(f"#{h}" for h in result.get('hashtags', []))
    datetime_str = result.get('tag_datetime') or result.get('tag_date')
    if result.get('has_time') and result.get('tag_datetime'):
        from datetime import datetime as dt
        parsed = dt.fromisoformat(result['tag_datetime'])
        datetime_str = parsed.strftime('%Y-%m-%d %H:%M EST')
    else:
        datetime_str = result.get('tag_date')
    
    response = (
        f"✅ Tag saved\n"
        f"📅 {datetime_str}\n"
        f"💬 {result.get('comment')}\n"
        f"🏷️ {hashtags_str if hashtags_str else '(none)'}\n"
        f"🆔 ID: {result.get('id')}"
    )
    
    await update.message.reply_text(response)


async def tags_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /tags command - view date tags with optional filters.
    
    Usage:
        /tags                   - Last 10 tags
        /tags jan 10            - Tags for specific date
        /tags #nfp              - Tags with hashtag
        /tags jan 1 jan 31      - Tags in date range
    """
    chat_id = update.effective_chat.id
    
    if not is_authorized(chat_id):
        await update.message.reply_text(f"Unauthorized.\nYour Chat ID: {chat_id}")
        return
    
    from src.core.date_tags_store import get_tags, format_tag_for_display
    
    # Parse arguments
    args = context.args or []
    
    # Check for hashtag filter
    hashtag = None
    date_args = []
    for arg in args:
        if arg.startswith('#'):
            hashtag = arg.lstrip('#')
        else:
            date_args.append(arg)
    
    # Determine date filter
    date = None
    start_date = None
    end_date = None
    
    if date_args:
        date_str = ' '.join(date_args)
        # Check if it looks like a range (contains 'to' or has two date-like patterns)
        if ' to ' in date_str.lower():
            parts = date_str.lower().split(' to ')
            start_date = parts[0].strip()
            end_date = parts[1].strip() if len(parts) > 1 else None
        else:
            date = date_str
    
    # Get tags
    tags = get_tags(
        date=date,
        start_date=start_date,
        end_date=end_date,
        hashtag=hashtag,
        limit=15,
    )
    
    if not tags:
        filter_desc = []
        if date:
            filter_desc.append(f"date: {date}")
        if hashtag:
            filter_desc.append(f"#{hashtag}")
        filter_str = ' '.join(filter_desc) if filter_desc else "any"
        await update.message.reply_text(f"📋 No tags found for: {filter_str}")
        return
    
    # Format response
    lines = ["📋 *Date Tags*\n"]
    
    if hashtag:
        lines.append(f"Filter: #{hashtag}\n")
    if date:
        lines.append(f"Date: {date}\n")
    
    lines.append("")
    
    for tag in tags:
        lines.append(format_tag_for_display(tag))
        lines.append("")
    
    lines.append(f"_Showing {len(tags)} tag(s)_")
    
    response = '\n'.join(lines)
    await update.message.reply_text(response, parse_mode="Markdown")


async def tagstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /tagstats command - show tag statistics by hashtag."""
    chat_id = update.effective_chat.id
    
    if not is_authorized(chat_id):
        await update.message.reply_text(f"Unauthorized.\nYour Chat ID: {chat_id}")
        return
    
    from src.core.date_tags_store import get_tag_stats
    
    stats = get_tag_stats()
    
    lines = ["📊 *Tag Statistics*\n"]
    
    if stats['hashtag_counts']:
        for hashtag, count in stats['hashtag_counts'][:15]:
            # Create a simple bar
            bar_len = min(count, 20)
            bar = '█' * bar_len
            lines.append(f"`#{hashtag:<15}` {bar} {count}")
    
    lines.append("")
    lines.append(f"`(uncategorized)  ` {stats['uncategorized']}")
    lines.append("")
    lines.append(f"*Total:* {stats['total_tags']} tags")
    
    await update.message.reply_text('\n'.join(lines), parse_mode="Markdown")


async def untag_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /untag command - delete a tag by ID."""
    chat_id = update.effective_chat.id
    
    if not is_authorized(chat_id):
        await update.message.reply_text(f"Unauthorized.\nYour Chat ID: {chat_id}")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: `/untag <id>`\n\n"
            "Example: `/untag 42`\n\n"
            "Get tag IDs from `/tags` command.",
            parse_mode="Markdown"
        )
        return
    
    try:
        tag_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid tag ID. Must be a number.")
        return
    
    from src.core.date_tags_store import delete_tag
    
    username = update.effective_user.username
    result = delete_tag(tag_id, created_by=username)
    
    if not result.get('success'):
        await update.message.reply_text(f"❌ {result.get('error', 'Unknown error')}")
        return
    
    deleted = result.get('deleted_tag', {})
    await update.message.reply_text(
        f"🗑️ Tag deleted\n"
        f"📅 {deleted.get('tag_date', 'N/A')}\n"
        f"💬 {deleted.get('comment', 'N/A')[:50]}..."
    )


def main():
    """Start the Telegram bot."""
    # Validate configuration
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" or not BOT_TOKEN:
        print("ERROR: Please set BOT_TOKEN in telegram_bot_config.py or TELEGRAM_BOT_TOKEN env var")
        sys.exit(1)
    
    if not ALLOWED_CHAT_IDS or ALLOWED_CHAT_IDS == [0]:
        print("ERROR: Please set ALLOWED_CHAT_IDS in telegram_bot_config.py or TELEGRAM_CHAT_ID env var")
        sys.exit(1)
    
    print("=" * 60)
    print("Market Data Orchestrator - Telegram Bot")
    print("=" * 60)
    print(f"Authorized Chat IDs: {ALLOWED_CHAT_IDS} ({len(ALLOWED_CHAT_IDS)} groups)")
    print(f"Send plots: {SEND_PLOTS}")
    print(f"Send JSON output: {SEND_JSON_OUTPUT}")
    print("=" * 60)
    print("Bot starting... Press Ctrl+C to stop.")
    print("=" * 60)
    
    async def _post_init(app: Application) -> None:
        await pmmacro.start_polling_task(app)

    async def _post_shutdown(app: Application) -> None:
        await pmmacro.stop_polling_task(app)

    request = HTTPXRequest(connect_timeout=10, read_timeout=20, write_timeout=20, pool_timeout=10)
    get_updates_request = HTTPXRequest(connect_timeout=10, read_timeout=40, write_timeout=20, pool_timeout=10)

    # Create application
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .request(request)
        .get_updates_request(get_updates_request)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("data", data_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("query", query_command))  # /query command for groups
    application.add_handler(CommandHandler("feedback", feedback_command))
    application.add_handler(CommandHandler("fomc_alerts", fomc_alerts_command))
    application.add_handler(CommandHandler("pre_event", pre_event_command))  # /pre_event command for pre-event B/E analysis
    application.add_handler(CommandHandler("post_event", post_event_command))  # /post_event command for post-event B/E analysis
    application.add_handler(CommandHandler("live_pre_event", live_pre_event_command))  # /live_pre_event - start live dashboard with public tunnel
    application.add_handler(CommandHandler("live_pre_event_stop", live_pre_event_stop_command))  # /live_pre_event_stop - stop live dashboard
    application.add_handler(CommandHandler("live_pre_event_2025", live_pre_event_2025_command))  # /live_pre_event_2025 - 2025 only, no bucketing
    
    # Date tagging commands
    application.add_handler(CommandHandler("tag", tag_command))  # /tag - add date tag
    application.add_handler(CommandHandler("tags", tags_command))  # /tags - view tags
    application.add_handler(CommandHandler("tagstats", tagstats_command))  # /tagstats - tag statistics
    application.add_handler(CommandHandler("untag", untag_command))  # /untag - delete tag

    # X macro stream command handlers + approval callbacks.
    application.add_handler(CommandHandler("x_status", xmacro.cmd_x_status))
    application.add_handler(CommandHandler("x_follow", xmacro.cmd_x_follow))
    application.add_handler(CommandHandler("x_unfollow", xmacro.cmd_x_unfollow))
    application.add_handler(CommandHandler("x_keywords_add", xmacro.cmd_x_keywords_add))
    application.add_handler(CommandHandler("x_keywords_remove", xmacro.cmd_x_keywords_remove))
    application.add_handler(CommandHandler("x_keywords_list", xmacro.cmd_x_keywords_list))
    application.add_handler(CommandHandler("x_handles_list", xmacro.cmd_x_handles_list))
    application.add_handler(CommandHandler("x_rebuild_rules", xmacro.cmd_x_rebuild_rules))
    application.add_handler(CommandHandler("x_retry_forwards", xmacro.cmd_x_retry_forwards))
    application.add_handler(CommandHandler("bbg_retry_forwards", bbgnews.cmd_bbg_retry_forwards))

    # Polymarket tracking command handlers.
    application.add_handler(CommandHandler("pm_track", pmmacro.cmd_pm_track))
    application.add_handler(CommandHandler("pm_list", pmmacro.cmd_pm_list))
    application.add_handler(CommandHandler("pm_untrack", pmmacro.cmd_pm_untrack))

    application.add_handler(
        CallbackQueryHandler(xmacro.handle_tweet_action, pattern="^tweet_(approve|reject|score):")
    )
    application.add_handler(
        CallbackQueryHandler(bbgnews.handle_bbg_news_action, pattern="^bbgnews_(approve|reject|score):")
    )
    application.add_handler(
        CallbackQueryHandler(pmmacro.handle_pm_alert_action, pattern="^pm_(approve|reject):")
    )
    application.add_handler(
        CallbackQueryHandler(pmmacro.handle_pm_selection_action, pattern="^pm_(pick|done|cancel):")
    )
    
    # Handle text messages: DMs (any text) or group messages with @mention
    # Using Entity(MENTION) filter to ensure we receive messages where bot is tagged
    application.add_handler(MessageHandler(
        (filters.TEXT & ~filters.COMMAND) & (
            filters.ChatType.PRIVATE |  # All DMs
            filters.Entity(MessageEntity.MENTION)  # Group messages with @mentions
        ),
        handle_query
    ))
    application.add_error_handler(on_error)
    
    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

