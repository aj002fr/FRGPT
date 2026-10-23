#!/usr/bin/env python3
"""
Show detailed sentiment analysis for a specific FOMC transcript.
Usage: python scripts/show_sentiment_details.py <date>
Example: python scripts/show_sentiment_details.py 2022-01-26
"""

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SENTIMENT_DIR = PROJECT_ROOT / "workspace" / "fed" / "fomc conference" / "sentiment"


def score_to_expected_direction(score: float, threshold: float = 2.0) -> str:
    """Hawkish -> TY down, Dovish -> TY up."""
    if score > threshold:
        return "DOWN (hawkish)"
    elif score < -threshold:
        return "UP (dovish)"
    else:
        return "NEUTRAL"


def format_match(match: bool | None) -> str:
    if match is True:
        return "[Y] MATCH"
    elif match is False:
        return "[X] MISMATCH"
    else:
        return "[-] N/A"


def main():
    parser = argparse.ArgumentParser(description="Show detailed sentiment analysis")
    parser.add_argument("date", help="Transcript date (YYYY-MM-DD)")
    parser.add_argument("--max-chunks", type=int, default=None, help="Limit chunks shown")
    args = parser.parse_args()
    
    # Find the JSON file
    json_files = list(SENTIMENT_DIR.glob(f"{args.date}*.json"))
    if not json_files:
        print(f"No sentiment file found for date: {args.date}")
        return 1
    
    json_file = json_files[0]
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    print("=" * 100)
    print(f"FOMC Press Conference Sentiment Analysis: {data['date']}")
    print(f"Video ID: {data['video_id']}")
    print(f"Anchor Time (UTC): {data['anchor_utc']}")
    print(f"Duration: {data['duration_seconds'] / 60:.1f} minutes")
    print(f"Total Chunks: {data['chunk_count']}")
    print(f"Final Score: {data['final_score']:+.1f}")
    print("=" * 100)
    
    # Match rates summary
    rates = data.get("match_rates", {})
    print("\nOVERALL MATCH RATES:")
    print(f"  Chunk Score vs Same Window:  {rates.get('chunk_score_same', 'N/A'):.1%}" if rates.get('chunk_score_same') else "  Chunk Score vs Same Window: N/A")
    print(f"  Running Score vs Same Window: {rates.get('running_score_same', 'N/A'):.1%}" if rates.get('running_score_same') else "  Running Score vs Same Window: N/A")
    print(f"  Chunk Score vs Lag1m:         {rates.get('chunk_score_lag1m', 'N/A'):.1%}" if rates.get('chunk_score_lag1m') else "  Chunk Score vs Lag1m: N/A")
    print(f"  Running Score vs Lag1m:       {rates.get('running_score_lag1m', 'N/A'):.1%}" if rates.get('running_score_lag1m') else "  Running Score vs Lag1m: N/A")
    print()
    
    chunks = data.get("chunks", [])
    if args.max_chunks:
        chunks = chunks[:args.max_chunks]
    
    for i, chunk in enumerate(chunks):
        print("-" * 100)
        print(f"CHUNK {chunk['idx'] + 1}/{data['chunk_count']}")
        print(f"Time: {chunk['start_s'] / 60:.1f} - {chunk['end_s'] / 60:.1f} min into conference")
        print()
        
        # Full text
        print("TEXT:")
        text = chunk.get("text", "")
        # Clean up and wrap
        text_clean = text.replace("\n", " ").strip()
        # Word wrap at ~100 chars
        words = text_clean.split()
        lines = []
        current_line = []
        for word in words:
            if sum(len(w) for w in current_line) + len(current_line) + len(word) > 95:
                lines.append(" ".join(current_line))
                current_line = [word]
            else:
                current_line.append(word)
        if current_line:
            lines.append(" ".join(current_line))
        for line in lines[:6]:  # Show first 6 lines
            print(f"  {line}")
        if len(lines) > 6:
            print(f"  ... ({len(lines) - 6} more lines)")
        print()
        
        # Sentiment
        print("SENTIMENT:")
        print(f"  Chunk Score:   {chunk['chunk_score']:+.1f}")
        print(f"  Running Score: {chunk['running_score']:+.1f} (15-min avg)")
        print(f"  Topic:         {chunk.get('topic', 'N/A')}")
        print(f"  Key Phrase:    \"{chunk.get('key_phrase', 'N/A')}\"")
        print(f"  Reasoning:     {chunk.get('reasoning', 'N/A')}")
        print()
        
        # Expected directions
        expected_chunk = score_to_expected_direction(chunk['chunk_score'])
        expected_running = score_to_expected_direction(chunk['running_score'])
        print("EXPECTED TY DIRECTION (from sentiment):")
        print(f"  From Chunk Score:   {expected_chunk}")
        print(f"  From Running Score: {expected_running}")
        print()
        
        # Price action
        print("ACTUAL PRICE ACTION:")
        for win_name in ["same", "lag1m"]:
            pw = chunk.get("price_windows", {}).get(win_name, {})
            if pw:
                direction = pw.get("direction", "N/A").upper()
                delta_ticks = pw.get("delta_ticks")
                delta_bps = pw.get("delta_bps")
                close_start = pw.get("close_start")
                close_end = pw.get("close_end")
                
                win_label = "Same 5-min" if win_name == "same" else "Next 5-min (lagged)"
                if delta_ticks is not None:
                    print(f"  {win_label}: {direction} ({delta_ticks:+.1f} ticks / {delta_bps:+.2f} bps)")
                    print(f"    Price: {close_start:.6f} -> {close_end:.6f}")
                else:
                    print(f"  {win_label}: No data")
        print()
        
        # Match results
        print("MATCH RESULTS:")
        match_chunk = chunk.get("match_chunk_score", {})
        match_running = chunk.get("match_running_score", {})
        for win_name in ["same", "lag1m"]:
            win_label = "Same 5-min" if win_name == "same" else "Lag 5-min"
            chunk_match = format_match(match_chunk.get(win_name))
            running_match = format_match(match_running.get(win_name))
            print(f"  {win_label}: Chunk {chunk_match} | Running {running_match}")
        print()
    
    print("=" * 100)
    return 0


if __name__ == "__main__":
    exit(main())

