#!/usr/bin/env python3
"""
Fetch FOMC press conference video URLs from YouTube Data API.

Searches the official Federal Reserve YouTube channel for press conference
videos matching dates in the CSV, and updates the CSV with video URLs.

This script does NOT download transcripts - it only populates the youtube_url
column for manual verification before running fetch_fed_press_conference_transcripts.py.

Requires: YOUTUBE_API_KEY in config/keys.env
"""

import csv
import json
import re
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
KEYS_FILE = CONFIG_DIR / "keys.env"
FOMC_DIR = PROJECT_ROOT / "workspace" / "fed" / "fomc conference"
INPUT_CSV = FOMC_DIR / "fed_press_conference_videos.csv"
OUTPUT_CSV = FOMC_DIR / "fed_press_conference_videos.csv"  # Same file, updated in place
SEARCH_LOG = FOMC_DIR / "youtube_search_log.json"

# YouTube API configuration
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Channel name to filter results (case-insensitive partial match)
# We filter in Python instead of using channelId because the Fed's channel ID
# doesn't work reliably with the API search filter
PREFERRED_CHANNEL_NAME = "Federal Reserve"


def load_api_key() -> str:
    """Load YouTube API key from config/keys.env."""
    if not KEYS_FILE.exists():
        raise FileNotFoundError(
            f"Keys file not found: {KEYS_FILE}\n"
            f"Please create it and add: YOUTUBE_API_KEY=your_api_key_here"
        )
    
    with open(KEYS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("YOUTUBE_API_KEY="):
                key = line.split("=", 1)[1].strip()
                if key:
                    return key
    
    raise ValueError(
        f"YOUTUBE_API_KEY not found in {KEYS_FILE}\n"
        f"Please add: YOUTUBE_API_KEY=your_api_key_here"
    )


def search_youtube(
    api_key: str,
    query: str,
    published_after: Optional[str] = None,
    published_before: Optional[str] = None,
    max_results: int = 10,
) -> list[dict]:
    """
    Search YouTube using the Data API v3.
    
    Args:
        api_key: YouTube Data API key
        query: Search query string
        published_after: Optional ISO 8601 date (e.g., "2024-01-31T00:00:00Z")
        published_before: Optional ISO 8601 date
        max_results: Maximum number of results (1-50)
    
    Returns:
        List of video items from the API response
    """
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "key": api_key,
        "order": "relevance",
    }
    
    if published_after:
        params["publishedAfter"] = published_after
    
    if published_before:
        params["publishedBefore"] = published_before
    
    url = f"{YOUTUBE_API_BASE}/search?{urllib.parse.urlencode(params)}"
    
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("items", [])
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        raise RuntimeError(f"YouTube API error {e.code}: {error_body}")


