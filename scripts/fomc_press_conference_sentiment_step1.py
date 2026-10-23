#!/usr/bin/env python3
"""
FOMC Press Conference Transcript Sentiment vs TY Price Action (Step 1)
=======================================================================
For each Fed press conference transcript:
- Score each chunk hawkish↔dovish via GPT (chunk_score)
- Compute recency-biased running score (EMA) and final score
- Compute deterministic price-action alignment vs TY for both scores

Outputs:
- Per-transcript JSON: workspace/fed/fomc conference/sentiment/{date}_{video_id}.json
- Aggregate CSV: workspace/fed/fomc conference/sentiment/summary.csv

Usage:
    python scripts/fomc_press_conference_sentiment_step1.py
    python scripts/fomc_press_conference_sentiment_step1.py --max-transcripts 2 --max-chunks 10
    python scripts/fomc_press_conference_sentiment_step1.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Imports from project
# ---------------------------------------------------------------------------
from rules.treasury_price_conversion import (
    points_to_ticks,
    ticks_to_bps,
    decimal_to_bond_notation,
    TICKS_PER_POINT,
)
from config.settings import get_api_key

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    OpenAI = None  # type: ignore

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

# ---------------------------------------------------------------------------
# Constants / Config
# ---------------------------------------------------------------------------
TRANSCRIPTS_DIR = PROJECT_ROOT / "workspace" / "fed" / "fomc conference" / "transcripts"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "fed" / "fomc conference" / "sentiment"
ECONOMIC_EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"

EASTERN_TZ = ZoneInfo("America/New_York")

# Default chunking: seconds per chunk
DEFAULT_CHUNK_SECONDS = 60  # 1 minute

# Running score lookback (number of chunks for 15-min window at 1-min chunks)
DEFAULT_LOOKBACK_CHUNKS = 15  # 15 × 1 min = 15 min

# Neutral thresholds
DEFAULT_SCORE_NEUTRAL_THRESHOLD = 2.0  # abs(score) <= this => neutral
DEFAULT_TICKS_NEUTRAL_THRESHOLD = 1.0  # abs(move) <= this => neutral move

# GPT model
GPT_MODEL = "gpt-4o"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("FOMCSentiment")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Chunk:
    """A contiguous segment of transcript."""
    idx: int
    start_s: float
    end_s: float
    text: str
    # Populated after scoring
    chunk_score: Optional[float] = None
    running_score: Optional[float] = None
    topic: Optional[str] = None
    key_phrase: Optional[str] = None
    reasoning: Optional[str] = None


@dataclass
class PriceWindow:
    """Price action for a specific time window."""
    window_type: str  # same, lag1m
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None
    close_start: Optional[float] = None
    close_end: Optional[float] = None
    delta_decimal: Optional[float] = None
    delta_ticks: Optional[float] = None
    delta_bps: Optional[float] = None
    direction: Optional[str] = None  # up, down, neutral


@dataclass
class ChunkResult:
    """Full result for a chunk including scores and price alignment."""
    chunk: Chunk
    price_windows: Dict[str, PriceWindow] = field(default_factory=dict)
    # Match flags: score type -> window type -> bool or None
    match_chunk_score: Dict[str, Optional[bool]] = field(default_factory=dict)
    match_running_score: Dict[str, Optional[bool]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Step 1: Loader
# ---------------------------------------------------------------------------
def load_transcripts(
    max_transcripts: Optional[int] = None,
    random_sample: bool = False,
) -> List[Dict[str, Any]]:
    """Load transcript JSONs from TRANSCRIPTS_DIR."""
    import random
    
    transcripts = []
    json_files = sorted(TRANSCRIPTS_DIR.glob("*.json"))
    # Exclude index.json
    json_files = [f for f in json_files if f.name != "index.json"]
    
    if random_sample and max_transcripts and max_transcripts < len(json_files):
        json_files = random.sample(json_files, max_transcripts)
        json_files = sorted(json_files)  # Sort for consistent ordering
    elif max_transcripts:
        json_files = json_files[:max_transcripts]
    
    for fp in json_files:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["_filepath"] = str(fp)
        transcripts.append(data)
    
    logger.info(f"Loaded {len(transcripts)} transcripts" + (" (random sample)" if random_sample else ""))
    return transcripts


def get_anchor_time_utc(event_date_str: str) -> datetime:
    """
    Parse event_date (US/Eastern local, ISO format) and convert to UTC datetime.
    Format: YYYY-MM-DDTHH:MM:SS (no offset, but assumed ET)
    """
    # Parse as naive, localize to Eastern, convert to UTC
    dt_naive = datetime.fromisoformat(event_date_str)
    dt_eastern = dt_naive.replace(tzinfo=EASTERN_TZ)
    dt_utc = dt_eastern.astimezone(timezone.utc)
    return dt_utc


def lookup_event_anchor(transcript_date: str) -> Optional[datetime]:
    """
    Look up the Fed Press Conference event_date for the given transcript date
    from economic_events.db and return as UTC datetime.
    
    transcript_date: YYYY-MM-DD
    """
    if not ECONOMIC_EVENTS_DB.exists():
        logger.warning(f"economic_events.db not found at {ECONOMIC_EVENTS_DB}")
        return None
    
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    cursor = conn.cursor()
    cursor.execute("""
        SELECT event_date 
        FROM economic_events 
        WHERE event_name = 'Fed Press Conference'
          AND country LIKE '%United States%'
          AND DATE(event_date) = ?
        ORDER BY event_date ASC
        LIMIT 1
    """, (transcript_date,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        logger.warning(f"No Fed Press Conference event found for date {transcript_date}")
        return None
    
    return get_anchor_time_utc(row[0])


# ---------------------------------------------------------------------------
# Step 2: Chunking
# ---------------------------------------------------------------------------
def chunk_segments(
    segments: List[Dict[str, Any]],
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS,
    max_chunks: Optional[int] = None,
) -> List[Chunk]:
    """
    Build contiguous time-based chunks from transcript segments.
    Each chunk covers approximately chunk_seconds of transcript time.
    """
    if not segments:
        return []
    
    chunks: List[Chunk] = []
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
        
        # Check if chunk boundary reached
        if current_end_s - current_start_s >= chunk_seconds:
            chunks.append(Chunk(
                idx=len(chunks),
                start_s=current_start_s,
                end_s=current_end_s,
                text=" ".join(current_texts),
            ))
            current_texts = []
            current_start_s = None
            
            if max_chunks and len(chunks) >= max_chunks:
                break
    
    # Add remaining text as final chunk
    if current_texts and current_start_s is not None:
        if not max_chunks or len(chunks) < max_chunks:
            chunks.append(Chunk(
                idx=len(chunks),
                start_s=current_start_s,
                end_s=current_end_s,
                text=" ".join(current_texts),
            ))
    
    logger.info(f"Created {len(chunks)} chunks")
    return chunks


# ---------------------------------------------------------------------------
# Step 3: Market data
# ---------------------------------------------------------------------------
def get_pg_connection():
    """Get PostgreSQL connection."""
    if not HAS_PSYCOPG2:
        raise ImportError("psycopg2 not installed")
    
    from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
    )


def fetch_ty_bars(
    start_utc: datetime,
    end_utc: datetime,
) -> List[Dict[str, Any]]:
    """
    Fetch TY 1-minute bars from ohlcv_1m for the given UTC time range.
    Returns list of dicts with keys: ts_event, open, high, low, close, volume
    """
    if not HAS_PSYCOPG2:
        logger.warning("psycopg2 not available, skipping market data fetch")
        return []
    
    conn = get_pg_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT ts_event, open, high, low, close, volume
            FROM ohlcv_1m
            WHERE symbol ILIKE 'TY%%'
              AND ts_event >= %s
              AND ts_event <= %s
            ORDER BY ts_event ASC
        """, (start_utc, end_utc))
        rows = [dict(r) for r in cursor.fetchall()]
        return rows
    finally:
        conn.close()


