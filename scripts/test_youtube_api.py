#!/usr/bin/env python3
"""
Test script: Debug YouTube API search for FOMC press conference.

Tests multiple filter combinations to find why search returns no results.
"""

import json
import urllib.request
import urllib.parse
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

TEST_DATE = "2024-01-31"
SEARCH_QUERY = "FOMC press conference January 31 2024"

# Official Federal Reserve YouTube channel
FED_CHANNEL_ID = "UCTvLj7u9uMQvZqafcWrh5JQ"

# =============================================================================
# Load API Key
# =============================================================================

KEYS_FILE = Path(__file__).parent.parent / "config" / "keys.env"

def load_api_key() -> str:
    with open(KEYS_FILE, "r") as f:
        for line in f:
            if line.strip().startswith("YOUTUBE_API_KEY="):
                return line.strip().split("=", 1)[1]
    raise ValueError("YOUTUBE_API_KEY not found in config/keys.env")

# =============================================================================
# Search Function
# =============================================================================

def search_youtube(api_key: str, params: dict) -> list:
    """Make YouTube API search request."""
    base_params = {
        "part": "snippet",
        "type": "video",
        "maxResults": 5,
        "key": api_key,
    }
    base_params.update(params)
    
    url = f"https://www.googleapis.com/youtube/v3/search?{urllib.parse.urlencode(base_params)}"
    
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("items", [])
    except urllib.error.HTTPError as e:
        print(f"  HTTP Error {e.code}: {e.read().decode('utf-8')}")
        return []

def print_results(items: list):
    """Print search results."""
    if not items:
        print("  -> No results found")
        return
    
    for i, item in enumerate(items, 1):
        video_id = item["id"]["videoId"]
        title = item["snippet"]["title"]
        channel = item["snippet"]["channelTitle"]
        published = item["snippet"]["publishedAt"][:10]
        print(f"  [{i}] {title}")
        print(f"      Channel: {channel}")
        print(f"      Published: {published}")
        print(f"      URL: https://www.youtube.com/watch?v={video_id}")
        print()

# =============================================================================
# Main Test
# =============================================================================

def main():
    api_key = load_api_key()
    print(f"API Key: {api_key[:10]}...{api_key[-4:]}")
    print(f"Query: {SEARCH_QUERY}")
    print("=" * 70)
    print()
    
    # Test 1: Most restrictive (all filters)
    print("TEST 1: All filters (channelId + videoCaption + date range)")
    print("-" * 70)
    from datetime import datetime, timedelta
    dt = datetime.strptime(TEST_DATE, "%Y-%m-%d")
    pub_after = (dt - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    pub_before = (dt + timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")
    
    results = search_youtube(api_key, {
        "q": SEARCH_QUERY,
        "channelId": FED_CHANNEL_ID,
        "videoCaption": "closedCaption",
        "publishedAfter": pub_after,
        "publishedBefore": pub_before,
    })
    print_results(results)
    
    # Test 2: Remove date filter
    print("TEST 2: channelId + videoCaption (NO date filter)")
    print("-" * 70)
    results = search_youtube(api_key, {
        "q": SEARCH_QUERY,
        "channelId": FED_CHANNEL_ID,
        "videoCaption": "closedCaption",
    })
    print_results(results)
    
    # Test 3: Remove caption filter
    print("TEST 3: channelId + date filter (NO videoCaption)")
    print("-" * 70)
    results = search_youtube(api_key, {
        "q": SEARCH_QUERY,
        "channelId": FED_CHANNEL_ID,
        "publishedAfter": pub_after,
        "publishedBefore": pub_before,
    })
    print_results(results)
    
    # Test 4: Only channelId
    print("TEST 4: Only channelId (no other filters)")
    print("-" * 70)
    results = search_youtube(api_key, {
        "q": SEARCH_QUERY,
        "channelId": FED_CHANNEL_ID,
    })
    print_results(results)
    
    # Test 5: No channelId, just caption filter
    print("TEST 5: videoCaption only (NO channelId - searches all YouTube)")
    print("-" * 70)
    results = search_youtube(api_key, {
        "q": SEARCH_QUERY,
        "videoCaption": "closedCaption",
    })
    print_results(results)
    
    # Test 6: No filters at all
    print("TEST 6: NO FILTERS (just the query - should match YouTube search)")
    print("-" * 70)
    results = search_youtube(api_key, {
        "q": SEARCH_QUERY,
    })
    print_results(results)
    
    print("=" * 70)
    print("DONE. Compare which test found results to identify the problematic filter.")
    
    return 0


if __name__ == "__main__":
    exit(main())
