#!/usr/bin/env python3
"""
Fetch timestamped YouTube transcripts for Fed Press Conference videos.

Reads the CSV created by export_fed_press_conference_dates.py (containing dates
and YouTube URLs filled in by the user) and downloads transcripts for each video
using youtube-transcript-api.

Input:  workspace/fed/fomc conference/fed_press_conference_videos.csv
Output: workspace/fed/fomc conference/transcripts/{date}_{video_id}.json
        workspace/fed/fomc conference/transcript_run_summary.json
"""

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
    )
    import requests
    from http.cookiejar import MozillaCookieJar
except ImportError:
    print("Error: youtube-transcript-api is not installed.")
    print("Install it with: pip install -r requirements-youtube.txt")
    sys.exit(1)

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
FOMC_DIR = PROJECT_ROOT / "workspace" / "fed" / "fomc conference"
INPUT_CSV = FOMC_DIR / "fed_press_conference_videos.csv"
TRANSCRIPTS_DIR = FOMC_DIR / "transcripts"
RUN_SUMMARY_FILE = FOMC_DIR / "transcript_run_summary.json"

# Cookie file for bypassing YouTube IP blocks
# Export from browser using "Get cookies.txt LOCALLY" extension
COOKIES_FILE = PROJECT_ROOT / "config" / "youtube_cookies.txt"


def load_cookies_into_session(cookie_file: Path) -> requests.Session:
    """
    Load Netscape-format cookies.txt into a requests.Session.
    
    Args:
        cookie_file: Path to cookies.txt file
        
    Returns:
        requests.Session with cookies loaded
    """
    session = requests.Session()
    
    # MozillaCookieJar can read Netscape cookie format
    cookie_jar = MozillaCookieJar(str(cookie_file))
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    
    session.cookies = cookie_jar
    return session


def get_transcript_api() -> YouTubeTranscriptApi:
    """
    Create YouTubeTranscriptApi instance, using cookies if available.
    
    Returns:
        Configured YouTubeTranscriptApi instance
    """
    if COOKIES_FILE.exists():
        print(f"Using cookies from: {COOKIES_FILE}")
        try:
            session = load_cookies_into_session(COOKIES_FILE)
            return YouTubeTranscriptApi(http_client=session)
        except Exception as e:
            print(f"Warning: Failed to load cookies: {e}")
            print("Proceeding without cookies...")
            return YouTubeTranscriptApi()
    else:
        print(f"No cookies file found at {COOKIES_FILE}")
        print("If you get IP blocked, export YouTube cookies from your browser.")
        print("See: https://github.com/jdepoix/youtube-transcript-api#working-around-ip-bans")
        return YouTubeTranscriptApi()




def extract_video_id(url: str) -> Optional[str]:
    """
    Extract YouTube video ID from various URL formats.
    
    Supports:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://www.youtube.com/embed/VIDEO_ID
    - Just the video ID itself
    
    Returns:
        Video ID string, or None if not parseable.
    """
    if not url:
        return None
    
    url = url.strip()
    
    # Direct video ID (11 chars, alphanumeric + _ -)
    if re.match(r'^[A-Za-z0-9_-]{11}$', url):
        return url
    
    # Standard watch URL
    match = re.search(r'[?&]v=([A-Za-z0-9_-]{11})', url)
    if match:
        return match.group(1)
    
    # Short URL (youtu.be)
    match = re.search(r'youtu\.be/([A-Za-z0-9_-]{11})', url)
    if match:
        return match.group(1)
    
    # Embed URL
    match = re.search(r'youtube\.com/embed/([A-Za-z0-9_-]{11})', url)
    if match:
        return match.group(1)
    
    return None


def fetch_transcript(api: YouTubeTranscriptApi, video_id: str) -> list[dict]:
    """
    Fetch transcript segments for a YouTube video.
    
    Args:
        api: YouTubeTranscriptApi instance
        video_id: YouTube video ID
        
    Returns:
        List of dicts with keys: start, duration, text
        
    Raises:
        Exception on failure (NoTranscriptFound, TranscriptsDisabled, etc.)
    """
    # Version 1.x API: use instance.fetch()
    transcript = api.fetch(video_id)
    # Convert to list of dicts with start, duration, text
    return [
        {
            "start": snippet.start,
            "duration": snippet.duration,
            "text": snippet.text,
        }
        for snippet in transcript
    ]