def find_close_at_time(
    bars: List[Dict[str, Any]],
    target_utc: datetime,
) -> Optional[float]:
    """
    Find the close price at or just before target_utc.
    Returns None if no bar found.
    """
    best_bar = None
    for bar in bars:
        bar_ts = bar["ts_event"]
        # Handle timezone-aware comparison
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        if bar_ts <= target_utc:
            best_bar = bar
        else:
            break
    return best_bar["close"] if best_bar else None


def compute_price_delta(close_start: float, close_end: float) -> Tuple[float, float, float, str]:
    """
    Compute price delta in decimal, ticks, bps, and direction.
    Returns: (delta_decimal, delta_ticks, delta_bps, direction)
    """
    delta_decimal = close_end - close_start
    delta_ticks = points_to_ticks(delta_decimal)
    delta_bps = ticks_to_bps(delta_ticks)
    
    if delta_ticks > 0:
        direction = "up"
    elif delta_ticks < 0:
        direction = "down"
    else:
        direction = "neutral"
    
    return delta_decimal, delta_ticks, delta_bps, direction


def compute_chunk_price_windows(
    chunk: Chunk,
    anchor_utc: datetime,
    bars: List[Dict[str, Any]],
) -> Dict[str, PriceWindow]:
    """
    Compute price windows for a chunk (1-min chunks):
    - same: concurrent 1-min window (chunk_start -> chunk_end)
    - lag1m: next 1 minute after chunk ends
    """
    windows = {}
    
    chunk_start_utc = anchor_utc + timedelta(seconds=chunk.start_s)
    chunk_end_utc = anchor_utc + timedelta(seconds=chunk.end_s)
    
    # Define window specs: (name, start, end)
    window_specs = [
        ("same", chunk_start_utc, chunk_end_utc),
        ("lag1m", chunk_end_utc, chunk_end_utc + timedelta(minutes=1)),
    ]
    
    for name, win_start, win_end in window_specs:
        close_start = find_close_at_time(bars, win_start)
        close_end = find_close_at_time(bars, win_end)
        
        pw = PriceWindow(window_type=name, start_ts=win_start, end_ts=win_end)
        
        if close_start is not None and close_end is not None:
            pw.close_start = close_start
            pw.close_end = close_end
            delta_dec, delta_ticks, delta_bps, direction = compute_price_delta(close_start, close_end)
            pw.delta_decimal = delta_dec
            pw.delta_ticks = delta_ticks
            pw.delta_bps = delta_bps
            pw.direction = direction
        
        windows[name] = pw
    
    return windows