def format_date_for_search(date_str: str) -> tuple[str, str, str]:
    """
    Convert date string to search-friendly formats.
    
    Args:
        date_str: Date in YYYY-MM-DD format
    
    Returns:
        Tuple of (month_name, day, year) e.g., ("January", "31", "2024")
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return (dt.strftime("%B"), str(dt.day), str(dt.year))


def find_video_for_date(api_key: str, date_str: str) -> Optional[dict]:
    """
    Search for an FOMC press conference video for a specific date.
    
    Searches YouTube and filters results to prefer the official Federal Reserve channel.
    
    Args:
        api_key: YouTube API key
        date_str: Date in YYYY-MM-DD format
    
    Returns:
        Best matching video dict with id, title, url, or None if not found
    """
    month, day, year = format_date_for_search(date_str)
    
    # Create date bounds for filtering (day before to day after)
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    from datetime import timedelta
    pub_after = (dt - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    pub_before = (dt + timedelta(days=3)).strftime("%Y-%m-%dT00:00:00Z")
    
    # Search query - use specific date format
    query = f"FOMC press conference {month} {day} {year}"
    
    try:
        # Search without channel filter, filter in Python
        results = search_youtube(
            api_key=api_key,
            query=query,
            published_after=pub_after,
            published_before=pub_before,
            max_results=10,
        )
        
        if not results:
            # Try without date filter
            results = search_youtube(
                api_key=api_key,
                query=query,
                max_results=10,
            )
        
        if not results:
            return None
        
        # Filter results: prefer Federal Reserve channel
        fed_videos = []
        other_videos = []
        
        for video in results:
            channel_title = video["snippet"].get("channelTitle", "")
            title = video["snippet"].get("title", "")
            
            # Check if from Federal Reserve channel
            if PREFERRED_CHANNEL_NAME.lower() in channel_title.lower():
                # Also check title contains "press conference" or "FOMC"
                title_lower = title.lower()
                if "press conference" in title_lower or "fomc" in title_lower:
                    fed_videos.append(video)
            else:
                other_videos.append(video)
        
        # Use Federal Reserve video if found, otherwise first other result
        if fed_videos:
            video = fed_videos[0]
            source = "Federal Reserve channel"
        elif other_videos:
            video = other_videos[0]
            source = f"other channel ({video['snippet'].get('channelTitle', 'unknown')})"
        else:
            return None
        
        video_id = video["id"]["videoId"]
        return {
            "video_id": video_id,
            "title": video["snippet"]["title"],
            "channel": video["snippet"].get("channelTitle", ""),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "published_at": video["snippet"]["publishedAt"],
            "query_used": query,
            "source": source,
        }
        
    except Exception as e:
        print(f"    Warning: Search failed: {e}")
        return None


def process_csv(api_key: str, overwrite: bool = False) -> dict:
    """
    Process the CSV and search for video URLs.
    
    Args:
        api_key: YouTube API key
        overwrite: If True, overwrite existing URLs; if False, skip rows with URLs
    
    Returns:
        Summary dict with results
    """
    if not INPUT_CSV.exists():
        raise FileNotFoundError(f"CSV not found: {INPUT_CSV}")
    
    # Read existing CSV
    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    print(f"Processing {len(rows)} rows from {INPUT_CSV}...")
    print(f"Preferring videos from: {PREFERRED_CHANNEL_NAME}")
    print()
    
    found = []
    not_found = []
    skipped = []
    updated_rows = []
    
    for i, row in enumerate(rows, 1):
        date = row.get("date", "").strip()
        existing_url = row.get("youtube_url", "").strip()
        
        if not date:
            print(f"  [{i}/{len(rows)}] Skipping row with empty date")
            skipped.append({"row": i, "reason": "empty date"})
            updated_rows.append(row)
            continue
        
        if existing_url and not overwrite:
            print(f"  [{i}/{len(rows)}] {date}: Already has URL, skipping")
            skipped.append({"date": date, "reason": "already has URL"})
            updated_rows.append(row)
            continue
        
        print(f"  [{i}/{len(rows)}] {date}: Searching...", end=" ", flush=True)
        
        result = find_video_for_date(api_key, date)
        
        if result:
            channel = result.get('channel', 'unknown')
            print(f"FOUND: {result['title'][:50]}... [{channel}]")
            row["youtube_url"] = result["url"]
            found.append({
                "date": date,
                **result,
            })
        else:
            print("NOT FOUND")
            not_found.append({"date": date})
        
        updated_rows.append(row)
    
    # Write updated CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "youtube_url"])
        writer.writeheader()
        for row in updated_rows:
            writer.writerow({"date": row["date"], "youtube_url": row.get("youtube_url", "")})
    
    # Build summary
    summary = {
        "run_at": datetime.now().isoformat(),
        "preferred_channel": PREFERRED_CHANNEL_NAME,
        "total_rows": len(rows),
        "found": len(found),
        "not_found": len(not_found),
        "skipped": len(skipped),
        "found_details": found,
        "not_found_details": not_found,
        "skipped_details": skipped,
    }
    
    return summary


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Fetch FOMC press conference video URLs from YouTube"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing URLs in the CSV (default: skip rows with URLs)",
    )
    args = parser.parse_args()
    
    print("FOMC Press Conference Video URL Fetcher")
    print("=" * 50)
    
    try:
        api_key = load_api_key()
        print(f"API key loaded from {KEYS_FILE}")
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        return 1
    
    try:
        summary = process_csv(api_key, overwrite=args.overwrite)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print(f"\nPlease run export_fed_press_conference_dates.py first.")
        return 1
    except RuntimeError as e:
        print(f"\nAPI Error: {e}")
        return 1
    
    # Write search log
    with open(SEARCH_LOG, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    print()
    print("=" * 50)
    print("Summary:")
    print(f"  Total dates:  {summary['total_rows']}")
    print(f"  Found:        {summary['found']}")
    print(f"  Not found:    {summary['not_found']}")
    print(f"  Skipped:      {summary['skipped']}")
    print()
    print(f"CSV updated:     {OUTPUT_CSV}")
    print(f"Search log:      {SEARCH_LOG}")
    print()
    print("Next steps:")
    print("  1. Open the CSV and verify the URLs are correct")
    print("  2. Manually add any missing URLs")
    print("  3. Run: python scripts/fetch_fed_press_conference_transcripts.py")
    
    return 0


if __name__ == "__main__":
    exit(main())

