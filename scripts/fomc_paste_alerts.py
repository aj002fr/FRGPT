#!/usr/bin/env python3
"""
FOMC Paste-and-Alert (Backup for Live)
=======================================
Simple interactive script: paste transcript chunks, get sentiment + alerts.

Usage:
    python scripts/fomc_paste_alerts.py
    python scripts/fomc_paste_alerts.py --chat-id 7977255339
    python scripts/fomc_paste_alerts.py --dry-run

How to use:
    1. Run the script
    2. Paste a chunk of transcript text
    3. Press Enter, then type 'GO' and Enter (or just Enter twice on empty line)
    4. See sentiment score + get Telegram alert if triggered
    5. Repeat for next chunk
    6. Type 'quit' to exit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
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
    from telegram import Bot
    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False
    Bot = None  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
EASTERN_TZ = ZoneInfo("America/New_York")
LOOKBACK_CHUNKS = 15  # 15-minute running score
EXTREME_THRESHOLD = 6.0
CHANGE_THRESHOLD = 3.0
STRENGTHENING_CONSECUTIVE = 3
GPT_MODEL = "gpt-4o"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("FOMCPaste")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class ChunkResult:
    idx: int
    text: str
    chunk_score: float
    running_score: float
    topic: str
    key_phrase: str
    label_5class: str


@dataclass
class AlertState:
    last_running_score: float = 0.0
    consecutive_direction: int = 0
    
    def check_alert(self, chunk_score: float, running_score: float) -> Tuple[bool, str]:
        reasons = []
        
        # Extreme score
        if abs(chunk_score) >= EXTREME_THRESHOLD:
            direction = "HAWKISH" if chunk_score > 0 else "DOVISH"
            reasons.append(f"🔥 EXTREME {direction}: {chunk_score:+.1f}")
        
        # Running score shift
        delta = running_score - self.last_running_score
        if abs(delta) >= CHANGE_THRESHOLD:
            direction = "↑ Hawkish" if delta > 0 else "↓ Dovish"
            reasons.append(f"📊 Shift {direction}: {delta:+.1f}")
        
        # Consecutive pattern
        if chunk_score > 2:
            self.consecutive_direction = self.consecutive_direction + 1 if self.consecutive_direction > 0 else 1
        elif chunk_score < -2:
            self.consecutive_direction = self.consecutive_direction - 1 if self.consecutive_direction < 0 else -1
        else:
            self.consecutive_direction = 0
        
        if abs(self.consecutive_direction) >= STRENGTHENING_CONSECUTIVE:
            direction = "HAWKISH" if self.consecutive_direction > 0 else "DOVISH"
            reasons.append(f"📈 {abs(self.consecutive_direction)}x consecutive {direction}")
        
        self.last_running_score = running_score
        
        return bool(reasons), " | ".join(reasons)


# ---------------------------------------------------------------------------
# GPT Scoring
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert Fed watcher analyzing FOMC press conference transcripts.
Score each text chunk on a hawkish-to-dovish scale.

HAWKISH (+): Inflation concerns, rates staying high, caution on cuts
DOVISH (-): Progress on inflation, openness to cuts, labor market cooling

Scale: -10 (very dovish) to +10 (very hawkish), -2 to +2 = neutral

Respond with JSON only: {"score": <float>, "topic": <str>, "key_phrase": <str max 50 chars>}"""


def score_text(text: str, client: OpenAI) -> Tuple[float, str, str]:
    """Score text using GPT. Returns (score, topic, key_phrase)."""
    try:
        response = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Analyze:\n\n\"{text}\""},
            ],
            temperature=0.2,
            max_tokens=100,
        )
        content = response.choices[0].message.content.strip()
        # Strip markdown fences
        if content.startswith("```"):
            lines = content.split("\n")[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)
        
        result = json.loads(content)
        return (
            float(result.get("score", 0.0)),
            str(result.get("topic", "Other")),
            str(result.get("key_phrase", ""))[:50],
        )
    except Exception as e:
        logger.warning(f"GPT error: {e}")
        return 0.0, "Other", ""


def score_to_label(score: float) -> str:
    if score >= 6:
        return "VERY HAWKISH 🦅🦅"
    elif score >= 2:
        return "Hawkish 🦅"
    elif score <= -6:
        return "VERY DOVISH 🕊️🕊️"
    elif score <= -2:
        return "Dovish 🕊️"
    else:
        return "Neutral ⚖️"


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def get_telegram_bot() -> Optional[Bot]:
    if not HAS_TELEGRAM:
        return None
    try:
        from scripts.telegram_bot_config import BOT_TOKEN
        return Bot(token=BOT_TOKEN)
    except Exception:
        return None