# ---------------------------------------------------------------------------
# Step 4: LLM scoring
# ---------------------------------------------------------------------------
# Import updated prompt (we'll update the file below)
FOMC_SENTIMENT_SYSTEM_PROMPT = """You are an expert Fed watcher analyzing FOMC press conference transcripts.
Your task is to score each text chunk on a hawkish-to-dovish scale.

HAWKISH signals (positive scores):
- Concerns about persistent/sticky inflation
- Strong labor market as reason to keep rates high
- "Higher for longer" rate stance
- Caution about premature rate cuts
- Upside inflation risks
- Need to see more progress before cutting

DOVISH signals (negative scores):
- Progress on inflation / disinflation
- Labor market cooling
- Openness to rate cuts
- Downside employment risks
- Economic slowdown concerns
- Financial conditions tightening on their own

SCORING GUIDE:
+7 to +10: VERY HAWKISH - rates staying high, fighting inflation
+3 to +6:  HAWKISH - cautious about cutting
-2 to +2:  NEUTRAL - balanced, data-dependent
-6 to -3:  DOVISH - leaning toward cuts
-10 to -7: VERY DOVISH - cuts imminent

IMPORTANT:
- Focus on Powell's statements (ignore reporter questions)
- Weight forward guidance / policy reaction function heavily
- Consider context from ongoing Q&A

Always respond with valid JSON only."""

FOMC_SENTIMENT_ANALYSIS_PROMPT = """Analyze this FOMC press conference excerpt for hawkish/dovish sentiment:

TEXT:
\"\"\"{text}\"\"\"

Respond with JSON:
{{
    "score": <float from -10 to +10>,
    "topic": <one of: "Employment", "Inflation", "Rate Policy", "Economic Outlook", "Financial Conditions", "Other">,
    "key_phrase": <most significant phrase, max 60 chars>,
    "reasoning": <brief explanation, max 100 chars>
}}

Focus on what this implies for the future path of interest rates.
Respond ONLY with the JSON object."""