def process_csv(api: YouTubeTranscriptApi) -> dict:
    """
    Read the CSV and fetch transcripts for each video.
    
    Args:
        api: YouTubeTranscriptApi instance
    
    Returns:
        Run summary dict with successes, failures, and stats.
    """
    if not INPUT_CSV.exists():
        raise FileNotFoundError(f"CSV not found: {INPUT_CSV}")
    
    # Ensure transcripts directory exists
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    
    successes = []
    failures = []
    skipped = []
    
    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    print(f"Processing {len(rows)} rows from {INPUT_CSV}...")
    
    for i, row in enumerate(rows, 1):
        date = row.get("date", "").strip()
        youtube_url = row.get("youtube_url", "").strip()
        
        if not date:
            print(f"  [{i}/{len(rows)}] Skipping row with empty date")
            skipped.append({"row": i, "reason": "empty date"})
            continue
        
        if not youtube_url:
            print(f"  [{i}/{len(rows)}] {date}: No YouTube URL, skipping")
            skipped.append({"date": date, "reason": "no youtube_url"})
            continue
        
        video_id = extract_video_id(youtube_url)
        if not video_id:
            print(f"  [{i}/{len(rows)}] {date}: Could not extract video ID from '{youtube_url}'")
            failures.append({
                "date": date,
                "youtube_url": youtube_url,
                "error": "could not extract video_id",
            })
            continue
        
        output_file = TRANSCRIPTS_DIR / f"{date}_{video_id}.json"
        
        # Skip if already downloaded
        if output_file.exists():
            print(f"  [{i}/{len(rows)}] {date}: Already exists, skipping")
            skipped.append({"date": date, "video_id": video_id, "reason": "already exists"})
            continue
        
        print(f"  [{i}/{len(rows)}] {date}: Fetching transcript for {video_id}...", end=" ")
        
        try:
            transcript = fetch_transcript(api, video_id)
            
            # Calculate total duration
            total_duration = sum(seg.get("duration", 0) for seg in transcript)
            
            # Write transcript JSON (timestamped segments)
            output_data = {
                "date": date,
                "video_id": video_id,
                "youtube_url": youtube_url,
                "fetched_at": datetime.now(ZoneInfo("America/New_York")).isoformat(),
                "segment_count": len(transcript),
                "duration_seconds": round(total_duration, 1),
                "segments": transcript,
            }
            
            with open(output_file, "w", encoding="utf-8") as out_f:
                json.dump(output_data, out_f, indent=2, ensure_ascii=False)
            
            # Write plain text transcript (for reading/searching)
            txt_file = TRANSCRIPTS_DIR / f"{date}_{video_id}.txt"
            full_text = " ".join(seg.get("text", "") for seg in transcript)
            with open(txt_file, "w", encoding="utf-8") as txt_f:
                txt_f.write(f"FOMC Press Conference - {date}\n")
                txt_f.write(f"Video: {youtube_url}\n")
                txt_f.write(f"Duration: {int(total_duration // 60)} minutes\n")
                txt_f.write("=" * 60 + "\n\n")
                txt_f.write(full_text)
            
            print(f"OK ({len(transcript)} segments, {int(total_duration // 60)} min)")
            successes.append({
                "date": date,
                "video_id": video_id,
                "segment_count": len(transcript),
                "duration_seconds": round(total_duration, 1),
                "output_file": str(output_file),
                "txt_file": str(txt_file),
            })
            
        except NoTranscriptFound as e:
            print(f"FAILED (no transcript found)")
            failures.append({
                "date": date,
                "video_id": video_id,
                "youtube_url": youtube_url,
                "error": "no transcript found",
                "details": str(e),
            })
        except TranscriptsDisabled as e:
            print(f"FAILED (transcripts disabled)")
            failures.append({
                "date": date,
                "video_id": video_id,
                "youtube_url": youtube_url,
                "error": "transcripts disabled",
                "details": str(e),
            })
        except VideoUnavailable as e:
            print(f"FAILED (video unavailable)")
            failures.append({
                "date": date,
                "video_id": video_id,
                "youtube_url": youtube_url,
                "error": "video unavailable",
                "details": str(e),
            })
        except Exception as e:
            print(f"FAILED ({type(e).__name__}: {e})")
            failures.append({
                "date": date,
                "video_id": video_id,
                "youtube_url": youtube_url,
                "error": type(e).__name__,
                "details": str(e),
            })
    
    # Build and write index manifest
    index_file = TRANSCRIPTS_DIR / "index.json"
    
    # Collect all transcript metadata (including previously downloaded)
    all_transcripts = []
    for json_file in TRANSCRIPTS_DIR.glob("*.json"):
        if json_file.name == "index.json":
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                all_transcripts.append({
                    "date": data.get("date"),
                    "video_id": data.get("video_id"),
                    "segment_count": data.get("segment_count"),
                    "duration_seconds": data.get("duration_seconds"),
                    "youtube_url": data.get("youtube_url"),
                })
        except Exception:
            pass
    
    # Sort by date
    all_transcripts.sort(key=lambda x: x.get("date", ""))
    
    index_data = {
        "updated_at": datetime.now(ZoneInfo("America/New_York")).isoformat(),
        "total": len(all_transcripts),
        "transcripts": all_transcripts,
    }
    
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)
    
    # Build run summary
    run_summary = {
        "run_at": datetime.now(ZoneInfo("America/New_York")).isoformat(),
        "input_csv": str(INPUT_CSV),
        "transcripts_dir": str(TRANSCRIPTS_DIR),
        "total_rows": len(rows),
        "successes": len(successes),
        "failures": len(failures),
        "skipped": len(skipped),
        "success_details": successes,
        "failure_details": failures,
        "skipped_details": skipped,
    }
    
    return run_summary


def main():
    """Main entry point."""
    print(f"Fed Press Conference Transcript Downloader")
    print(f"=" * 50)
    
    # Initialize API (with cookies if available)
    api = get_transcript_api()
    print()
    
    try:
        run_summary = process_csv(api)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print(f"\nPlease run export_fed_press_conference_dates.py first to create the CSV.")
        return 1
    
    # Write run summary
    with open(RUN_SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(run_summary, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'=' * 50}")
    print(f"Summary:")
    print(f"  Total rows:  {run_summary['total_rows']}")
    print(f"  Successes:   {run_summary['successes']}")
    print(f"  Failures:    {run_summary['failures']}")
    print(f"  Skipped:     {run_summary['skipped']}")
    print(f"\nRun summary written to: {RUN_SUMMARY_FILE}")
    print(f"Transcripts saved to:   {TRANSCRIPTS_DIR}")
    
    return 0


if __name__ == "__main__":
    exit(main())