async def send_alert(bot: Bot, chat_id: int, chunk: ChunkResult, reason: str) -> None:
    emoji = "🦅" if chunk.chunk_score > 0 else "🕊️" if chunk.chunk_score < 0 else "⚖️"
    message = (
        f"FOMC LIVE ALERT {emoji}\n\n"
        f"{reason}\n\n"
        f"Chunk Score: {chunk.chunk_score:+.1f} ({chunk.label_5class})\n"
        f"Running Score: {chunk.running_score:+.1f}\n"
        f"Topic: {chunk.topic}\n"
        f"Key: {chunk.key_phrase}\n\n"
        f"{chunk.text[:150]}..."
    )
    try:
        await bot.send_message(chat_id=chat_id, text=message)
        logger.info(f"✅ Alert sent to {chat_id}")
    except Exception as e:
        logger.error(f"❌ Telegram error: {e}")


# ---------------------------------------------------------------------------
# Main Interactive Loop
# ---------------------------------------------------------------------------
async def main_loop(chat_id: Optional[int], dry_run: bool):
    print("\n" + "=" * 60)
    print("  FOMC PASTE-AND-ALERT")
    print("=" * 60)
    print("Paste transcript text, then:")
    print("  - Type 'GO' + Enter to score")
    print("  - Or press Enter on empty line")
    print("  - Type 'quit' to exit")
    print("=" * 60 + "\n")
    
    # Initialize
    client = None
    if not dry_run and HAS_OPENAI:
        try:
            api_key = get_api_key("OPENAI_API_KEY")
            client = OpenAI(api_key=api_key)
            print("✅ OpenAI connected")
        except Exception as e:
            print(f"⚠️ OpenAI error: {e}")
    
    bot = None
    if not dry_run and chat_id:
        bot = get_telegram_bot()
        if bot:
            print(f"✅ Telegram alerts → {chat_id}")
        else:
            print("⚠️ Telegram not available")
    
    if dry_run:
        print("🧪 DRY RUN MODE (no GPT, no Telegram)")
    
    print()
    
    state = AlertState()
    scores_history: List[float] = []
    chunk_idx = 0
    
    while True:
        print(f"--- Chunk {chunk_idx + 1} ---")
        print("Paste text (then 'GO' + Enter, or empty line):\n")
        
        lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            
            if line.strip().upper() == "GO":
                break
            if line.strip().lower() == "quit":
                print("\nBye!")
                return
            if line.strip() == "" and lines:
                # Empty line after content = submit
                break
            
            lines.append(line)
        
        text = " ".join(lines).strip()
        
        if not text:
            print("(empty - try again)\n")
            continue
        
        word_count = len(text.split())
        print(f"\n📝 Got {word_count} words. Scoring...")
        
        # Score
        if client and not dry_run:
            score, topic, key_phrase = score_text(text, client)
        else:
            score, topic, key_phrase = 0.0, "Other", "[dry run]"
        
        # Running score
        scores_history.append(score)
        lookback = scores_history[-LOOKBACK_CHUNKS:]
        running_score = sum(lookback) / len(lookback)
        
        label = score_to_label(score)
        
        chunk = ChunkResult(
            idx=chunk_idx,
            text=text[:200],
            chunk_score=score,
            running_score=running_score,
            topic=topic,
            key_phrase=key_phrase,
            label_5class=label,
        )
        
        # Display result
        print()
        print("┌" + "─" * 50 + "┐")
        print(f"│ Chunk Score:   {score:+.1f}  {label}")
        print(f"│ Running Score: {running_score:+.1f}")
        print(f"│ Topic:         {topic}")
        print(f"│ Key phrase:    {key_phrase}")
        print("└" + "─" * 50 + "┘")
        
        # Check alert
        should_alert, reason = state.check_alert(score, running_score)
        
        if should_alert:
            print(f"\n🚨 ALERT: {reason}")
            
            if bot and chat_id:
                await send_alert(bot, chat_id, chunk, reason)
        else:
            print("(no alert triggered)")
        
        print()
        chunk_idx += 1


def main():
    parser = argparse.ArgumentParser(description="FOMC Paste-and-Alert (backup for live)")
    parser.add_argument("--chat-id", type=int, help="Telegram chat ID for alerts")
    parser.add_argument("--dry-run", action="store_true", help="Skip GPT and Telegram")
    args = parser.parse_args()
    
    asyncio.run(main_loop(args.chat_id, args.dry_run))


if __name__ == "__main__":
    main()