def strip_markdown_fences(text: str) -> str:
    """Strip markdown code fences from GPT response."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```)
        lines = lines[1:]
        # Remove last line if it's just ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def score_chunk_with_gpt(
    chunk: Chunk,
    client: OpenAI,
) -> Tuple[float, str, str, str]:
    """
    Score a chunk using GPT. Returns (score, topic, key_phrase, reasoning).
    """
    try:
        response = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": FOMC_SENTIMENT_SYSTEM_PROMPT},
                {"role": "user", "content": FOMC_SENTIMENT_ANALYSIS_PROMPT.format(text=chunk.text)},
            ],
            temperature=0.2,
            max_tokens=200,
        )
        content = response.choices[0].message.content.strip()
        
        # Strip markdown fences if present
        content = strip_markdown_fences(content)
        
        # Parse JSON
        result = json.loads(content)
        score = float(result.get("score", 0.0))
        topic = str(result.get("topic", "Other"))
        key_phrase = str(result.get("key_phrase", ""))[:60]
        reasoning = str(result.get("reasoning", ""))[:100]
        
        return score, topic, key_phrase, reasoning
    except Exception as e:
        logger.warning(f"GPT scoring failed for chunk {chunk.idx}: {e}")
        return 0.0, "Other", "", f"Error: {e}"


def score_chunks(
    chunks: List[Chunk],
    lookback_chunks: int = DEFAULT_LOOKBACK_CHUNKS,
    dry_run: bool = False,
) -> List[Chunk]:
    """
    Score all chunks with GPT and compute running score (15-min lookback).
    """
    if not HAS_OPENAI:
        logger.error("OpenAI not installed, cannot score chunks")
        return chunks
    
    if dry_run:
        logger.info("Dry run: skipping GPT scoring")
        for i, chunk in enumerate(chunks):
            chunk.chunk_score = 0.0
            chunk.running_score = 0.0
            chunk.topic = "Other"
            chunk.key_phrase = "[dry run]"
            chunk.reasoning = "Dry run mode"
        return chunks
    
    try:
        api_key = get_api_key("OPENAI_API_KEY")
        client = OpenAI(api_key=api_key)
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI client: {e}")
        return chunks
    
    scores_history: List[float] = []
    
    for i, chunk in enumerate(chunks):
        score, topic, key_phrase, reasoning = score_chunk_with_gpt(chunk, client)
        chunk.chunk_score = score
        chunk.topic = topic
        chunk.key_phrase = key_phrase
        chunk.reasoning = reasoning
        
        # Update running score: simple average of last N chunks (15-min lookback)
        scores_history.append(score)
        lookback_scores = scores_history[-lookback_chunks:]
        running_score = sum(lookback_scores) / len(lookback_scores)
        
        chunk.running_score = running_score
        
        logger.info(
            f"Chunk {i+1}/{len(chunks)}: score={score:+.1f}, running={running_score:+.1f} "
            f"(last {len(lookback_scores)} chunks), topic={topic}"
        )
    
    return chunks


# ---------------------------------------------------------------------------
# Step 5: Alignment computation
# ---------------------------------------------------------------------------
def expected_direction_from_score(
    score: float,
    threshold: float = DEFAULT_SCORE_NEUTRAL_THRESHOLD,
) -> str:
    """
    Determine expected TY direction from sentiment score.
    Hawkish (+) => TY down (yields up)
    Dovish (-) => TY up (yields down)
    """
    if score > threshold:
        return "down"  # Hawkish -> expect TY price to fall
    elif score < -threshold:
        return "up"    # Dovish -> expect TY price to rise
    else:
        return "neutral"


def check_match(
    expected_dir: str,
    observed_dir: str,
    observed_ticks: Optional[float],
    ticks_threshold: float = DEFAULT_TICKS_NEUTRAL_THRESHOLD,
) -> Optional[bool]:
    """
    Check if expected direction matches observed direction.
    Returns None if either side is neutral or data missing.
    """
    if expected_dir == "neutral":
        return None  # No expectation
    
    if observed_ticks is None or observed_dir is None:
        return None  # Missing data
    
    if abs(observed_ticks) <= ticks_threshold:
        return None  # Observed move too small
    
    return expected_dir == observed_dir


def compute_alignment(
    chunk: Chunk,
    price_windows: Dict[str, PriceWindow],
    score_threshold: float = DEFAULT_SCORE_NEUTRAL_THRESHOLD,
    ticks_threshold: float = DEFAULT_TICKS_NEUTRAL_THRESHOLD,
) -> Tuple[Dict[str, Optional[bool]], Dict[str, Optional[bool]]]:
    """
    Compute match flags for both chunk_score and running_score against each window.
    Returns: (match_chunk_score_dict, match_running_score_dict)
    """
    match_chunk: Dict[str, Optional[bool]] = {}
    match_running: Dict[str, Optional[bool]] = {}
    
    for win_name, pw in price_windows.items():
        # Expected from chunk_score
        expected_chunk = expected_direction_from_score(
            chunk.chunk_score or 0.0, score_threshold
        )
        # Expected from running_score
        expected_running = expected_direction_from_score(
            chunk.running_score or 0.0, score_threshold
        )
        
        match_chunk[win_name] = check_match(
            expected_chunk, pw.direction, pw.delta_ticks, ticks_threshold
        )
        match_running[win_name] = check_match(
            expected_running, pw.direction, pw.delta_ticks, ticks_threshold
        )
    
    return match_chunk, match_running


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------
def process_transcript(
    transcript: Dict[str, Any],
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS,
    max_chunks: Optional[int] = None,
    lookback_chunks: int = DEFAULT_LOOKBACK_CHUNKS,
    score_threshold: float = DEFAULT_SCORE_NEUTRAL_THRESHOLD,
    ticks_threshold: float = DEFAULT_TICKS_NEUTRAL_THRESHOLD,
    anchor_offset_seconds: float = 0.0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Process a single transcript: chunk, score, fetch market data, compute alignment.
    Returns result dict for JSON output.
    """
    date = transcript.get("date", "unknown")
    video_id = transcript.get("video_id", "unknown")
    segments = transcript.get("segments", [])
    duration_seconds = transcript.get("duration_seconds", 0.0)
    
    logger.info(f"Processing transcript: {date} ({video_id})")
    
    # Lookup anchor time
    anchor_utc = lookup_event_anchor(date)
    if anchor_utc is None:
        # Fallback: assume 14:30 ET on that date
        dt_naive = datetime.fromisoformat(f"{date}T14:30:00")
        anchor_utc = dt_naive.replace(tzinfo=EASTERN_TZ).astimezone(timezone.utc)
        logger.warning(f"Using fallback anchor time: {anchor_utc}")
    
    # Apply offset
    anchor_utc = anchor_utc + timedelta(seconds=anchor_offset_seconds)
    
    # Chunk
    chunks = chunk_segments(segments, chunk_seconds, max_chunks)
    
    if not chunks:
        logger.warning(f"No chunks created for {date}")
        return {
            "date": date,
            "video_id": video_id,
            "error": "No chunks created",
        }
    
    # Fetch market data
    # Window: anchor_utc to anchor_utc + duration + 10 min buffer
    end_utc = anchor_utc + timedelta(seconds=duration_seconds + 600)
    bars = fetch_ty_bars(anchor_utc, end_utc)
    logger.info(f"Fetched {len(bars)} TY bars")
    
    # Score chunks
    chunks = score_chunks(chunks, lookback_chunks, dry_run)
    
    # Compute alignment for each chunk
    chunk_results: List[Dict[str, Any]] = []
    
    for chunk in chunks:
        price_windows = compute_chunk_price_windows(chunk, anchor_utc, bars)
        match_chunk, match_running = compute_alignment(
            chunk, price_windows, score_threshold, ticks_threshold
        )
        
        chunk_result = {
            "idx": chunk.idx,
            "start_s": chunk.start_s,
            "end_s": chunk.end_s,
            "text": chunk.text[:200] + "..." if len(chunk.text) > 200 else chunk.text,
            "chunk_score": chunk.chunk_score,
            "running_score": chunk.running_score,
            "topic": chunk.topic,
            "key_phrase": chunk.key_phrase,
            "reasoning": chunk.reasoning,
            "price_windows": {
                name: {
                    "delta_ticks": pw.delta_ticks,
                    "delta_bps": pw.delta_bps,
                    "direction": pw.direction,
                    "close_start": pw.close_start,
                    "close_end": pw.close_end,
                }
                for name, pw in price_windows.items()
            },
            "match_chunk_score": match_chunk,
            "match_running_score": match_running,
        }
        chunk_results.append(chunk_result)
    
    # Compute transcript-level summary
    final_score = chunks[-1].running_score if chunks else 0.0
    
    # Match rates by window and score type
    def compute_match_rate(results: List[Dict], key: str, window: str) -> Optional[float]:
        matches = [r[key].get(window) for r in results if r[key].get(window) is not None]
        if not matches:
            return None
        return sum(1 for m in matches if m) / len(matches)
    
    match_rates = {}
    for win in ["same", "lag1m"]:
        match_rates[f"chunk_score_{win}"] = compute_match_rate(chunk_results, "match_chunk_score", win)
        match_rates[f"running_score_{win}"] = compute_match_rate(chunk_results, "match_running_score", win)
    
    return {
        "date": date,
        "video_id": video_id,
        "anchor_utc": anchor_utc.isoformat(),
        "duration_seconds": duration_seconds,
        "chunk_count": len(chunks),
        "final_score": final_score,
        "match_rates": match_rates,
        "chunks": chunk_results,
    }


