#!/usr/bin/env python3
"""
FOMC Live Sentiment Alerts
==========================
Real-time sentiment scoring + Telegram alerts during Fed press conferences.

Modes:
1. --replay <date>     : Replay a historical transcript (for testing)
2. --stdin             : Accept pasted transcript text from terminal
3. --scrape            : Scrape live from StreamText (during actual FOMC)

Usage:
    # Test with historical transcript
    python scripts/fomc_live_sentiment_alerts.py --replay 2024-01-31
    
    # Paste mode (ctrl+D to end input, or continuous line-by-line)
    python scripts/fomc_live_sentiment_alerts.py --stdin
    
    # Live scrape (during actual FOMC)
    python scripts/fomc_live_sentiment_alerts.py --scrape
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_api_key

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    OpenAI = None  # type: ignore

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

try:
    from telegram import Bot
    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False
    Bot = None  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TRANSCRIPTS_DIR = PROJECT_ROOT / "workspace" / "fed" / "fomc conference" / "transcripts"
EASTERN_TZ = ZoneInfo("America/New_York")

# Chunk settings
CHUNK_SECONDS = 60  # 1-minute chunks
LOOKBACK_CHUNKS = 15  # 15-minute running score

# Alert thresholds
EXTREME_THRESHOLD = 6.0  # Very hawkish/dovish
CHANGE_THRESHOLD = 3.0   # Meaningful shift in running score
STRENGTHENING_CONSECUTIVE = 3  # 3 chunks in same direction

# StreamText URL
STREAMTEXT_URL = "https://www.streamtext.net/player?event=CFI-FRB&chat=false"

# GPT model
GPT_MODEL = "gpt-4o"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("FOMCLive")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class LiveChunk:
    """A chunk of live transcript."""
    idx: int
    timestamp: datetime
    text: str
    chunk_score: float = 0.0
    running_score: float = 0.0
    topic: str = "Other"
    key_phrase: str = ""
    reasoning: str = ""
    label_5class: str = "neutral"


@dataclass
class AlertState:
    """Track state for alert decisions."""
    chunks: List[LiveChunk] = field(default_factory=list)
    last_running_score: float = 0.0
    consecutive_direction: int = 0  # positive = hawkish streak, negative = dovish streak
    last_alert_idx: int = -1
    
    def should_alert(self, chunk: LiveChunk) -> Tuple[bool, str]:
        """Determine if we should send an alert for this chunk."""
        reasons = []
        
        # 1. Extreme score
        if abs(chunk.chunk_score) >= EXTREME_THRESHOLD:
            direction = "HAWKISH" if chunk.chunk_score > 0 else "DOVISH"
            reasons.append(f"🔥 EXTREME {direction}: {chunk.chunk_score:+.1f}")
        
        # 2. Meaningful change in running score
        delta = chunk.running_score - self.last_running_score
        if abs(delta) >= CHANGE_THRESHOLD:
            direction = "↑ Hawkish" if delta > 0 else "↓ Dovish"
            reasons.append(f"📊 Running score shift {direction}: {delta:+.1f}")
        
        # 3. Strengthening pattern (3+ consecutive same direction)
        if chunk.chunk_score > 2:
            if self.consecutive_direction > 0:
                self.consecutive_direction += 1
            else:
                self.consecutive_direction = 1
        elif chunk.chunk_score < -2:
            if self.consecutive_direction < 0:
                self.consecutive_direction -= 1
            else:
                self.consecutive_direction = -1
        else:
            self.consecutive_direction = 0
        
        if abs(self.consecutive_direction) >= STRENGTHENING_CONSECUTIVE:
            direction = "HAWKISH" if self.consecutive_direction > 0 else "DOVISH"
            reasons.append(f"📈 {abs(self.consecutive_direction)}x consecutive {direction}")
        
        # Update state
        self.last_running_score = chunk.running_score
        self.chunks.append(chunk)
        
        if reasons:
            self.last_alert_idx = chunk.idx
            return True, " | ".join(reasons)
        
        return False, ""


# ---------------------------------------------------------------------------
# GPT Scoring
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert Fed watcher analyzing FOMC press conference transcripts in real-time.
Score each text chunk on a hawkish-to-dovish scale.

HAWKISH (+): Inflation concerns, rates staying high, caution on cuts
DOVISH (-): Progress on inflation, openness to cuts, labor market cooling

Scale: -10 (very dovish) to +10 (very hawkish)
-2 to +2 = neutral/balanced

Respond with JSON only: {"score": <float>, "topic": <str>, "key_phrase": <str max 50 chars>, "reasoning": <str max 80 chars>}"""


def strip_markdown_fences(text: str) -> str:
    """Strip markdown code fences from GPT response."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def score_text_with_gpt(text: str, client: OpenAI) -> Tuple[float, str, str, str]:
    """Score text using GPT. Returns (score, topic, key_phrase, reasoning)."""
    try:
        response = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Analyze this Fed speech excerpt:\n\n\"{text}\""},
            ],
            temperature=0.2,
            max_tokens=150,
        )
        content = strip_markdown_fences(response.choices[0].message.content.strip())
        result = json.loads(content)
        return (
            float(result.get("score", 0.0)),
            str(result.get("topic", "Other")),
            str(result.get("key_phrase", ""))[:50],
            str(result.get("reasoning", ""))[:80],
        )
    except Exception as e:
        logger.warning(f"GPT scoring failed: {e}")
        return 0.0, "Other", "", f"Error: {e}"


def score_to_5class(score: float) -> str:
    """Map score to 5-class label."""
    if score >= 6:
        return "very_hawkish"
    elif score >= 2:
        return "hawkish"
    elif score <= -6:
        return "very_dovish"
    elif score <= -2:
        return "dovish"
    else:
        return "neutral"


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def get_telegram_bot() -> Optional[Bot]:
    """Get Telegram bot instance."""
    if not HAS_TELEGRAM:
        logger.warning("python-telegram-bot not installed")
        return None
    
    try:
        from scripts.telegram_bot_config import BOT_TOKEN
        return Bot(token=BOT_TOKEN)
    except Exception as e:
        logger.warning(f"Failed to create Telegram bot: {e}")
        return None


def get_chat_ids(override: Optional[List[int]] = None) -> List[int]:
    """Get authorized chat IDs. If override provided, use that instead."""
    if override:
        return override
    try:
        from scripts.telegram_bot_config import ALLOWED_CHAT_IDS
        return ALLOWED_CHAT_IDS
    except Exception:
        return []


async def send_telegram_alert(bot: Bot, chat_ids: List[int], message: str) -> None:
    """Send alert to all authorized chats."""
    for chat_id in chat_ids:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                # No parse_mode - plain text to avoid markdown issues
            )
            logger.info(f"Alert sent to chat {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send to {chat_id}: {e}")


def escape_markdown(text: str) -> str:
    """Escape special Markdown characters for Telegram."""
    # Escape these chars: _ * [ ] ( ) ~ ` > # + - = | { } . !
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text


def format_alert_message(chunk: LiveChunk, alert_reason: str) -> str:
    """Format a Telegram alert message (plain text, no markdown)."""
    emoji = "🦅" if chunk.chunk_score > 0 else "🕊️" if chunk.chunk_score < 0 else "⚖️"
    
    # Use plain text to avoid markdown parsing issues
    return (
        f"FOMC LIVE ALERT {emoji}\n\n"
        f"{alert_reason}\n\n"
        f"Chunk Score: {chunk.chunk_score:+.1f} ({chunk.label_5class})\n"
        f"Running Score: {chunk.running_score:+.1f}\n"
        f"Topic: {chunk.topic}\n"
        f"Key: {chunk.key_phrase}\n\n"
        f"📝 {chunk.text[:150]}..."
    )


# ---------------------------------------------------------------------------
# Replay Mode (Historical Testing)
# ---------------------------------------------------------------------------
def load_historical_transcript(date: str) -> Optional[Dict[str, Any]]:
    """Load a historical transcript for replay."""
    pattern = f"{date}_*.json"
    matches = list(TRANSCRIPTS_DIR.glob(pattern))
    
    if not matches:
        logger.error(f"No transcript found for date {date}")
        return None
    
    with open(matches[0], "r", encoding="utf-8") as f:
        return json.load(f)


def chunk_segments_for_replay(
    segments: List[Dict[str, Any]],
    chunk_seconds: float = CHUNK_SECONDS,
) -> List[Tuple[float, str]]:
    """Build time-based chunks from transcript segments.
    Returns list of (start_seconds, text) tuples.
    """
    if not segments:
        return []
    
    chunks: List[Tuple[float, str]] = []
    current_texts: List[str] = []
    current_start_s: Optional[float] = None
    current_end_s: float = 0.0
    
    for seg in segments:
        seg_start = seg.get("start", 0.0)
        seg_duration = seg.get("duration", 0.0)
        seg_end = seg_start + seg_duration
        seg_text = seg.get("text", "").strip()
        
        if not seg_text:
            continue
        
        if current_start_s is None:
            current_start_s = seg_start
        
        current_texts.append(seg_text)
        current_end_s = seg_end
        
        if current_end_s - current_start_s >= chunk_seconds:
            chunks.append((current_start_s, " ".join(current_texts)))
            current_texts = []
            current_start_s = None
    
    # Add remaining
    if current_texts and current_start_s is not None:
        chunks.append((current_start_s, " ".join(current_texts)))
    
    return chunks


async def run_replay_mode(
    date: str,
    speed: float = 10.0,  # 10x speed by default
    dry_run: bool = False,
    chat_ids_override: Optional[List[int]] = None,
) -> None:
    """Replay a historical transcript with simulated timing."""
    logger.info(f"=== REPLAY MODE: {date} (speed={speed}x) ===")
    
    transcript = load_historical_transcript(date)
    if not transcript:
        return
    
    segments = transcript.get("segments", [])
    chunks = chunk_segments_for_replay(segments)
    
    if not chunks:
        logger.error("No chunks created")
        return
    
    logger.info(f"Loaded {len(chunks)} chunks from {date}")
    
    # Initialize
    if not dry_run and HAS_OPENAI:
        api_key = get_api_key("OPENAI_API_KEY")
        client = OpenAI(api_key=api_key)
    else:
        client = None
    
    bot = None if dry_run else get_telegram_bot()
    chat_ids = get_chat_ids(chat_ids_override) if bot else []
    
    if chat_ids:
        logger.info(f"Alerts will be sent to chat IDs: {chat_ids}")
    
    state = AlertState()
    scores_history: List[float] = []
    
    prev_time = 0.0
    
    for i, (start_s, text) in enumerate(chunks):
        # Simulate timing
        if i > 0:
            delay = (start_s - prev_time) / speed
            if delay > 0:
                logger.info(f"[Waiting {delay:.1f}s simulated...]")
                await asyncio.sleep(min(delay, 2.0))  # Cap at 2s real time
        
        prev_time = start_s
        
        # Score
        if client and not dry_run:
            score, topic, key_phrase, reasoning = score_text_with_gpt(text, client)
        else:
            score, topic, key_phrase, reasoning = 0.0, "Other", "[dry run]", "Dry run mode"
        
        # Running score
        scores_history.append(score)
        lookback = scores_history[-LOOKBACK_CHUNKS:]
        running_score = sum(lookback) / len(lookback)
        
        # Create chunk
        chunk = LiveChunk(
            idx=i,
            timestamp=datetime.now(EASTERN_TZ),
            text=text[:200],
            chunk_score=score,
            running_score=running_score,
            topic=topic,
            key_phrase=key_phrase,
            reasoning=reasoning,
            label_5class=score_to_5class(score),
        )
        
        # Log
        logger.info(
            f"Chunk {i+1}/{len(chunks)} @ {start_s/60:.1f}m: "
            f"score={score:+.1f}, running={running_score:+.1f}, {chunk.label_5class}"
        )
        
        # Check alert
        should_alert, alert_reason = state.should_alert(chunk)
        
        if should_alert:
            logger.info(f"🚨 ALERT: {alert_reason}")
            
            if bot and chat_ids:
                message = format_alert_message(chunk, alert_reason)
                await send_telegram_alert(bot, chat_ids, message)
            else:
                print("\n" + "=" * 60)
                print(format_alert_message(chunk, alert_reason).replace("*", "").replace("`", "").replace("_", ""))
                print("=" * 60 + "\n")
    
    # Summary
    logger.info(f"\n=== REPLAY COMPLETE ===")
    logger.info(f"Total chunks: {len(chunks)}")
    logger.info(f"Alerts sent: {len([c for c in state.chunks if state.last_alert_idx >= 0])}")
    logger.info(f"Final running score: {state.last_running_score:+.1f}")


# ---------------------------------------------------------------------------
# Stdin Mode (Pasted Text)
# ---------------------------------------------------------------------------
async def run_stdin_mode(dry_run: bool = False, chat_ids_override: Optional[List[int]] = None) -> None:
    """Accept pasted transcript text from stdin."""
    logger.info("=== STDIN MODE ===")
    logger.info("Paste transcript text. Press Ctrl+D (Unix) or Ctrl+Z (Windows) to end.")
    logger.info("Or paste line-by-line for continuous processing.\n")
    
    # Initialize
    if not dry_run and HAS_OPENAI:
        api_key = get_api_key("OPENAI_API_KEY")
        client = OpenAI(api_key=api_key)
    else:
        client = None
    
    bot = None if dry_run else get_telegram_bot()
    chat_ids = get_chat_ids(chat_ids_override) if bot else []
    
    if chat_ids:
        logger.info(f"Alerts will be sent to chat IDs: {chat_ids}")
    
    state = AlertState()
    scores_history: List[float] = []
    
    buffer: List[str] = []
    chunk_idx = 0
    
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            
            buffer.append(line)
            combined = " ".join(buffer)
            
            # Process when we have enough text (roughly 1 min of speech = ~150 words)
            word_count = len(combined.split())
            if word_count < 100:
                continue
            
            # Score
            if client and not dry_run:
                score, topic, key_phrase, reasoning = score_text_with_gpt(combined, client)
            else:
                score, topic, key_phrase, reasoning = 0.0, "Other", "[dry run]", "Dry run"
            
            # Running score
            scores_history.append(score)
            lookback = scores_history[-LOOKBACK_CHUNKS:]
            running_score = sum(lookback) / len(lookback)
            
            # Create chunk
            chunk = LiveChunk(
                idx=chunk_idx,
                timestamp=datetime.now(EASTERN_TZ),
                text=combined[:200],
                chunk_score=score,
                running_score=running_score,
                topic=topic,
                key_phrase=key_phrase,
                reasoning=reasoning,
                label_5class=score_to_5class(score),
            )
            
            logger.info(
                f"Chunk {chunk_idx+1}: score={score:+.1f}, running={running_score:+.1f}, {chunk.label_5class}"
            )
            
            # Check alert
            should_alert, alert_reason = state.should_alert(chunk)
            
            if should_alert:
                logger.info(f"🚨 ALERT: {alert_reason}")
                
                if bot and chat_ids:
                    message = format_alert_message(chunk, alert_reason)
                    await send_telegram_alert(bot, chat_ids, message)
                else:
                    print("\n" + format_alert_message(chunk, alert_reason).replace("*", "").replace("`", "").replace("_", ""))
            
            # Reset buffer
            buffer = []
            chunk_idx += 1
            
    except EOFError:
        pass
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
    
    logger.info(f"\n=== SESSION END ===")
    logger.info(f"Chunks processed: {chunk_idx}")


# ---------------------------------------------------------------------------
# Scrape Mode (StreamText) - Uses Playwright for dynamic JS content
# ---------------------------------------------------------------------------
async def run_scrape_mode(
    poll_interval: float = 5.0,
    dry_run: bool = False,
    chat_ids_override: Optional[List[int]] = None,
) -> None:
    """Scrape live transcript from StreamText using Playwright (handles JS)."""
    if not HAS_PLAYWRIGHT:
        logger.error("Playwright not installed. Install with: pip install playwright && playwright install chromium")
        logger.info("Falling back to --stdin mode for now.")
        return
    
    logger.info("=== SCRAPE MODE (Playwright) ===")
    logger.info(f"URL: {STREAMTEXT_URL}")
    logger.info(f"Polling every {poll_interval}s")
    logger.info("Press Ctrl+C to stop.\n")
    
    # Initialize GPT
    if not dry_run and HAS_OPENAI:
        api_key = get_api_key("OPENAI_API_KEY")
        client = OpenAI(api_key=api_key)
    else:
        client = None
    
    bot = None if dry_run else get_telegram_bot()
    chat_ids = get_chat_ids(chat_ids_override) if bot else []
    
    if chat_ids:
        logger.info(f"Alerts will be sent to chat IDs: {chat_ids}")
    
    state = AlertState()
    scores_history: List[float] = []
    
    seen_text = ""
    chunk_idx = 0
    buffer: List[str] = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        logger.info("Navigating to StreamText...")
        await page.goto(STREAMTEXT_URL, wait_until="networkidle")
        logger.info("Page loaded. Waiting for live content...\n")
        
        try:
            while True:
                # StreamText typically has the transcript in a scrollable div
                # Common selectors: #text, .transcript, [data-text], etc.
                # Try multiple selectors
                selectors = [
                    "#text",
                    ".transcript",
                    "[data-testid='transcript']",
                    "div[style*='overflow']",  # scrollable container
                    "body",  # fallback
                ]
                
                text_content = ""
                for selector in selectors:
                    try:
                        element = await page.query_selector(selector)
                        if element:
                            text_content = await element.inner_text()
                            if text_content and len(text_content) > 50:
                                break
                    except Exception:
                        continue
                
                # Clean up
                clean_text = re.sub(r'\s+', ' ', text_content).strip()
                
                # Find new content
                if clean_text and clean_text != seen_text and len(clean_text) > len(seen_text):
                    # Get just the new portion
                    if clean_text.startswith(seen_text):
                        new_text = clean_text[len(seen_text):].strip()
                    else:
                        # Text changed completely - take the end portion
                        new_text = clean_text[-500:] if len(clean_text) > 500 else clean_text
                    
                    if new_text and len(new_text) > 20:
                        logger.debug(f"New text ({len(new_text)} chars): {new_text[:80]}...")
                        buffer.append(new_text)
                        combined = " ".join(buffer)
                        word_count = len(combined.split())
                        
                        # Process chunk when we have enough (~100 words ≈ 1 min of speech)
                        if word_count >= 100:
                            # Score
                            if client and not dry_run:
                                score, topic, key_phrase, reasoning = score_text_with_gpt(combined, client)
                            else:
                                score, topic, key_phrase, reasoning = 0.0, "Other", "[dry run]", "Dry run"
                            
                            # Running score
                            scores_history.append(score)
                            lookback = scores_history[-LOOKBACK_CHUNKS:]
                            running_score = sum(lookback) / len(lookback)
                            
                            chunk = LiveChunk(
                                idx=chunk_idx,
                                timestamp=datetime.now(EASTERN_TZ),
                                text=combined[:200],
                                chunk_score=score,
                                running_score=running_score,
                                topic=topic,
                                key_phrase=key_phrase,
                                reasoning=reasoning,
                                label_5class=score_to_5class(score),
                            )
                            
                            logger.info(
                                f"Chunk {chunk_idx+1}: score={score:+.1f}, "
                                f"running={running_score:+.1f}, {chunk.label_5class}"
                            )
                            
                            # Check alert
                            should_alert, alert_reason = state.should_alert(chunk)
                            
                            if should_alert:
                                logger.info(f"🚨 ALERT: {alert_reason}")
                                
                                if bot and chat_ids:
                                    message = format_alert_message(chunk, alert_reason)
                                    await send_telegram_alert(bot, chat_ids, message)
                            
                            buffer = []
                            chunk_idx += 1
                    
                    seen_text = clean_text
                
                await asyncio.sleep(poll_interval)
                
        except KeyboardInterrupt:
            logger.info("\nStopped by user")
        except Exception as e:
            logger.error(f"Scrape error: {e}")
        finally:
            await browser.close()
    
    logger.info(f"\n=== SCRAPE SESSION END ===")
    logger.info(f"Chunks processed: {chunk_idx}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="FOMC Live Sentiment Alerts")
    
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replay", metavar="DATE", help="Replay historical transcript (YYYY-MM-DD)")
    mode.add_argument("--stdin", action="store_true", help="Accept pasted text from stdin")
    mode.add_argument("--scrape", action="store_true", help="Scrape live from StreamText")
    
    parser.add_argument("--speed", type=float, default=10.0, help="Replay speed multiplier (default: 10x)")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Scrape poll interval in seconds")
    parser.add_argument("--chat-id", type=int, action="append", help="Override chat ID(s) for alerts (can repeat)")
    parser.add_argument("--dry-run", action="store_true", help="Skip GPT calls and Telegram alerts")
    
    args = parser.parse_args()
    
    chat_ids_override = args.chat_id  # List[int] or None
    
    if args.replay:
        asyncio.run(run_replay_mode(args.replay, speed=args.speed, dry_run=args.dry_run, chat_ids_override=chat_ids_override))
    elif args.stdin:
        asyncio.run(run_stdin_mode(dry_run=args.dry_run, chat_ids_override=chat_ids_override))
    elif args.scrape:
        asyncio.run(run_scrape_mode(poll_interval=args.poll_interval, dry_run=args.dry_run, chat_ids_override=chat_ids_override))


if __name__ == "__main__":
    main()