def write_summary_csv(
    results: List[Dict[str, Any]],
    output_path: Path,
) -> None:
    """Write aggregate summary CSV with one row per chunk×window."""
    rows = []
    
    for result in results:
        if "error" in result:
            continue
        
        date = result["date"]
        video_id = result["video_id"]
        
        for chunk in result.get("chunks", []):
            for win in ["same", "lag1m"]:
                pw = chunk.get("price_windows", {}).get(win, {})
                rows.append({
                    "date": date,
                    "video_id": video_id,
                    "chunk_idx": chunk["idx"],
                    "window": win,
                    "chunk_score": chunk.get("chunk_score"),
                    "running_score": chunk.get("running_score"),
                    "topic": chunk.get("topic"),
                    "delta_ticks": pw.get("delta_ticks"),
                    "delta_bps": pw.get("delta_bps"),
                    "observed_direction": pw.get("direction"),
                    "match_chunk_score": chunk.get("match_chunk_score", {}).get(win),
                    "match_running_score": chunk.get("match_running_score", {}).get(win),
                })
    
    if not rows:
        logger.warning("No rows for summary CSV")
        return
    
    fieldnames = list(rows[0].keys())
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    
    logger.info(f"Summary CSV written to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="FOMC sentiment vs TY price (Step 1)")
    parser.add_argument("--max-transcripts", type=int, default=None, help="Limit transcripts")
    parser.add_argument("--random", action="store_true", help="Randomly sample transcripts")
    parser.add_argument("--max-chunks", type=int, default=None, help="Limit chunks per transcript")
    parser.add_argument("--chunk-seconds", type=float, default=DEFAULT_CHUNK_SECONDS, help="Seconds per chunk")
    parser.add_argument("--lookback-chunks", type=int, default=DEFAULT_LOOKBACK_CHUNKS, help="Chunks for running score (15-min = 15 at 1-min chunks)")
    parser.add_argument("--score-threshold", type=float, default=DEFAULT_SCORE_NEUTRAL_THRESHOLD)
    parser.add_argument("--ticks-threshold", type=float, default=DEFAULT_TICKS_NEUTRAL_THRESHOLD)
    parser.add_argument("--anchor-offset-seconds", type=float, default=0.0, help="Offset anchor time")
    parser.add_argument("--dry-run", action="store_true", help="Skip GPT calls")
    args = parser.parse_args()
    
    # Ensure output dir exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load transcripts
    transcripts = load_transcripts(args.max_transcripts, random_sample=args.random)
    
    if not transcripts:
        logger.error("No transcripts found")
        return 1
    
    results = []
    
    for transcript in transcripts:
        result = process_transcript(
            transcript,
            chunk_seconds=args.chunk_seconds,
            max_chunks=args.max_chunks,
            lookback_chunks=args.lookback_chunks,
            score_threshold=args.score_threshold,
            ticks_threshold=args.ticks_threshold,
            anchor_offset_seconds=args.anchor_offset_seconds,
            dry_run=args.dry_run,
        )
        
        # Write per-transcript JSON
        date = result.get("date", "unknown")
        video_id = result.get("video_id", "unknown")
        output_file = OUTPUT_DIR / f"{date}_{video_id}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"Written: {output_file}")
        
        results.append(result)
    
    # Write aggregate summary CSV
    summary_csv = OUTPUT_DIR / "summary.csv"
    write_summary_csv(results, summary_csv)
    
    # Print overall stats
    logger.info("=" * 60)
    logger.info(f"Processed {len(results)} transcripts")
    for result in results:
        if "error" in result:
            continue
        date = result["date"]
        final = result.get("final_score", 0.0)
        rates = result.get("match_rates", {})
        logger.info(
            f"  {date}: final_score={final:+.1f}, "
            f"match_chunk_same={rates.get('chunk_score_same')}, "
            f"match_running_same={rates.get('running_score_same')}"
        )
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

