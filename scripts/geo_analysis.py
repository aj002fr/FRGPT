#!/usr/bin/env python
"""
Geopolitical Events Analysis Dashboard
---------------------------------------
Generates an interactive HTML dashboard showing TY price paths for
geopolitical events with normalized paths, candlestick modals, and
distribution analysis.

Usage:
    python scripts/geo_analysis.py
    python scripts/geo_analysis.py --output custom_output.html
"""

import argparse
import json
import re
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from zoneinfo import ZoneInfo

# Add project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2
from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD
from rules.treasury_price_conversion import points_to_bps, points_to_ticks, ticks_to_bps
from src.servers.anomaly_detection.perplexity_search import search_market_fade

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_FILE = PROJECT_ROOT / "scripts" / "geo_analysis.html"
PERPLEXITY_CACHE_DEFAULT = PROJECT_ROOT / "workspace" / "analysis" / "geo_fade_cache.json"
SPIKE_WINDOW_MINUTES = 720
EXTENDED_WINDOW_DAYS = 2

# =============================================================================
# Event Data - Geopolitical Events (21-row comprehensive table)
# Times are stored in US/Eastern (ET). source_tz indicates original timezone.
# For multi-day events, we expand into separate entries per calendar day.
# =============================================================================

GEO_EVENTS_RAW = [
    # 1. Russia invades Ukraine (full-scale)
    {
        "id": 1,
        "name": "Russia invades Ukraine (full-scale)",
        "category": "Ukraine-Russia",
        "notes": "Russia launches large-scale invasion; Putin announces 'special military operation'",
        "summary": "Behavior: Sharp risk-off into early NY; 10y yield fell ~1.98% to ~1.85% then retraced most of the move. TY futures spiked up overnight/early US (roughly +20–25 ticks) and faded back to only modest gains by the close. Narrative: classic shock-then-fade as safe-haven demand faded when focus returned to Fed/inflation and no immediate global growth collapse.",
        "date": "2022-02-24",
        "event_time_local": "05:00",  # Moscow time for first strikes
        "source_tz": "Europe/Moscow",
        "day_of_week": "Thursday",
    },
    # 2. Major Ukrainian counteroffensive begins
    {
        "id": 2,
        "name": "Ukraine counteroffensive begins",
        "category": "Ukraine-Russia",
        "notes": "Ukraine starts large counteroffensive in south (Kherson front), later expanding east",
        "summary": "Behavior: War ongoing and partly priced; 10y yields moved only a few bps with macro/Fed as main driver. TY futures traded a narrow range (few ticks) with no clean, news-timed spike or fade attributable to the counteroffensive.",
        "date": "2022-08-29",
        "event_time_local": "",  # Early morning local, no single public clock time
        "source_tz": "Europe/Kiev",
        "day_of_week": "Monday",
    },
    # 3. Russia announces annexation of four Ukrainian regions
    {
        "id": 3,
        "name": "Russia annexes four Ukrainian regions",
        "category": "Ukraine-Russia",
        "notes": "Putin signs 'accession treaties' for Donetsk, Luhansk, Kherson, Zaporizhzhia",
        "summary": "Behavior: Escalation but not a surprise in direction; 10y yields fluctuated only a few bps. TY futures had a modest up bias (2–4 ticks), inside typical daily volatility with no pronounced spike-then-fade.",
        "date": "2022-09-30",
        "event_time_local": "12:00",  # Ceremony held Moscow daytime
        "source_tz": "Europe/Moscow",
        "day_of_week": "Friday",
    },
    # 4. Kakhovka Dam destruction
    {
        "id": 4,
        "name": "Kakhovka Dam destruction",
        "category": "Ukraine-Russia",
        "notes": "Massive breach of Russian-held Kakhovka Dam on Dnipro River causes Kherson flooding",
        "summary": "Behavior: Humanitarian/military shock, not global macro; small net 10y yield changes. TY futures saw a small safe-haven uptick that bled back; directionally higher but only by a few ticks.",
        "date": "2023-06-06",
        "event_time_local": "05:00",  # Early morning Ukraine time
        "source_tz": "Europe/Kiev",
        "day_of_week": "Tuesday",
    },
    # 5. Hamas 7 Oct attack on Israel
    {
        "id": 5,
        "name": "Hamas Oct 7 attack on Israel",
        "category": "Israel-Hamas",
        "notes": "Hamas launches large-scale rocket and cross-border assault into southern Israel, triggering Gaza war",
        "summary": "Behavior: Weekend attack; by Monday, risk reassessment and oil mattered but US data/Fed still central. TY futures had a mild up move in Sunday night/Monday Asia, then most of it eased by late Monday, leaving only a modest net change.",
        "date": "2023-10-07",
        "event_time_local": "06:30",  # Shortly after dawn local time
        "source_tz": "Asia/Jerusalem",
        "day_of_week": "Saturday",
    },
    # 6. Start of Houthi Red Sea attacks
    {
        "id": 6,
        "name": "Houthi Red Sea attacks begin",
        "category": "Houthis-Red Sea",
        "notes": "Yemen's Houthis begin targeting commercial shipping in Red Sea after Gaza war begins",
        "summary": "Behavior: Shipping/energy disruption risk; 10y yields stayed in a small range. TY futures showed a slight up bid on initial hits, then faded as the episode was treated as localized; net close change small.",
        "date": "2023-10-19",
        "event_time_local": "",  # Times differ by ship
        "source_tz": "Asia/Aden",
        "day_of_week": "Thursday",
    },
    # 7. US announces Red Sea naval coalition
    {
        "id": 7,
        "name": "US announces Red Sea naval coalition",
        "category": "Houthis-Red Sea",
        "notes": "US and partners announce Operation Prosperity Guardian to protect Red Sea shipping",
        "summary": "Behavior: Framed as containment/de-escalation; cross-asset reaction muted. TY futures saw a mild down (risk-on) bias within a standard day's chop; no distinct spike/fade.",
        "date": "2023-12-18",  # Mid-Dec 2023, using representative date
        "event_time_local": "14:00",  # US daytime
        "source_tz": "America/New_York",
        "day_of_week": "Monday",
    },
    # 8. Houthis expand attacks to Indian Ocean - multi-day, use first incident
    {
        "id": 8,
        "name": "Houthis expand attacks to Indian Ocean",
        "category": "Houthis-Red Sea",
        "notes": "Houthis claim first attacks against cargo ships beyond Red Sea, including Indian Ocean",
        "summary": "Behavior: Extended shipping-risk story; energy/freight more direct than rates. TY futures saw a short-lived up bid overnight/early session that retraced quickly; end-of-day change marginal.",
        "date": "2024-03-04",  # Early March 2024, representative date
        "event_time_local": "",  # Individual attack times vary
        "source_tz": "Asia/Aden",
        "day_of_week": "Monday",
    },
    # 9. Israel-Iran April 2024 exchange (Iranian barrage) - Night 13-14 Apr
    {
        "id": 9,
        "name": "Iran launches drone/missile attack on Israel",
        "category": "Israel-Iran",
        "notes": "Iran's Operation 'True Promise' - large drone/missile attack on Israel",
        "summary": "Behavior: Risk-off into Friday; 10y yields fell ~10 bps as traders hedged the expected response. TY futures rallied strongly into Friday, then sold off Sunday night/Monday as interception success reduced tail risk, creating a near-full fade of the pre-event rally.",
        "date": "2024-04-14",  # Launched late night 13 Apr into early 14 Apr
        "event_time_local": "01:00",  # Early morning 14 Apr local
        "source_tz": "Asia/Jerusalem",
        "day_of_week": "Sunday",
        "multi_day": ["2024-04-13", "2024-04-14"],  # Spans two days
    },
    # 10. Israel strikes Iran nuclear-linked sites (April)
    {
        "id": 10,
        "name": "Israel retaliates - strikes near Isfahan",
        "category": "Israel-Iran",
        "notes": "Israel retaliates with strikes near Isfahan, hitting military sites linked to nuclear infrastructure",
        "summary": "Behavior: Controlled tit-for-tat; limited scope reduced tail-risk pricing. TY futures popped up briefly on initial headlines, then drifted lower as escalation fears faded; net move small by the close.",
        "date": "2024-04-19",
        "event_time_local": "04:00",  # Early morning local near Isfahan
        "source_tz": "Asia/Tehran",
        "day_of_week": "Friday",
    },
    # 11. US House passes large Ukraine aid bill
    {
        "id": 11,
        "name": "US House passes $61B Ukraine aid bill",
        "category": "Ukraine-Russia",
        "notes": "US House approves major Ukraine aid package after months of delay",
        "summary": "Behavior: Largely political/expectations-based; macro still dominant. TY futures showed only modest intraday moves, with no distinct spike/fade tied to the vote.",
        "date": "2024-04-19",
        "event_time_local": "16:00",  # House vote afternoon/evening Washington
        "source_tz": "America/New_York",
        "day_of_week": "Friday",
    },
    # 12. Russia launches new Kharkiv-region offensive
    {
        "id": 12,
        "name": "Russia launches Kharkiv offensive",
        "category": "Ukraine-Russia",
        "notes": "Russia opens new front with ground attacks into Kharkiv region in northeast Ukraine",
        "summary": "Behavior: Expansion within entrenched war; US macro and Fed path dominated 10y pricing. TY futures direction matched the day's data; no discrete spike/fade tied to the offensive.",
        "date": "2024-05-10",
        "event_time_local": "08:00",  # Attacks morning through day Ukraine time
        "source_tz": "Europe/Kiev",
        "day_of_week": "Friday",
    },
    # 13. Ukraine's cross-border Kursk incursion begins - Night 6-7 Aug
    {
        "id": 13,
        "name": "Ukraine's Kursk incursion begins",
        "category": "Ukraine-Russia",
        "notes": "Ukrainian forces cross into Russia's Kursk region, starting campaign that later reached 28+ settlements",
        "summary": "Behavior: Unusual headline but viewed as geographically contained; no abrupt change in global growth/inflation outlook. TY futures stayed within typical daily ranges and macro releases dominated.",
        "date": "2024-08-06",
        "event_time_local": "23:00",  # Night of 6 Aug / early 7 Aug
        "source_tz": "Europe/Moscow",
        "day_of_week": "Tuesday",
        "multi_day": ["2024-08-06", "2024-08-07"],
    },
    # 14. Russia's Kursk governor says Ukraine holds 28 settlements
    {
        "id": 14,
        "name": "Kursk governor: Ukraine holds 28 settlements",
        "category": "Ukraine-Russia",
        "notes": "Acting governor Smirnov says Ukraine holds 28 settlements and advanced ~12 km into Kursk region",
        "summary": "Behavior: Headline extension, but no clear repricing in global rates. TY futures moved within standard daily volatility with only brief, hard-to-isolate blips.",
        "date": "2024-08-12",
        "event_time_local": "12:00",  # Reported during day
        "source_tz": "Europe/Moscow",
        "day_of_week": "Monday",
    },
    # 15. Kursk chemical-weapons allegation
    {
        "id": 15,
        "name": "Kursk chemical weapons allegation",
        "category": "Ukraine-Russia",
        "notes": "Russian state media alleges Ukraine used toxic agents in Kursk operations",
        "summary": "Behavior: Escalatory rhetoric but not seen as a macro inflection. TY futures traded with the day's data and issuance; no systematic spike/fade.",
        "date": "2024-10-06",
        "event_time_local": "",  # Article timestamped that day
        "source_tz": "Europe/Moscow",
        "day_of_week": "Sunday",
    },
    # 16. Zelenskyy Washington / Congress visit (NATO week)
    {
        "id": 16,
        "name": "Zelenskyy Washington visit (NATO week)",
        "category": "Ukraine-Russia",
        "notes": "Zelenskyy returns to Washington, meets US lawmakers and thanks Congress during NATO summit",
        "summary": "Behavior: Highly anticipated political theater rather than surprise; debt/aid already priced. TY futures followed the day's US data/curve flows with no event-time dislocation.",
        "date": "2024-07-10",
        "event_time_local": "10:00",  # Meetings morning-midday Washington
        "source_tz": "America/New_York",
        "day_of_week": "Wednesday",
    },
    # 17. Israel-Iran "Twelve-Day War" begins
    {
        "id": 17,
        "name": "Israel-Iran 'Twelve-Day War' begins",
        "category": "Israel-Iran",
        "notes": "Israel launches large, coordinated strikes on Iran, starting short but intense war",
        "summary": "Behavior: Risk assets wobbled and energy rallied, but the US macro outlook did not reset. TY futures popped up on the initial hours, then drifted lower over the next 1–2 sessions as containment became clearer; net change moderate, not crisis-scale.",
        "date": "2025-06-13",
        "event_time_local": "05:30",  # Pre-dawn/early morning
        "source_tz": "Asia/Jerusalem",
        "day_of_week": "Friday",
    },
    # 18. US enters Iran-Israel war
    {
        "id": 18,
        "name": "US enters Iran-Israel war",
        "category": "Israel-Iran",
        "notes": "US conducts strikes on Iranian nuclear-related facilities with bombers/missiles",
        "summary": "Behavior: Tail risk rose briefly but markets read it as targeted/limited. TY futures had an initial up impulse (flight-to-safety) and then gave back part of it intraday/next day as scope appeared contained.",
        "date": "2025-06-22",
        "event_time_local": "04:00",  # Early morning Iran time
        "source_tz": "Asia/Tehran",
        "day_of_week": "Sunday",
    },
    # 19. Ceasefire in Iran-Israel war takes hold
    {
        "id": 19,
        "name": "Iran-Israel ceasefire takes hold",
        "category": "Israel-Iran",
        "notes": "After announcements previous day, ceasefire effectively stops major strikes",
        "summary": "Behavior: De-escalation lowered tail risk. TY futures drifted down as safe-haven demand unwound, but the move was incremental since much of the risk premium had already been pared.",
        "date": "2025-06-24",
        "event_time_local": "04:00",  # Around 04:00 GMT
        "source_tz": "UTC",
        "day_of_week": "Tuesday",
    },
    # 20. Houthis resume major Red Sea attacks (pre-war phase) - 6-7 Jun 2025
    {
        "id": 20,
        "name": "Houthis resume major Red Sea attacks",
        "category": "Houthis-Red Sea",
        "notes": "After a lull, Houthis renew high-intensity attacks including Magic Seas and Eternity C",
        "summary": "Behavior: Renewed attacks but risk premium already embedded; oil/freight more affected than rates. TY futures saw modest up bursts around first reports, then mean-reverted; end-of-day changes small.",
        "date": "2025-06-06",
        "event_time_local": "02:00",  # Attacks at night/early morning
        "source_tz": "Asia/Aden",
        "day_of_week": "Friday",
        "multi_day": ["2025-06-06", "2025-06-07"],
    },
    # 21. US attacks Venezuela ("Operation Absolute Resolve")
    {
        "id": 21,
        "name": "US attacks Venezuela (Op Absolute Resolve)",
        "category": "Venezuela",
        "notes": "Trump authorizes; US forces execute operation to seize President Maduro. Order 22:46 EST 2 Jan; raid 02:00 VET 3 Jan",
        "summary": "Behavior: Localized operation, not systemic. TY futures saw a small risk-off uptick around headlines that faded quickly; multi-day impact negligible vs macro flows.",
        "date": "2026-01-03",
        "event_time_local": "02:00",  # Raid time in Caracas
        "source_tz": "America/Caracas",
        "day_of_week": "Saturday",
        "multi_day": ["2026-01-02", "2026-01-03"],
    },
]

# =============================================================================
# Timezone Conversion & Multi-Day Expansion
# =============================================================================

ET = ZoneInfo("America/New_York")

def convert_to_et(date_str: str, time_str: str, source_tz_str: str) -> Optional[datetime]:
    """
    Convert a local date+time in source timezone to US/Eastern.
    Returns None if time_str is empty (unknown time).
    """
    if not time_str:
        return None
    
    try:
        if source_tz_str == "UTC":
            source_tz = ZoneInfo("UTC")
        else:
            source_tz = ZoneInfo(source_tz_str)
        
        # Parse local datetime
        local_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        local_dt = local_dt.replace(tzinfo=source_tz)
        
        # Convert to ET
        et_dt = local_dt.astimezone(ET)
        return et_dt
    except Exception as e:
        logger.warning(f"Failed to convert {date_str} {time_str} from {source_tz_str}: {e}")
        return None


def expand_events() -> List[Dict[str, Any]]:
    """
    Expand GEO_EVENTS_RAW into a list of single-day events.
    Multi-day events are split into one entry per calendar day.
    Each event gets event_start/event_end in ET (if time is known).
    """
    expanded = []
    
    for raw_event in GEO_EVENTS_RAW:
        # Get dates to process
        if "multi_day" in raw_event and raw_event["multi_day"]:
            dates_to_process = raw_event["multi_day"]
        else:
            dates_to_process = [raw_event["date"]]
        
        for i, date_str in enumerate(dates_to_process):
            event = {
                "id": raw_event["id"],
                "name": raw_event["name"],
                "category": raw_event["category"],
                "notes": raw_event["notes"],
                "source_date": raw_event["date"],  # Original date from table
                "date": date_str,  # Date to plot (may differ for multi-day)
                "day_of_week": raw_event.get("day_of_week", ""),
                "source_tz": raw_event.get("source_tz", ""),
                "event_start": "",
                "event_end": "",
                "time_unknown": True,
            }
            
            # Convert time to ET if available
            time_str = raw_event.get("event_time_local", "")
            if time_str:
                # For multi-day events, only use time on first day
                if i == 0:
                    et_dt = convert_to_et(date_str, time_str, raw_event.get("source_tz", "UTC"))
                    if et_dt:
                        event["event_start"] = et_dt.strftime("%Y-%m-%d %H:%M:%S")
                        # Event end = 2 hours after start (reasonable default window)
                        end_dt = et_dt + timedelta(hours=2)
                        event["event_end"] = end_dt.strftime("%Y-%m-%d %H:%M:%S")
                        event["time_unknown"] = False
                else:
                    # For subsequent days of multi-day events, no specific time
                    event["notes"] = f"[Day {i+1}] " + event["notes"]
            
            # Add suffix for multi-day events
            if len(dates_to_process) > 1:
                event["name"] = f"{event['name']} ({date_str})"
            
            expanded.append(event)
    
    return expanded


# Expanded events ready for processing
GEO_EVENTS = expand_events()


def is_us_involved(event: Dict[str, Any]) -> bool:
    """Heuristic flag for US-involved events based on name/notes."""
    text = f"{event.get('name', '')} {event.get('notes', '')}"
    pattern = r"\b(U\.S\.|US|United States|America|American|Washington|Congress|Pentagon|White House|State Department|Treasury)\b"
    return re.search(pattern, text, re.IGNORECASE) is not None


def get_db_connection():
    """Get PostgreSQL connection."""
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD
    )


def find_nearest_trading_date(date_str: str, conn, max_days: int = 5) -> Optional[str]:
    """
    Find the nearest calendar day with TY trading data.
    Searches forward from the given date up to max_days.
    Returns the date string if found, else None.
    """
    cur = conn.cursor()
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    
    for offset in range(max_days + 1):
        check_date = dt + timedelta(days=offset)
        check_start = check_date
        check_end = check_date + timedelta(days=1)
        
        cur.execute("""
            SELECT COUNT(*) FROM ohlcv_1m 
            WHERE symbol = 'TY' 
              AND ts_event >= %s 
              AND ts_event < %s
        """, (check_start, check_end))
        
        count = cur.fetchone()[0]
        if count >= 100:  # At least 100 bars = meaningful data
            cur.close()
            return check_date.strftime('%Y-%m-%d')
    
    cur.close()
    return None


def fetch_price_data_for_date(date_str: str, conn) -> Dict[str, Any]:
    """
    Fetch 1-minute TY price data for a calendar day (midnight to midnight).
    """
    cur = conn.cursor()
    
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    # Calendar day: midnight to midnight
    query_start = dt  # midnight start of day
    query_end = dt + timedelta(days=1)  # midnight end of day
    
    cur.execute("""
        SELECT ts_event, open, high, low, close, volume
        FROM ohlcv_1m 
        WHERE symbol = 'TY' 
          AND ts_event >= %s 
          AND ts_event < %s
        ORDER BY ts_event
    """, (query_start, query_end))
    
    rows = cur.fetchall()
    cur.close()
    
    if not rows:
        return {'timestamps': [], 'opens': [], 'highs': [], 'lows': [], 'closes': [], 'volumes': []}
    
    return {
        'timestamps': [row[0].isoformat() for row in rows],
        'opens': [float(row[1]) for row in rows],
        'highs': [float(row[2]) for row in rows],
        'lows': [float(row[3]) for row in rows],
        'closes': [float(row[4]) for row in rows],
        'volumes': [int(row[5]) if row[5] else 0 for row in rows],
    }


def fetch_price_data_for_range(date_str: str, conn, days_before: int = 2, days_after: int = 2) -> Dict[str, Any]:
    """
    Fetch 1-minute TY price data for ±N days around a date (for candlestick modal).
    """
    cur = conn.cursor()
    
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    query_start = dt - timedelta(days=days_before)
    query_end = dt + timedelta(days=days_after + 1)  # +1 to include the last day
    
    cur.execute("""
        SELECT ts_event, open, high, low, close, volume
        FROM ohlcv_1m 
        WHERE symbol = 'TY' 
          AND ts_event >= %s 
          AND ts_event < %s
        ORDER BY ts_event
    """, (query_start, query_end))
    
    rows = cur.fetchall()
    cur.close()
    
    if not rows:
        return {'timestamps': [], 'opens': [], 'highs': [], 'lows': [], 'closes': [], 'volumes': []}
    
    return {
        'timestamps': [row[0].isoformat() for row in rows],
        'opens': [float(row[1]) for row in rows],
        'highs': [float(row[2]) for row in rows],
        'lows': [float(row[3]) for row in rows],
        'closes': [float(row[4]) for row in rows],
        'volumes': [int(row[5]) if row[5] else 0 for row in rows],
    }


def fetch_economic_events_for_date(date_str: str) -> List[Dict[str, Any]]:
    """Fetch economic events from the SQLite database for a given date."""
    import sqlite3
    
    db_path = PROJECT_ROOT / "workspace" / "economic_events.db"
    if not db_path.exists():
        logger.warning(f"Economic events DB not found: {db_path}")
        return []
    
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    
    # Fetch events for the date
    cur.execute("""
        SELECT event_name, event_date, importance, actual, consensus, previous
        FROM economic_events
        WHERE date(event_date) = ?
          AND country LIKE '%United States%'
        ORDER BY event_date
    """, (date_str,))
    
    rows = cur.fetchall()
    conn.close()
    
    events = []
    for row in rows:
        events.append({
            'name': row[0],
            'timestamp': row[1],
            'importance': row[2] or 'medium',
            'actual': row[3],
            'consensus': row[4],
            'previous': row[5],
        })
    
    return events


def fetch_economic_events_for_range(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """Fetch economic events between start_date and end_date (inclusive)."""
    import sqlite3

    db_path = PROJECT_ROOT / "workspace" / "economic_events.db"
    if not db_path.exists():
        logger.warning(f"Economic events DB not found: {db_path}")
        return []

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("""
        SELECT event_name, event_date, importance, actual, consensus, previous
        FROM economic_events
        WHERE date(event_date) >= ?
          AND date(event_date) <= ?
          AND country LIKE '%United States%'
          AND importance IN ('high', 'medium')
        ORDER BY event_date
    """, (start_date, end_date))

    rows = cur.fetchall()
    conn.close()

    events = []
    for row in rows:
        events.append({
            'name': row[0],
            'timestamp': row[1],
            'importance': row[2] or 'medium',
            'actual': row[3],
            'consensus': row[4],
            'previous': row[5],
        })

    return events


def compute_window_stats(closes: List[float], window_size: int) -> Dict[str, float]:
    """Compute max move statistics for a given window size (in minutes).
    
    Returns max positive, max negative, mean, and percentiles.
    """
    import numpy as np
    
    if len(closes) < 2:
        return {}
    
    ref_price = closes[0]
    
    # Get closes within window
    window_closes = closes[:min(window_size, len(closes))]
    
    # Compute bps changes from reference
    bps_changes = [points_to_bps(c - ref_price) for c in window_closes]
    
    if not bps_changes:
        return {}
    
    arr = np.array(bps_changes)
    return {
        'max_up': round(float(np.max(arr)), 2),
        'max_down': round(float(np.min(arr)), 2),
        'mean': round(float(np.mean(arr)), 2),
        'std': round(float(np.std(arr)), 2),
        'p5': round(float(np.percentile(arr, 5)), 2),
        'p25': round(float(np.percentile(arr, 25)), 2),
        'p50': round(float(np.percentile(arr, 50)), 2),
        'p75': round(float(np.percentile(arr, 75)), 2),
        'p95': round(float(np.percentile(arr, 95)), 2),
        'final': round(float(bps_changes[-1]), 2) if bps_changes else 0,
    }


def compute_return_vol_for_window(price_data: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Compute return and volatility in bps for a given price_data window."""
    closes = price_data.get('closes', [])
    highs = price_data.get('highs', [])
    lows = price_data.get('lows', [])
    if len(closes) < 2:
        return None
    start_price = closes[0]
    end_price = closes[-1]
    return {
        'return_bps': round(points_to_bps(end_price - start_price), 2),
        'vol_bps': round(points_to_bps(max(highs) - min(lows)), 2) if highs and lows else 0.0,
    }


def slice_price_data_by_time(price_data: Dict[str, Any], start_ts: str, end_ts: str) -> Optional[Dict[str, Any]]:
    """Slice price_data to timestamps between start_ts and end_ts (inclusive)."""
    timestamps = price_data.get('timestamps', [])
    if not timestamps:
        return None
    try:
        start_dt = datetime.strptime(start_ts, '%Y-%m-%d %H:%M:%S')
        end_dt = datetime.strptime(end_ts, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    # Normalize to naive timestamps for comparison
    start_dt = start_dt.replace(tzinfo=None)
    end_dt = end_dt.replace(tzinfo=None)

    indices = []
    for i, ts in enumerate(timestamps):
        ts_dt = datetime.fromisoformat(ts.replace('Z', '').replace('+00:00', '')).replace(tzinfo=None)
        if start_dt <= ts_dt <= end_dt:
            indices.append(i)

    if len(indices) < 2:
        return None

    start_idx = indices[0]
    end_idx = indices[-1] + 1
    return {
        'timestamps': timestamps[start_idx:end_idx],
        'opens': price_data.get('opens', [])[start_idx:end_idx],
        'highs': price_data.get('highs', [])[start_idx:end_idx],
        'lows': price_data.get('lows', [])[start_idx:end_idx],
        'closes': price_data.get('closes', [])[start_idx:end_idx],
        'volumes': price_data.get('volumes', [])[start_idx:end_idx],
    }


def detect_spike_window(
    price_data: Dict[str, Any],
    window_minutes: int = SPIKE_WINDOW_MINUTES,
    anchor_ts: Optional[str] = None,
    pre_minutes: int = 120,
    post_minutes: int = 360,
) -> Optional[Dict[str, Any]]:
    """Find the window with the largest absolute net move."""
    timestamps = price_data.get('timestamps', [])
    closes = price_data.get('closes', [])
    highs = price_data.get('highs', [])
    lows = price_data.get('lows', [])
    if len(closes) < window_minutes + 1 or len(timestamps) != len(closes):
        return None

    ts_list = [datetime.fromisoformat(ts.replace('Z', '')).replace(tzinfo=None) for ts in timestamps]
    anchor_start = anchor_end = None
    if anchor_ts:
        anchor_dt = datetime.strptime(anchor_ts, '%Y-%m-%d %H:%M:%S').replace(tzinfo=None)
        anchor_start = anchor_dt - timedelta(minutes=pre_minutes)
        anchor_end = anchor_dt + timedelta(minutes=post_minutes)

    best = None
    max_idx = len(closes) - window_minutes - 1
    for i in range(max_idx + 1):
        if anchor_start and (ts_list[i] < anchor_start or ts_list[i + window_minutes] > anchor_end):
            continue
        net_move = closes[i + window_minutes] - closes[i]
        score = abs(net_move)
        if best is None or score > best['score']:
            window_high = max(highs[i:i + window_minutes + 1]) if highs else max(closes[i:i + window_minutes + 1])
            window_low = min(lows[i:i + window_minutes + 1]) if lows else min(closes[i:i + window_minutes + 1])
            best = {
                'score': score,
                'start_idx': i,
                'end_idx': i + window_minutes,
                'window_high': window_high,
                'window_low': window_low,
            }

    return best


def compute_spike_stats(
    price_data: Dict[str, Any],
    anchor_ts: Optional[str],
    window_minutes: int = SPIKE_WINDOW_MINUTES,
    pre_minutes: int = 120,
    post_minutes: int = 360,
) -> Dict[str, Any]:
    """Compute spike-window return/volatility stats (bps)."""
    closes = price_data.get('closes', [])
    available_minutes = len(closes) - 1
    if available_minutes < 1:
        return {'has_data': False}

    # If we don't have enough data for the target window, shrink the window.
    window_minutes = min(window_minutes, available_minutes)
    if window_minutes < 30:
        return {'has_data': False}

    spike = detect_spike_window(
        price_data,
        window_minutes,
        anchor_ts=anchor_ts,
        pre_minutes=pre_minutes,
        post_minutes=post_minutes,
    )
    if not spike:
        return {'has_data': False}

    start_idx = spike['start_idx']
    end_idx = spike['end_idx']
    start_price = closes[start_idx]
    end_price = closes[end_idx]
    window_closes = closes[start_idx:end_idx + 1]
    max_up = max(window_closes) - start_price
    max_down = min(window_closes) - start_price
    spike_return_bps = round(points_to_bps(end_price - start_price), 2)
    spike_max_up_bps = round(points_to_bps(max_up), 2)
    spike_max_down_bps = round(points_to_bps(max_down), 2)

    # Fade = how much the move retraced from the max excursion within the spike window.
    # If the move is up, fade = max_up - final_return. If down, fade = final_return - max_down.
    if spike_return_bps >= 0:
        fade_bps = round(max(0.0, spike_max_up_bps - spike_return_bps), 2)
        denom = abs(spike_max_up_bps) if spike_max_up_bps else None
    else:
        fade_bps = round(max(0.0, spike_return_bps - spike_max_down_bps), 2)
        denom = abs(spike_max_down_bps) if spike_max_down_bps else None
    fade_pct = round((fade_bps / denom) * 100.0, 1) if denom else None
    max_move_bps = round(max(spike_max_up_bps, abs(spike_max_down_bps)), 2)

    return {
        'has_data': True,
        'spike_start_ts': price_data['timestamps'][start_idx],
        'spike_end_ts': price_data['timestamps'][end_idx],
        'spike_window_minutes': window_minutes,
        'spike_return_bps': spike_return_bps,
        'spike_volatility_bps': round(points_to_bps(spike['window_high'] - spike['window_low']), 2),
        'spike_max_up_bps': spike_max_up_bps,
        'spike_max_down_bps': spike_max_down_bps,
        'spike_max_move_bps': max_move_bps,
        'fade_bps': fade_bps,
        'fade_pct': fade_pct,
    }


def compute_per_event_stats(price_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute per-event statistics for interpretability.
    Returns day return, volatility, max moves in different windows.
    All values in bps.
    """
    import numpy as np
    
    closes = price_data.get('closes', [])
    highs = price_data.get('highs', [])
    lows = price_data.get('lows', [])
    
    if len(closes) < 2:
        return {
            'day_return': 0,
            'day_volatility': 0,
            'max_up_day': 0,
            'max_down_day': 0,
            'max_up_5m': 0,
            'max_down_5m': 0,
            'max_up_1h': 0,
            'max_down_1h': 0,
            'has_data': False,
        }
    
    ref_price = closes[0]
    
    # Day return = close - open in bps
    day_return = round(points_to_bps(closes[-1] - ref_price), 1)
    
    # Day volatility = (max high - min low) in bps
    day_volatility = round(points_to_bps(max(highs) - min(lows)), 1) if highs and lows else 0
    
    # Full day max moves
    bps_changes = [points_to_bps(c - ref_price) for c in closes]
    max_up_day = round(max(bps_changes), 1)
    max_down_day = round(min(bps_changes), 1)
    
    # 5-min window max moves
    bps_5m = bps_changes[:min(5, len(bps_changes))]
    max_up_5m = round(max(bps_5m), 1) if bps_5m else 0
    max_down_5m = round(min(bps_5m), 1) if bps_5m else 0
    
    # 1-hour window max moves
    bps_1h = bps_changes[:min(60, len(bps_changes))]
    max_up_1h = round(max(bps_1h), 1) if bps_1h else 0
    max_down_1h = round(min(bps_1h), 1) if bps_1h else 0
    
    return {
        'day_return': day_return,
        'day_volatility': day_volatility,
        'max_up_day': max_up_day,
        'max_down_day': max_down_day,
        'max_up_5m': max_up_5m,
        'max_down_5m': max_down_5m,
        'max_up_1h': max_up_1h,
        'max_down_1h': max_down_1h,
        'has_data': True,
    }


def compute_returns_and_volatility(events_data: List[Dict]) -> Dict[str, Any]:
    """Compute returns, volatility, and window statistics for distribution plots.
    
    Uses treasury_price_conversion.py:
    - 1 point = 32 ticks = 8 bps
    - points_to_bps(price_move) converts decimal price move to bps
    """
    import numpy as np
    
    returns = []
    volatilities = []
    
    # Window statistics aggregated across all events
    window_stats = {
        '5min': {'max_up': [], 'max_down': [], 'final': []},
        '1hour': {'max_up': [], 'max_down': [], 'final': []},
        'session': {'max_up': [], 'max_down': [], 'final': []},
    }
    
    for event in events_data:
        price_data = event.get('price_data', {})
        closes = price_data.get('closes', [])
        ret_bps = event.get('spike_return_bps')
        vol_bps = event.get('spike_volatility_bps')

        if ret_bps is not None:
            returns.append(ret_bps)
        if vol_bps is not None:
            volatilities.append(vol_bps)

        if len(closes) >= 2:
            for window_name, window_mins in [('5min', 5), ('1hour', 60), ('session', len(closes))]:
                stats = compute_window_stats(closes, window_mins)
                if stats:
                    window_stats[window_name]['max_up'].append(stats['max_up'])
                    window_stats[window_name]['max_down'].append(stats['max_down'])
                    window_stats[window_name]['final'].append(stats['final'])
    
    # Aggregate window stats
    def agg_stats(values: List[float]) -> Dict[str, float]:
        if not values:
            return {}
        arr = np.array(values)
        return {
            'mean': round(float(np.mean(arr)), 2),
            'std': round(float(np.std(arr)), 2),
            'min': round(float(np.min(arr)), 2),
            'max': round(float(np.max(arr)), 2),
            'p5': round(float(np.percentile(arr, 5)), 2),
            'p25': round(float(np.percentile(arr, 25)), 2),
            'p50': round(float(np.percentile(arr, 50)), 2),
            'p75': round(float(np.percentile(arr, 75)), 2),
            'p95': round(float(np.percentile(arr, 95)), 2),
        }
    
    aggregated_window_stats = {}
    for window_name, metrics in window_stats.items():
        aggregated_window_stats[window_name] = {
            'max_up': agg_stats(metrics['max_up']),
            'max_down': agg_stats(metrics['max_down']),
            'final': agg_stats(metrics['final']),
            'raw': {
                'max_up': metrics['max_up'],
                'max_down': metrics['max_down'],
                'final': metrics['final'],
            }
        }
    
    return {
        'returns': returns,
        'volatilities': volatilities,
        'window_stats': aggregated_window_stats,
    }


def load_perplexity_cache(cache_path: Path) -> Dict[str, Any]:
    """Load Perplexity fade cache from disk."""
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Failed to read Perplexity cache: {exc}")
        return {}


def save_perplexity_cache(cache_path: Path, cache: Dict[str, Any]) -> None:
    """Persist Perplexity fade cache to disk."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def build_fade_context(event: Dict[str, Any], spike_stats: Dict[str, Any]) -> Optional[str]:
    """Build context string for Perplexity fade search."""
    if not spike_stats or not spike_stats.get("has_data"):
        return None
    return (
        f"Spike return {spike_stats.get('spike_return_bps')} bps; "
        f"spike window {spike_stats.get('spike_start_ts')} → {spike_stats.get('spike_end_ts')}."
    )


def parse_narrative_anchor(date_str: str, window_str: Optional[str]) -> Optional[str]:
    """Extract an HH:MM anchor (ET) from a narrative window string."""
    if not window_str:
        return None
    text = window_str.lower()

    # Match 12-hour time like "2pm" or "2:30 pm"
    match_12h = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if match_12h:
        hour = int(match_12h.group(1))
        minute = int(match_12h.group(2) or 0)
        suffix = match_12h.group(3)
        if suffix == "pm" and hour != 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        return f"{date_str} {hour:02d}:{minute:02d}:00"

    # Match 24-hour time like "14:30"
    match_24h = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if match_24h:
        hour = int(match_24h.group(1))
        minute = int(match_24h.group(2))
        return f"{date_str} {hour:02d}:{minute:02d}:00"

    # Handle simple "noon"/"midnight"
    if "noon" in text:
        return f"{date_str} 12:00:00"
    if "midnight" in text:
        return f"{date_str} 00:00:00"

    return None


def get_perplexity_fade(
    event: Dict[str, Any],
    spike_stats: Dict[str, Any],
    cache: Dict[str, Any],
    cache_path: Path,
    force_refresh: bool = False,
) -> Optional[Dict[str, Any]]:
    """Fetch Perplexity fade summary with local caching."""
    key = f"{event.get('date')}|{event.get('name')}"
    if not force_refresh and key in cache:
        return cache[key]

    context = build_fade_context(event, spike_stats)
    result = search_market_fade(
        date=event.get("date"),
        event_name=event.get("name"),
        context=context,
    )

    if not result.get("success"):
        error = result.get("error", "")
        if "No Perplexity API key" in error:
            return None

    cache[key] = {
        **result,
        "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_perplexity_cache(cache_path, cache)
    return cache[key]


def generate_html(events_data: List[Dict], distributions: Dict) -> str:
    """Generate the HTML dashboard."""
    
    # Serialize data for JavaScript
    events_json = json.dumps(events_data, indent=2)
    distributions_json = json.dumps(distributions, indent=2)
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Geopolitical Events Analysis - TY Price Paths</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Space+Grotesk:wght@400;500;600;700&display=swap');
        
        :root {{
            --bg-primary: #0a0f1a;
            --bg-secondary: #111827;
            --bg-tertiary: #1f2937;
            --bg-card: #1a1f2e;
            --text-primary: #f9fafb;
            --text-secondary: #9ca3af;
            --text-muted: #6b7280;
            --border-color: #374151;
            --border-light: #4b5563;
            --accent-teal: #14b8a6;
            --accent-cyan: #22d3ee;
            --accent-green: #22c55e;
            --accent-orange: #f97316;
            --accent-red: #ef4444;
            --accent-blue: #3b82f6;
            --accent-purple: #a855f7;
            --accent-amber: #fbbf24;
        }}
        
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{
            font-family: 'Space Grotesk', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg-primary);
            background-image: 
                radial-gradient(ellipse at top left, rgba(20, 184, 166, 0.08) 0%, transparent 50%),
                radial-gradient(ellipse at bottom right, rgba(168, 85, 247, 0.06) 0%, transparent 50%);
            color: var(--text-primary);
            min-height: 100vh;
            line-height: 1.6;
        }}
        
        .container {{ max-width: 1900px; margin: 0 auto; padding: 30px; }}
        
        header {{
            margin-bottom: 35px;
            padding-bottom: 25px;
            border-bottom: 1px solid var(--border-color);
        }}
        
        h1 {{
            font-size: 2.2rem;
            font-weight: 700;
            margin-bottom: 8px;
            background: linear-gradient(135deg, var(--accent-teal), var(--accent-cyan));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .subtitle {{ color: var(--text-secondary); font-size: 1rem; }}
        
        .stats-bar {{
            display: flex;
            gap: 20px;
            margin-top: 20px;
            flex-wrap: wrap;
        }}

        .filter-row {{
            margin-top: 14px;
            display: flex;
            gap: 12px;
            align-items: center;
            flex-wrap: wrap;
        }}

        .filter-toggle {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            border-radius: 8px;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            font-size: 0.85rem;
        }}
        
        .stat-item {{
            background: var(--bg-secondary);
            padding: 14px 22px;
            border-radius: 10px;
            border: 1px solid var(--border-color);
            transition: border-color 0.2s, transform 0.2s;
        }}
        
        .stat-item:hover {{
            border-color: var(--accent-teal);
            transform: translateY(-2px);
        }}
        
        .stat-label {{ color: var(--text-secondary); font-size: 0.85rem; margin-bottom: 4px; }}
        .stat-value {{ color: var(--accent-teal); font-size: 1.3rem; font-weight: 600; }}
        
        .section {{
            background: var(--bg-secondary);
            border-radius: 14px;
            padding: 25px;
            margin-bottom: 30px;
            border: 1px solid var(--border-color);
        }}
        
        .section-title {{
            font-size: 1.2rem;
            font-weight: 600;
            margin-bottom: 20px;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .section-title::before {{
            content: '';
            display: inline-block;
            width: 4px;
            height: 20px;
            background: linear-gradient(180deg, var(--accent-teal), var(--accent-purple));
            border-radius: 2px;
        }}
        
        #price-chart, #candlestick-chart {{ width: 100%; height: 500px; }}
        
        .events-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }}
        
        .event-card {{
            background: var(--bg-card);
            border-radius: 10px;
            padding: 18px;
            border: 1px solid var(--border-color);
            cursor: pointer;
            transition: all 0.25s ease;
            position: relative;
            overflow: hidden;
        }}
        
        .event-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
            background: var(--accent-teal);
            opacity: 0;
            transition: opacity 0.2s;
        }}
        
        .event-card:hover {{
            border-color: var(--accent-teal);
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(20, 184, 166, 0.15);
        }}
        
        .event-card:hover::before {{ opacity: 1; }}
        
        .event-card.selected {{
            border-color: var(--accent-cyan);
            background: rgba(20, 184, 166, 0.08);
        }}
        
        .event-card.selected::before {{ opacity: 1; background: var(--accent-cyan); }}
        
        .event-date {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--accent-amber);
            margin-bottom: 6px;
        }}
        
        .event-name {{
            font-size: 0.95rem;
            color: var(--text-primary);
            margin-bottom: 8px;
            line-height: 1.4;
        }}
        
        .event-category {{
            display: inline-block;
            font-size: 0.75rem;
            padding: 3px 10px;
            border-radius: 12px;
            background: rgba(168, 85, 247, 0.2);
            color: var(--accent-purple);
            margin-bottom: 8px;
        }}
        
        .event-notes {{
            font-size: 0.85rem;
            color: var(--text-muted);
            font-style: italic;
            border-top: 1px solid var(--border-color);
            padding-top: 10px;
            margin-top: 8px;
        }}
        
        .distro-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
        }}
        
        @media (max-width: 1200px) {{
            .distro-grid {{ grid-template-columns: 1fr; }}
        }}
        
        .distro-item {{
            display: flex;
            gap: 16px;
            align-items: flex-start;
        }}
        
        .distro-chart {{ 
            height: 350px; 
            flex: 1;
        }}
        
        .stats-box {{
            background: #1f2937;
            border: 1px solid #374151;
            border-radius: 8px;
            padding: 16px;
            min-width: 180px;
            font-size: 0.85rem;
        }}
        
        .stats-box h4 {{
            margin: 0 0 12px 0;
            color: #a5b4fc;
            font-size: 0.9rem;
            font-weight: 600;
            border-bottom: 1px solid #374151;
            padding-bottom: 8px;
        }}
        
        .stats-row {{
            display: flex;
            justify-content: space-between;
            padding: 4px 0;
        }}
        
        .stats-row .label {{
            color: #9ca3af;
        }}
        
        .stats-row .value {{
            font-weight: 500;
            font-family: 'JetBrains Mono', monospace;
        }}
        
        .stats-row.mean .value {{ color: #fbbf24; }}
        .stats-row.median .value {{ color: #22d3ee; }}
        .stats-row.quartile .value {{ color: #9ca3af; }}
        .stats-row.extreme .value {{ color: #ef4444; }}
        
        /* Modal styles */
        .modal {{
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.85);
            backdrop-filter: blur(4px);
        }}
        
        .modal.active {{ display: flex; align-items: center; justify-content: center; }}
        
        .modal-content {{
            background: var(--bg-secondary);
            border-radius: 16px;
            width: 95%;
            max-width: 1400px;
            max-height: 90vh;
            overflow: hidden;
            border: 1px solid var(--border-color);
            box-shadow: 0 25px 50px rgba(0, 0, 0, 0.5);
        }}
        
        .modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            padding: 20px 25px;
            border-bottom: 1px solid var(--border-color);
            background: var(--bg-tertiary);
        }}
        
        .modal-title {{ font-size: 1.3rem; font-weight: 600; }}
        
        .modal-notes {{
            font-size: 0.9rem;
            color: var(--text-secondary);
            margin-top: 8px;
            padding: 12px 15px;
            background: var(--bg-card);
            border-radius: 8px;
            border-left: 3px solid var(--accent-amber);
        }}
        
        .modal-close {{
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 2rem;
            cursor: pointer;
            padding: 0 10px;
            transition: color 0.2s;
        }}
        
        .modal-close:hover {{ color: var(--accent-red); }}
        
        .modal-expand {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            font-size: 1.2rem;
            cursor: pointer;
            padding: 8px 12px;
            border-radius: 6px;
            transition: all 0.2s;
            margin-right: 10px;
        }}
        
        .modal-expand:hover {{ background: var(--accent-blue); color: white; border-color: var(--accent-blue); }}
        
        .modal-body {{ padding: 20px 25px; overflow-y: auto; max-height: calc(90vh - 150px); }}
        
        #modal-candlestick {{ width: 100%; height: 500px; transition: height 0.3s; }}
        
        /* Expanded modal state */
        .modal-content.expanded {{
            width: 100%;
            max-width: 100%;
            height: 100vh;
            max-height: 100vh;
            border-radius: 0;
        }}
        
        .modal-content.expanded .modal-body {{
            max-height: calc(100vh - 150px);
        }}
        
        .modal-content.expanded #modal-candlestick {{
            height: calc(100vh - 350px);
        }}
        
        .events-list {{
            margin-top: 20px;
            padding: 15px;
            background: var(--bg-card);
            border-radius: 10px;
        }}
        
        .events-list-title {{
            font-size: 0.95rem;
            font-weight: 600;
            margin-bottom: 12px;
            color: var(--accent-cyan);
        }}
        
        .econ-event {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid var(--border-color);
            font-size: 0.85rem;
        }}
        
        .econ-event:last-child {{ border-bottom: none; }}
        
        .econ-event-name {{ color: var(--text-primary); }}
        .econ-event-time {{ color: var(--text-muted); font-family: 'JetBrains Mono', monospace; }}
        .econ-event-values {{ color: var(--accent-green); }}
        
        .importance-high {{ border-left: 3px solid var(--accent-red); padding-left: 10px; }}
        .importance-medium {{ border-left: 3px solid var(--accent-orange); padding-left: 10px; }}
        .importance-low {{ border-left: 3px solid var(--text-muted); padding-left: 10px; }}
        
        .generated-info {{
            color: var(--text-muted);
            font-size: 0.8rem;
            text-align: center;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid var(--border-color);
        }}
        
        /* Stats table */
        .stats-table {{
            width: 100%;
            border-collapse: collapse;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.85rem;
        }}
        
        .stats-table th, .stats-table td {{
            padding: 12px 15px;
            text-align: right;
            border-bottom: 1px solid var(--border-color);
        }}
        
        .stats-table th {{
            background: var(--bg-tertiary);
            color: var(--text-secondary);
            font-weight: 500;
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.5px;
        }}
        
        .stats-table th:first-child, .stats-table td:first-child {{
            text-align: left;
        }}
        
        .stats-table tr:hover {{
            background: rgba(20, 184, 166, 0.05);
        }}
        
        .stats-table .window-name {{
            color: var(--accent-cyan);
            font-weight: 600;
        }}
        
        .stats-table .positive {{ color: var(--accent-green); }}
        .stats-table .negative {{ color: var(--accent-red); }}
        .stats-table .neutral {{ color: var(--text-secondary); }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🌍 Geopolitical Events Analysis</h1>
            <p class="subtitle">TY Treasury Futures price paths around major geopolitical events - Calendar day view with event windows highlighted</p>
            <div class="stats-bar">
                <div class="stat-item">
                    <div class="stat-label">Total Events</div>
                    <div class="stat-value" id="total-events">-</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Date Range</div>
                    <div class="stat-value" id="date-range">-</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Avg Return</div>
                    <div class="stat-value" id="avg-return">-</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Avg Volatility</div>
                    <div class="stat-value" id="avg-volatility">-</div>
                </div>
            </div>
            <div class="filter-row">
                <label class="filter-toggle">
                    <input type="checkbox" id="filter-us-only" />
                    US involved only
                </label>
            </div>
        </header>
        
        <div class="section">
            <div class="section-title">Normalized Price Paths (bps change from session start)</div>
            <div id="price-chart"></div>
        </div>
        
        <div class="section">
            <div class="section-title">Events (Click to view candlestick chart)</div>
            <div class="events-grid" id="events-grid"></div>
        </div>
        
        <div class="section">
            <div class="section-title">Return & Volatility Distributions</div>
            <div class="filter-row" style="margin-top: 6px;">
                <label class="filter-toggle">
                    Window:
                    <select id="distro-window" style="margin-left: 8px; background: var(--bg-tertiary); color: var(--text-primary); border: 1px solid var(--border-color); padding: 4px 8px; border-radius: 6px;">
                        <option value="spike">Spike Window (12h)</option>
                        <option value="event_window">Event Window (if known)</option>
                        <option value="extended">±2 Day Net</option>
                        <option value="day">Event Day Net</option>
                    </select>
                </label>
            </div>
            <div class="distro-grid">
                <div class="distro-item">
                    <div id="returns-distro" class="distro-chart"></div>
                    <div id="returns-stats-box" class="stats-box"></div>
                </div>
                <div class="distro-item">
                    <div id="volatility-distro" class="distro-chart"></div>
                    <div id="volatility-stats-box" class="stats-box"></div>
                </div>
            </div>
        </div>
        
        <p class="generated-info">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | TY Treasury Futures 1-min data</p>
    </div>
    
    <!-- Modal for candlestick chart -->
    <div class="modal" id="candlestick-modal">
        <div class="modal-content" id="modal-content">
            <div class="modal-header">
                <div>
                    <div class="modal-title" id="modal-title">Event Details</div>
                    <div class="modal-notes" id="modal-notes"></div>
                </div>
                <div style="display: flex; align-items: flex-start;">
                    <button class="modal-expand" id="expand-btn" onclick="toggleExpand()" title="Expand/Collapse">⛶</button>
                    <button class="modal-close" onclick="closeModal()">&times;</button>
                </div>
            </div>
            <div class="modal-body">
                <div id="modal-candlestick"></div>
                <div class="events-list" id="econ-events-list">
                    <div class="events-list-title">📊 Economic Events on This Day</div>
                    <div id="econ-events-container"></div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        const eventsData = {events_json};
        const distributions = {distributions_json};
        const uiState = {{ usOnly: false }};
        const extendedWindowDays = {EXTENDED_WINDOW_DAYS};

        eventsData.forEach((event, idx) => {{
            event.__idx = idx;
        }});
        
        // Color palette for traces
        const colors = [
            '#14b8a6', '#22d3ee', '#a855f7', '#f97316', '#22c55e',
            '#3b82f6', '#ef4444', '#fbbf24', '#ec4899', '#8b5cf6'
        ];

        function formatSigned(value, decimals = 1) {{
            if (value === null || value === undefined || isNaN(value)) return '-';
            const sign = value > 0 ? '+' : '';
            return `${{sign}}${{value.toFixed(decimals)}}`;
        }}

        function hasPriceData(event) {{
            return event.price_data && event.price_data.closes && event.price_data.closes.length > 0;
        }}

        function getActiveEvents() {{
            return eventsData.filter(e => hasPriceData(e) && (!uiState.usOnly || e.us_involved));
        }}

        function renderHeaderStats(activeEvents) {{
            document.getElementById('total-events').textContent = activeEvents.length;
            if (activeEvents.length > 0) {{
                const dates = activeEvents.map(e => e.date).sort();
                document.getElementById('date-range').textContent = `${{dates[0]}} - ${{dates[dates.length - 1]}}`;
            }} else {{
                document.getElementById('date-range').textContent = '-';
            }}

            const returns = activeEvents
                .map(e => e.spike_return_bps)
                .filter(v => v !== null && v !== undefined);
            const vols = activeEvents
                .map(e => e.spike_volatility_bps)
                .filter(v => v !== null && v !== undefined);

            if (returns.length > 0) {{
                const avgRet = returns.reduce((a, b) => a + b, 0) / returns.length;
                document.getElementById('avg-return').textContent = avgRet.toFixed(1) + ' bps';
            }} else {{
                document.getElementById('avg-return').textContent = '-';
            }}

            if (vols.length > 0) {{
                const avgVol = vols.reduce((a, b) => a + b, 0) / vols.length;
                document.getElementById('avg-volatility').textContent = avgVol.toFixed(1) + ' bps';
            }} else {{
                document.getElementById('avg-volatility').textContent = '-';
            }}
        }}

        function renderEventCards(activeEvents) {{
            const grid = document.getElementById('events-grid');
            grid.innerHTML = '';
            activeEvents.forEach((event) => {{
                const card = document.createElement('div');
                card.className = 'event-card';
                card.dataset.idx = event.__idx;

                const dateShiftNote = event.date_shifted 
                    ? `<div style="color: #f97316; font-size: 0.75rem; margin-top: 2px;">📅 Event: ${{event.source_date}} → Plotted: ${{event.date}}</div>`
                    : '';
                const timeNote = event.time_unknown
                    ? '<span style="color: #6b7280; font-size: 0.7rem; margin-left: 6px;">(time unknown)</span>'
                    : '';
                const gapInfo = (event.gap_bps !== null && event.gap_bps !== undefined)
                    ? `<span style="color: ${{event.gap_bps >= 0 ? '#22c55e' : '#ef4444'}}; font-weight: 600; margin-left: 6px;">(${{event.gap_bps >= 0 ? '+' : ''}}${{event.gap_bps.toFixed(1)}} bps gap)</span>`
                    : '';

                const statsDisplay = (event.spike_return_bps !== null && event.spike_return_bps !== undefined)
                    ? `<div class="event-stats" style="margin-top: 8px; font-size: 0.75rem; font-family: 'JetBrains Mono', monospace; color: #9ca3af;">
                        <span style="color: ${{event.spike_max_up_bps >= 0 ? '#22c55e' : '#ef4444'}}">↑${{event.spike_max_up_bps > 0 ? '+' : ''}}${{event.spike_max_up_bps}}</span> | 
                        <span style="color: ${{event.spike_max_down_bps <= 0 ? '#ef4444' : '#22c55e'}}">↓${{event.spike_max_down_bps}}</span> bps
                        <span style="margin-left: 8px; color: #6b7280;">Vol: ${{event.spike_volatility_bps}} bps</span>
                        <span style="margin-left: 8px; color: #a5b4fc;">Max: ${{event.spike_max_move_bps}} bps</span>
                       </div>`
                    : '';

                const fadeData = event.perplexity_fade || null;
                const fadeValue = fadeData && fadeData.fade_bps !== null && fadeData.fade_bps !== undefined
                    ? fadeData.fade_bps
                    : null;
                const fadeText = fadeValue !== null
                    ? `Fade: ${{formatSigned(fadeValue, 1)}} bps`
                    : (fadeData && fadeData.summary ? 'Fade: see details' : '');
                const fadeDisplay = fadeText
                    ? `<div style="margin-top: 6px; font-size: 0.75rem; color: #fbbf24; font-family: 'JetBrains Mono', monospace;">${{fadeText}}</div>`
                    : '';
                const fadeFromMarket = (event.fade_bps !== null && event.fade_bps !== undefined)
                    ? `<div style="margin-top: 6px; font-size: 0.75rem; color: #fbbf24; font-family: 'JetBrains Mono', monospace;">
                        Fade: ${{formatSigned(event.fade_bps, 1)}} bps${{event.fade_pct !== null && event.fade_pct !== undefined ? ' (' + event.fade_pct.toFixed(1) + '%)' : ''}}
                       </div>`
                    : '';

                card.innerHTML = `
                    <div class="event-date">${{event.source_date || event.date}}${{timeNote}}${{gapInfo}}</div>
                    ${{dateShiftNote}}
                    <span class="event-category">${{event.category}}</span>
                    <div class="event-name">${{event.name}}</div>
                    <div class="event-notes" style="font-size: 0.8rem; color: #9ca3af; margin: 4px 0;">${{event.notes}}</div>
                    <div class="event-day" style="font-size: 0.75rem; color: #6b7280;">${{event.day_of_week || ''}}</div>
                    ${{statsDisplay}}
                    ${{fadeFromMarket || fadeDisplay}}
                `;
                card.onclick = () => selectEvent(event.__idx, card);
                grid.appendChild(card);
            }});
        }}
        
        function renderDashboard() {{
            const activeEvents = getActiveEvents();
            renderHeaderStats(activeEvents);
            renderEventCards(activeEvents);
            plotAllPaths(activeEvents);
            plotDistributions(activeEvents);
        }}

        document.getElementById('distro-window').addEventListener('change', () => {{
            renderDashboard();
        }});

        document.getElementById('filter-us-only').addEventListener('change', (e) => {{
            uiState.usOnly = e.target.checked;
            renderDashboard();
        }});
        
        // Format timestamp for display
        function formatTimestamp(ts) {{
            const d = new Date(ts);
            return d.toLocaleTimeString('en-US', {{ hour: '2-digit', minute: '2-digit', hour12: true }});
        }}
        
        // Normalize timestamp to calendar day (hours from midnight)
        function normalizeToCalendarDay(timestamps) {{
            if (!timestamps || timestamps.length === 0) return [];
            
            return timestamps.map(ts => {{
                const d = new Date(ts);
                // Hours from midnight
                return d.getHours() + d.getMinutes() / 60;
            }});
        }}
        
        // Get event window in hours from midnight
        function getEventWindowHours(event) {{
            if (!event.event_start || !event.event_end) return null;
            
            const start = new Date(event.event_start);
            const end = new Date(event.event_end);
            
            return {{
                start: start.getHours() + start.getMinutes() / 60,
                end: end.getHours() + end.getMinutes() / 60
            }};
        }}
        
        // Convert hour to time label (0 = 12am, 12 = 12pm, etc.)
        function hourToTimeLabel(hour) {{
            const isPM = hour >= 12;
            const displayHour = Math.floor(hour) % 12 || 12;
            return `${{displayHour}}${{isPM ? 'pm' : 'am'}}`;
        }}
        
        // Plot all normalized price paths
        function plotAllPaths(activeEvents) {{
            const traces = activeEvents.map((event, idx) => {{
                const prices = event.price_data;
                if (!prices || !prices.closes || prices.closes.length === 0) return null;
                
                const refPrice = prices.closes[0];
                // 1 point = 8 bps (from treasury_price_conversion.py)
                const BPS_PER_POINT = 8;
                const bpsChanges = prices.closes.map(p => (p - refPrice) * BPS_PER_POINT);
                
                // Normalize to calendar day hours (0-24)
                const dayHours = normalizeToCalendarDay(prices.timestamps);
                
                return {{
                    x: dayHours,
                    y: bpsChanges,
                    type: 'scatter',
                    mode: 'lines',
                    name: `${{event.date}} - ${{event.name.substring(0, 25)}}`,
                    opacity: 0.7,
                    line: {{ width: 2, color: colors[idx % colors.length] }},
                    customdata: prices.timestamps.map((ts, i) => formatTimestamp(ts)),
                    hovertemplate: `<b>${{event.name}}</b><br>` +
                                   `Date: ${{event.date}}<br>` +
                                   `Time: %{{customdata}}<br>` +
                                   `Change: %{{y:.1f}} bps<br>` +
                                   `<extra></extra>`
                }};
            }}).filter(t => t !== null);
            
            // Custom tick labels for calendar time
            const tickVals = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24];
            const tickText = tickVals.map(h => hourToTimeLabel(h));
            
            Plotly.newPlot('price-chart', traces, {{
                paper_bgcolor: '#111827',
                plot_bgcolor: '#111827',
                font: {{ color: '#f9fafb', family: 'Space Grotesk' }},
                xaxis: {{
                    title: 'Time of Day (Calendar Day)',
                    gridcolor: '#374151',
                    zerolinecolor: '#4b5563',
                    tickmode: 'array',
                    tickvals: tickVals,
                    ticktext: tickText,
                    range: [0, 24],
                    rangeslider: {{ visible: true, bgcolor: '#1f2937', thickness: 0.08 }}
                }},
                yaxis: {{
                    title: 'Price Change (bps)',
                    gridcolor: '#374151',
                    zerolinecolor: '#ef4444',
                    zeroline: true,
                    fixedrange: false
                }},
                showlegend: true,
                legend: {{
                    orientation: 'v',
                    x: 1.02,
                    y: 1,
                    xanchor: 'left',
                    yanchor: 'top',
                    bgcolor: 'rgba(31, 41, 55, 0.95)',
                    bordercolor: '#4b5563',
                    borderwidth: 1,
                    font: {{ size: 11, color: '#f9fafb' }},
                    itemsizing: 'constant',
                    itemwidth: 30,
                    tracegroupgap: 4
                }},
                margin: {{ t: 30, r: 220, b: 120, l: 70 }},
                hovermode: 'closest',
                dragmode: 'zoom'
            }}, {{
                scrollZoom: true,
                displayModeBar: true,
                modeBarButtonsToRemove: ['lasso2d', 'select2d'],
                displaylogo: false
            }});
        }}
        
        // Select event and show modal
        function selectEvent(idx, cardEl) {{
            // Update card selection
            document.querySelectorAll('.event-card').forEach(c => c.classList.remove('selected'));
            if (cardEl) {{
                cardEl.classList.add('selected');
            }} else {{
                const fallback = document.querySelector(`.event-card[data-idx="${{idx}}"]`);
                if (fallback) fallback.classList.add('selected');
            }}
            
            const event = eventsData[idx];
            
            // Update modal content
            const dateDisplay = event.date_shifted 
                ? `${{event.source_date}} → ${{event.date}}` 
                : event.source_date || event.date;
            document.getElementById('modal-title').textContent = `${{dateDisplay}} - ${{event.name}}`;
            
            const timeInfo = event.time_unknown 
                ? '(time unknown - no event window shading)' 
                : `Event window: ${{event.event_start}} to ${{event.event_end}}`;
            
            // Gap info for overnight events
            const gapDisplay = (event.gap_bps !== null && event.gap_bps !== undefined)
                ? `<span>Gap: <strong style="color: ${{event.gap_bps >= 0 ? '#22c55e' : '#ef4444'}}">${{event.gap_bps >= 0 ? '+' : ''}}${{event.gap_bps.toFixed(1)}}</strong></span>`
                : '';
            
            // Stats for modal (spike window)
            const hasSpike = event.spike_return_bps !== null && event.spike_return_bps !== undefined;
            const statsInfo = hasSpike
                ? `<div style="margin-top: 10px; padding: 10px; background: #1f2937; border-radius: 6px; font-family: 'JetBrains Mono', monospace; font-size: 0.85rem;">
                    <div style="margin-bottom: 6px; color: #a5b4fc; font-weight: 600;">📊 Spike Window (bps)</div>
                    <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px;">
                        <span>Return: <strong style="color: ${{event.spike_return_bps >= 0 ? '#22c55e' : '#ef4444'}}">${{event.spike_return_bps >= 0 ? '+' : ''}}${{event.spike_return_bps}}</strong></span>
                        <span>Volatility: <strong style="color: #fbbf24">${{event.spike_volatility_bps}}</strong></span>
                        <span>Max Up: <strong style="color: #22c55e">+${{event.spike_max_up_bps}}</strong></span>
                        <span>Max Down: <strong style="color: #ef4444">${{event.spike_max_down_bps}}</strong></span>
                        <span>Max Move: <strong style="color: #a5b4fc">${{event.spike_max_move_bps}}</strong></span>
                        <span>Window: <strong style="color: #9ca3af;">${{event.spike_start_ts}} → ${{event.spike_end_ts}}</strong></span>
                        ${{gapDisplay}}
                    </div>
                   </div>`
                : '';

            const fadeData = event.perplexity_fade || null;
            const fadeSummary = fadeData && fadeData.summary
                ? `<div style="margin-top: 6px; color: #9ca3af;">${{fadeData.summary}}</div>`
                : '';
            const fadeLinks = fadeData && fadeData.citations && fadeData.citations.length
                ? fadeData.citations
                    .filter(c => c && c.url)
                    .map((c, i) => `<a href="${{c.url}}" target="_blank" rel="noopener" style="color: #22d3ee; margin-right: 8px;">link${{i + 1}}</a>`)
                    .join('')
                : '';
            const fadeCitations = fadeLinks
                ? `<div style="margin-top: 6px; font-size: 0.75rem; color: #6b7280;">Sources: ${{fadeLinks}}</div>`
                : '';
            const fadeFromMarket = (event.fade_bps !== null && event.fade_bps !== undefined)
                ? `<div>Fade (from market): <strong style="color: #fbbf24;">${{formatSigned(event.fade_bps, 1)}} bps</strong>${{event.fade_pct !== null && event.fade_pct !== undefined ? ' (' + event.fade_pct.toFixed(1) + '%)' : ''}}</div>`
                : '';
            const narrativeStats = event.narrative_stats || null;
            const narrativeStatsBlock = (narrativeStats && narrativeStats.has_data)
                ? `<div style="margin-top: 6px;">
                    <div style="color: #a5b4fc; font-weight: 600;">Estimated Window (from narrative)</div>
                    <div>Return: <strong>${{formatSigned(narrativeStats.spike_return_bps, 1)}} bps</strong></div>
                    <div>Volatility: <strong>${{narrativeStats.spike_volatility_bps.toFixed(1)}} bps</strong></div>
                    <div>Fade: <strong>${{formatSigned(narrativeStats.fade_bps, 1)}} bps</strong>${{narrativeStats.fade_pct !== null && narrativeStats.fade_pct !== undefined ? ' (' + narrativeStats.fade_pct.toFixed(1) + '%)' : ''}}</div>
                    <div style="color: #6b7280; font-size: 0.75rem;">${{narrativeStats.spike_start_ts}} → ${{narrativeStats.spike_end_ts}}</div>
                   </div>`
                : '';
            const narrativeInfo = (fadeFromMarket || fadeSummary || fadeCitations || narrativeStatsBlock)
                ? `<div style="margin-top: 10px; padding: 10px; background: #111827; border-radius: 6px; font-family: 'JetBrains Mono', monospace; font-size: 0.85rem;">
                    <div style="margin-bottom: 6px; color: #fbbf24; font-weight: 600;">🧠 Web Narrative</div>
                    ${{fadeFromMarket}}
                    ${{narrativeStatsBlock}}
                    ${{fadeSummary}}
                    ${{fadeCitations}}
                   </div>`
                : '';
            
            const summaryBlock = event.summary
                ? `<div style="margin-top: 8px; color: #e5e7eb;">${{event.summary}}</div>`
                : '';
            const macroEvents = event.macro_events_nearby || [];
            const macroBlock = macroEvents.length
                ? `<div style="margin-top: 8px; font-size: 0.8rem; color: #c7d2fe;">
                    <div style="font-weight: 600; margin-bottom: 4px;">Macro events (±${{extendedWindowDays}}d)</div>
                    ${{macroEvents.slice(0, 6).map(ev => {{
                        const t = new Date(ev.timestamp).toLocaleString('en-US', {{ hour: '2-digit', minute: '2-digit', month: 'short', day: '2-digit', hour12: true }});
                        return `<div style="color: #9ca3af;">${{t}} — ${{ev.name}}</div>`;
                    }}).join('')}}
                   </div>`
                : '';
            document.getElementById('modal-notes').innerHTML = `
                <strong>Category:</strong> ${{event.category}} | <strong>Day:</strong> ${{event.day_of_week || 'N/A'}}<br>
                <strong>Notes:</strong> ${{event.notes}}<br>
                ${{summaryBlock}}
                ${{macroBlock}}
                <span style="color: #9ca3af; font-size: 0.85rem;">${{timeInfo}}</span>
                ${{statsInfo}}
                ${{narrativeInfo}}
            `;
            
            // Plot candlestick
            plotCandlestick(event);
            
            // Show economic events
            renderEconomicEvents(event.economic_events || []);
            
            // Show modal
            document.getElementById('candlestick-modal').classList.add('active');
        }}
        
        // Convert decimal price to bond notation (PPP'TT)
        function decimalToBondNotation(decimalPrice) {{
            const points = Math.floor(decimalPrice);
            const remainder = decimalPrice - points;
            const ticks = remainder * 32;
            
            // Check for half-tick
            if (ticks % 1 === 0.5) {{
                const wholeTicks = Math.floor(ticks);
                return `${{points}}'${{wholeTicks.toString().padStart(2, '0')}}5`;
            }} else {{
                return `${{points}}'${{Math.round(ticks).toString().padStart(2, '0')}}`;
            }}
        }}
        
        function plotCandlestick(event) {{
            // Use candlestick_data (±2 days) if available, otherwise fall back to price_data
            const useCandle = event.candlestick_data && event.candlestick_data.closes && event.candlestick_data.closes.length > 0;
            const prices = useCandle ? event.candlestick_data : event.price_data;
            if (!prices || !prices.closes || prices.closes.length === 0) return;
            
            // Resample to 5-min candles for cleaner display
            const candleData = resampleToCandles(prices, 5);
            
            // Calculate price range for y-axis tick generation
            const allPrices = [...candleData.opens, ...candleData.highs, ...candleData.lows, ...candleData.closes];
            const minPrice = Math.min(...allPrices);
            const maxPrice = Math.max(...allPrices);
            
            // Generate tick values at 8-tick (1/4 point) intervals
            const tickStep = 0.25; // 8/32 = 0.25 point = 8 ticks
            const startTick = Math.floor(minPrice / tickStep) * tickStep;
            const endTick = Math.ceil(maxPrice / tickStep) * tickStep;
            
            const tickVals = [];
            const tickText = [];
            for (let p = startTick; p <= endTick; p += tickStep) {{
                tickVals.push(p);
                tickText.push(decimalToBondNotation(p));
            }}
            
            const trace = {{
                x: candleData.timestamps,
                open: candleData.opens,
                high: candleData.highs,
                low: candleData.lows,
                close: candleData.closes,
                type: 'candlestick',
                increasing: {{ line: {{ color: '#22c55e' }}, fillcolor: '#22c55e' }},
                decreasing: {{ line: {{ color: '#ef4444' }}, fillcolor: '#ef4444' }},
                hovertemplate: '<b>%{{x}}</b><br>' +
                               'Open: %{{customdata[0]}}<br>' +
                               'High: %{{customdata[1]}}<br>' +
                               'Low: %{{customdata[2]}}<br>' +
                               'Close: %{{customdata[3]}}<extra></extra>',
                customdata: candleData.opens.map((o, i) => [
                    decimalToBondNotation(o),
                    decimalToBondNotation(candleData.highs[i]),
                    decimalToBondNotation(candleData.lows[i]),
                    decimalToBondNotation(candleData.closes[i])
                ])
            }};
            
            // Add event window shape (shaded region for expected event window)
            const shapes = [];
            const annotations = [];
            
            // Add vertical line to mark the event date (midnight)
            const eventDateMidnight = event.date + 'T00:00:00';
            shapes.push({{
                type: 'line',
                x0: eventDateMidnight,
                x1: eventDateMidnight,
                y0: 0,
                y1: 1,
                yref: 'paper',
                line: {{ color: '#22d3ee', width: 2, dash: 'dash' }}
            }});
            
            // Annotation for event date
            annotations.push({{
                x: eventDateMidnight,
                y: 0.98,
                yref: 'paper',
                text: `📅 ${{event.date}}`,
                showarrow: false,
                font: {{ size: 10, color: '#22d3ee' }},
                xanchor: 'left',
                xshift: 5
            }});
            
            // Only show event window shading if time is known
            if (event.event_start && event.event_end && !event.time_unknown) {{
                // Shaded rectangle for event window
                shapes.push({{
                    type: 'rect',
                    x0: event.event_start,
                    x1: event.event_end,
                    y0: 0,
                    y1: 1,
                    yref: 'paper',
                    fillcolor: 'rgba(251, 191, 36, 0.2)',
                    line: {{ color: '#fbbf24', width: 2 }}
                }});
                
                // Label for event window
                annotations.push({{
                    x: event.event_start,
                    y: 1.02,
                    yref: 'paper',
                    text: '⚡ Event Window',
                    showarrow: false,
                    font: {{ size: 11, color: '#fbbf24', weight: 'bold' }},
                    xanchor: 'left'
                }});
            }}
            
            Plotly.newPlot('modal-candlestick', [trace], {{
                paper_bgcolor: '#111827',
                plot_bgcolor: '#111827',
                font: {{ color: '#f9fafb', family: 'Space Grotesk' }},
                xaxis: {{
                    title: 'Time (±2 days from event)',
                    gridcolor: '#374151',
                    type: 'date',
                    tickformat: '%b %d %H:%M',
                    rangeslider: {{ 
                        visible: true,
                        bgcolor: '#1f2937',
                        bordercolor: '#374151',
                        thickness: 0.1
                    }}
                }},
                yaxis: {{
                    title: "Price (32nds)",
                    gridcolor: '#374151',
                    tickmode: 'array',
                    tickvals: tickVals,
                    ticktext: tickText,
                    fixedrange: false
                }},
                shapes: shapes,
                annotations: annotations,
                margin: {{ t: 50, r: 80, b: 120, l: 80 }},
                dragmode: 'zoom',
                hovermode: 'x unified'
            }}, {{
                scrollZoom: true,
                displayModeBar: true,
                modeBarButtonsToAdd: ['drawline', 'drawopenpath'],
                modeBarButtonsToRemove: ['lasso2d', 'select2d']
            }});
        }}
        
        function resampleToCandles(prices, minutes) {{
            const result = {{
                timestamps: [],
                opens: [],
                highs: [],
                lows: [],
                closes: []
            }};
            
            for (let i = 0; i < prices.timestamps.length; i += minutes) {{
                const end = Math.min(i + minutes, prices.timestamps.length);
                const slice = {{
                    opens: prices.opens.slice(i, end),
                    highs: prices.highs.slice(i, end),
                    lows: prices.lows.slice(i, end),
                    closes: prices.closes.slice(i, end)
                }};
                
                if (slice.opens.length > 0) {{
                    result.timestamps.push(prices.timestamps[i]);
                    result.opens.push(slice.opens[0]);
                    result.highs.push(Math.max(...slice.highs));
                    result.lows.push(Math.min(...slice.lows));
                    result.closes.push(slice.closes[slice.closes.length - 1]);
                }}
            }}
            
            return result;
        }}
        
        function renderEconomicEvents(events) {{
            const container = document.getElementById('econ-events-container');
            
            if (!events || events.length === 0) {{
                container.innerHTML = '<div style="color: #6b7280; font-size: 0.85rem;">No economic events recorded for this day</div>';
                return;
            }}
            
            container.innerHTML = events.map(ev => {{
                const time = new Date(ev.timestamp).toLocaleTimeString('en-US', {{
                    hour: '2-digit',
                    minute: '2-digit',
                    hour12: true
                }});
                const values = [];
                if (ev.actual !== null && ev.actual !== undefined) values.push(`A: ${{ev.actual}}`);
                if (ev.consensus !== null && ev.consensus !== undefined) values.push(`C: ${{ev.consensus}}`);
                
                return `
                    <div class="econ-event importance-${{ev.importance || 'medium'}}">
                        <span class="econ-event-name">${{ev.name}}</span>
                        <span class="econ-event-time">${{time}}</span>
                        <span class="econ-event-values">${{values.join(' | ') || '-'}}</span>
                    </div>
                `;
            }}).join('');
        }}
        
        function closeModal() {{
            document.getElementById('candlestick-modal').classList.remove('active');
            document.getElementById('modal-content').classList.remove('expanded');
            document.getElementById('expand-btn').textContent = '⛶';
        }}
        
        function toggleExpand() {{
            const modalContent = document.getElementById('modal-content');
            const expandBtn = document.getElementById('expand-btn');
            const isExpanded = modalContent.classList.toggle('expanded');
            expandBtn.textContent = isExpanded ? '⛶' : '⛶';
            expandBtn.title = isExpanded ? 'Collapse' : 'Expand to fullscreen';
            
            // Resize the Plotly chart to fit new dimensions
            setTimeout(() => {{
                Plotly.Plots.resize('modal-candlestick');
            }}, 350);
        }}
        
        // Close modal on escape key
        document.addEventListener('keydown', (e) => {{
            if (e.key === 'Escape') closeModal();
        }});
        
        // Close modal on outside click
        document.getElementById('candlestick-modal').addEventListener('click', (e) => {{
            if (e.target.classList.contains('modal')) closeModal();
        }});
        
        // Compute percentile
        function percentile(arr, p) {{
            const sorted = [...arr].sort((a, b) => a - b);
            const idx = (p / 100) * (sorted.length - 1);
            const lower = Math.floor(idx);
            const upper = Math.ceil(idx);
            if (lower === upper) return sorted[lower];
            return sorted[lower] + (idx - lower) * (sorted[upper] - sorted[lower]);
        }}
        
        // Compute what percentile a given value lies at
        function valueToPercentile(data, value) {{
            const sorted = [...data].sort((a, b) => a - b);
            const n = sorted.length;
            if (n === 0) return null;
            
            // Count how many values are below the target value
            let countBelow = 0;
            for (let i = 0; i < n; i++) {{
                if (sorted[i] < value) countBelow++;
            }}
            
            // Percentile = (count below / total) * 100
            return (countBelow / n) * 100;
        }}
        
        // Compute stats for distribution
        function computeDistroStats(data, includeZeroPercentile = false) {{
            const sorted = [...data].sort((a, b) => a - b);
            const mean = data.reduce((a, b) => a + b, 0) / data.length;
            const min = sorted[0];
            const max = sorted[sorted.length - 1];
            const p5 = percentile(data, 5);
            const p25 = percentile(data, 25);
            const p50 = percentile(data, 50);
            const p75 = percentile(data, 75);
            const p95 = percentile(data, 95);
            
            // Compute what percentile 0 lies at (for returns)
            const zeroPercentile = includeZeroPercentile ? valueToPercentile(data, 0) : null;
            
            // Shapes for vertical lines (no annotations)
            const shapes = [
                // Mean line (yellow, dashed)
                {{ type: 'line', x0: mean, x1: mean, y0: 0, y1: 1, yref: 'paper',
                   line: {{ color: '#fbbf24', width: 2, dash: 'dash' }} }},
                // Median line (cyan, solid)
                {{ type: 'line', x0: p50, x1: p50, y0: 0, y1: 1, yref: 'paper',
                   line: {{ color: '#22d3ee', width: 2 }} }},
                // P25/P75 (gray, dotted)
                {{ type: 'line', x0: p25, x1: p25, y0: 0, y1: 1, yref: 'paper',
                   line: {{ color: '#9ca3af', width: 1, dash: 'dot' }} }},
                {{ type: 'line', x0: p75, x1: p75, y0: 0, y1: 1, yref: 'paper',
                   line: {{ color: '#9ca3af', width: 1, dash: 'dot' }} }},
                // P5/P95 (red, dotted)
                {{ type: 'line', x0: p5, x1: p5, y0: 0, y1: 1, yref: 'paper',
                   line: {{ color: '#ef4444', width: 1, dash: 'dot' }} }},
                {{ type: 'line', x0: p95, x1: p95, y0: 0, y1: 1, yref: 'paper',
                   line: {{ color: '#ef4444', width: 1, dash: 'dot' }} }},
            ];
            
            return {{ shapes, stats: {{ mean, min, max, p5, p25, p50, p75, p95, n: data.length, zeroPercentile }} }};
        }}
        
        // Render stats box HTML
        function renderStatsBox(containerId, title, stats, unit) {{
            const container = document.getElementById(containerId);
            
            // Build zero percentile row if available (for returns)
            const zeroPercentileRow = (stats.zeroPercentile !== null && stats.zeroPercentile !== undefined)
                ? `<div class="stats-row" style="margin-top: 8px; border-top: 1px solid #374151; padding-top: 8px; background: rgba(34, 197, 94, 0.1); margin: 8px -8px 0; padding: 8px;">
                    <span class="label" style="color: #22c55e;">0 bps @ Percentile</span>
                    <span class="value" style="color: #22c55e; font-weight: 700;">P${{stats.zeroPercentile.toFixed(0)}}</span>
                   </div>
                   <div style="font-size: 0.75rem; color: #6b7280; padding: 4px 0;">
                    (${{stats.zeroPercentile.toFixed(1)}}% of events had negative returns)
                   </div>`
                : '';
            
            container.innerHTML = `
                <h4>${{title}}</h4>
                <div class="stats-row mean">
                    <span class="label">Mean</span>
                    <span class="value">${{stats.mean.toFixed(1)}} ${{unit}}</span>
                </div>
                <div class="stats-row median">
                    <span class="label">Median (P50)</span>
                    <span class="value">${{stats.p50.toFixed(1)}} ${{unit}}</span>
                </div>
                <div class="stats-row quartile">
                    <span class="label">P25</span>
                    <span class="value">${{stats.p25.toFixed(1)}} ${{unit}}</span>
                </div>
                <div class="stats-row quartile">
                    <span class="label">P75</span>
                    <span class="value">${{stats.p75.toFixed(1)}} ${{unit}}</span>
                </div>
                <div class="stats-row extreme">
                    <span class="label">P5</span>
                    <span class="value">${{stats.p5.toFixed(1)}} ${{unit}}</span>
                </div>
                <div class="stats-row extreme">
                    <span class="label">P95</span>
                    <span class="value">${{stats.p95.toFixed(1)}} ${{unit}}</span>
                </div>
                <div class="stats-row" style="margin-top: 8px; border-top: 1px solid #374151; padding-top: 8px;">
                    <span class="label">Min</span>
                    <span class="value" style="color: #6ee7b7;">${{stats.min.toFixed(1)}} ${{unit}}</span>
                </div>
                <div class="stats-row">
                    <span class="label">Max</span>
                    <span class="value" style="color: #fca5a5;">${{stats.max.toFixed(1)}} ${{unit}}</span>
                </div>
                ${{zeroPercentileRow}}
                <div class="stats-row" style="margin-top: 8px; color: #6b7280; font-size: 0.8rem;">
                    <span>n = ${{stats.n}}</span>
                </div>
            `;
        }}
        
        // Plot distribution charts
        function plotDistributions(activeEvents) {{
            const windowMode = document.getElementById('distro-window').value;
            const returnsData = [];
            const volData = [];

            activeEvents.forEach(e => {{
                if (windowMode === 'spike') {{
                    if (e.spike_return_bps !== null && e.spike_return_bps !== undefined) {{
                        returnsData.push(e.spike_return_bps);
                    }}
                    if (e.spike_volatility_bps !== null && e.spike_volatility_bps !== undefined) {{
                        volData.push(e.spike_volatility_bps);
                    }}
                }} else if (windowMode === 'event_window') {{
                    if (e.event_window_stats && e.event_window_stats.return_bps !== null && e.event_window_stats.return_bps !== undefined) {{
                        returnsData.push(e.event_window_stats.return_bps);
                    }}
                    if (e.event_window_stats && e.event_window_stats.vol_bps !== null && e.event_window_stats.vol_bps !== undefined) {{
                        volData.push(e.event_window_stats.vol_bps);
                    }}
                }} else if (windowMode === 'extended') {{
                    if (e.extended_stats && e.extended_stats.return_bps !== null && e.extended_stats.return_bps !== undefined) {{
                        returnsData.push(e.extended_stats.return_bps);
                    }}
                    if (e.extended_stats && e.extended_stats.vol_bps !== null && e.extended_stats.vol_bps !== undefined) {{
                        volData.push(e.extended_stats.vol_bps);
                    }}
                }} else if (windowMode === 'day') {{
                    if (e.stats && e.stats.day_return !== null && e.stats.day_return !== undefined) {{
                        returnsData.push(e.stats.day_return);
                    }}
                    if (e.stats && e.stats.day_volatility !== null && e.stats.day_volatility !== undefined) {{
                        volData.push(e.stats.day_volatility);
                    }}
                }}
            }});
            // Returns distribution with KDE
            if (returnsData.length > 0) {{
                const returnsTrace = {{
                    x: returnsData,
                    type: 'histogram',
                    name: 'Returns',
                    marker: {{
                        color: 'rgba(20, 184, 166, 0.6)',
                        line: {{ color: '#14b8a6', width: 1 }}
                    }},
                    nbinsx: 15,
                    histnorm: 'probability density'
                }};
                
                // Add KDE line
                const kde = computeKDE(returnsData, 50);
                const kdeTrace = {{
                    x: kde.x,
                    y: kde.y,
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Density',
                    line: {{ color: '#22d3ee', width: 2 }}
                }};
                
                // Stats (shapes only, no annotations) - include zero percentile for returns
                const statsResult = computeDistroStats(returnsData, true);
                
                Plotly.newPlot('returns-distro', [returnsTrace, kdeTrace], {{
                    paper_bgcolor: '#111827',
                    plot_bgcolor: '#111827',
                    font: {{ color: '#f9fafb', family: 'Space Grotesk' }},
                    title: {{
                        text: 'Returns Distribution (bps)',
                        font: {{ size: 14 }}
                    }},
                    xaxis: {{
                        title: 'Return (bps)',
                        gridcolor: '#374151',
                        zerolinecolor: '#ef4444'
                    }},
                    yaxis: {{
                        title: 'Density',
                        gridcolor: '#374151'
                    }},
                    shapes: statsResult.shapes,
                    showlegend: false,
                    margin: {{ t: 50, r: 20, b: 50, l: 60 }}
                }});
                
                // Render stats box
                renderStatsBox('returns-stats-box', 'Returns Stats', statsResult.stats, 'bps');
            }} else {{
                Plotly.purge('returns-distro');
                document.getElementById('returns-stats-box').innerHTML = '<h4>Returns Stats</h4><div class="stats-row"><span class="label">No data</span></div>';
            }}
            
            // Volatility distribution with KDE
            if (volData.length > 0) {{
                const volTrace = {{
                    x: volData,
                    type: 'histogram',
                    name: 'Volatility',
                    marker: {{
                        color: 'rgba(168, 85, 247, 0.6)',
                        line: {{ color: '#a855f7', width: 1 }}
                    }},
                    nbinsx: 15,
                    histnorm: 'probability density'
                }};
                
                const kde = computeKDE(volData, 50);
                const kdeTrace = {{
                    x: kde.x,
                    y: kde.y,
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Density',
                    line: {{ color: '#ec4899', width: 2 }}
                }};
                
                // Stats (shapes only, no annotations)
                const statsResult = computeDistroStats(volData);
                
                Plotly.newPlot('volatility-distro', [volTrace, kdeTrace], {{
                    paper_bgcolor: '#111827',
                    plot_bgcolor: '#111827',
                    font: {{ color: '#f9fafb', family: 'Space Grotesk' }},
                    title: {{
                        text: 'Volatility Distribution (bps)',
                        font: {{ size: 14 }}
                    }},
                    xaxis: {{
                        title: 'Volatility (High-Low bps)',
                        gridcolor: '#374151'
                    }},
                    yaxis: {{
                        title: 'Density',
                        gridcolor: '#374151'
                    }},
                    shapes: statsResult.shapes,
                    showlegend: false,
                    margin: {{ t: 50, r: 20, b: 50, l: 60 }}
                }});
                
                // Render stats box
                renderStatsBox('volatility-stats-box', 'Volatility Stats', statsResult.stats, 'bps');
            }} else {{
                Plotly.purge('volatility-distro');
                document.getElementById('volatility-stats-box').innerHTML = '<h4>Volatility Stats</h4><div class="stats-row"><span class="label">No data</span></div>';
            }}
        }}
        
        // Simple KDE implementation using Gaussian kernel
        function computeKDE(data, numPoints) {{
            if (!data || data.length === 0) return {{ x: [], y: [] }};
            
            const min = Math.min(...data);
            const max = Math.max(...data);
            const range = max - min || 1;
            const bandwidth = 1.06 * standardDeviation(data) * Math.pow(data.length, -0.2);
            
            const x = [];
            const y = [];
            const step = range / numPoints;
            
            for (let i = 0; i <= numPoints; i++) {{
                const xi = min + i * step;
                x.push(xi);
                
                let density = 0;
                for (const d of data) {{
                    density += gaussianKernel((xi - d) / bandwidth);
                }}
                y.push(density / (data.length * bandwidth));
            }}
            
            return {{ x, y }};
        }}
        
        function gaussianKernel(u) {{
            return Math.exp(-0.5 * u * u) / Math.sqrt(2 * Math.PI);
        }}
        
        function standardDeviation(arr) {{
            const n = arr.length;
            if (n === 0) return 0;
            const mean = arr.reduce((a, b) => a + b, 0) / n;
            const variance = arr.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / n;
            return Math.sqrt(variance);
        }}
        
        // Initialize
        renderDashboard();
    </script>
</body>
</html>'''
    
    return html


def main():
    parser = argparse.ArgumentParser(description="Generate Geopolitical Events Analysis Dashboard")
    parser.add_argument('--output', type=str, default=None, help='Output HTML file path')
    parser.add_argument('--no-perplexity', action='store_true', help='Disable Perplexity fade lookup')
    parser.add_argument('--perplexity-cache', type=str, default=None, help='Path to Perplexity cache JSON')
    parser.add_argument('--perplexity-refresh', action='store_true', help='Force refresh Perplexity fade cache')
    args = parser.parse_args()
    
    output_file = Path(args.output) if args.output else OUTPUT_FILE
    use_perplexity = not args.no_perplexity
    cache_path = Path(args.perplexity_cache) if args.perplexity_cache else PERPLEXITY_CACHE_DEFAULT
    perplexity_cache = load_perplexity_cache(cache_path) if use_perplexity else {}
    perplexity_available = use_perplexity
    
    logger.info("Generating Geopolitical Events Analysis Dashboard")
    
    # Connect to database
    conn = get_db_connection()
    
    # Process each event
    events_data = []
    for event in GEO_EVENTS:
        source_date = event['date']
        logger.info(f"Processing: {source_date} - {event['name']}")
        
        # Try to fetch price data for source date
        price_data = fetch_price_data_for_date(source_date, conn)
        plot_date = source_date
        date_shifted = False
        
        # If no/insufficient data, try to find nearest trading day (Globex resolution)
        if len(price_data.get('closes', [])) < 100:
            nearest_date = find_nearest_trading_date(source_date, conn, max_days=5)
            if nearest_date and nearest_date != source_date:
                logger.info(f"  → No data on {source_date}, using Globex session from {nearest_date}")
                price_data = fetch_price_data_for_date(nearest_date, conn)
                plot_date = nearest_date
                date_shifted = True
        
        # Fetch economic events for the actual plot date
        econ_events = fetch_economic_events_for_date(plot_date)
        
        # Fetch ±2 days data for candlestick modal and extended spike detection
        candlestick_data = fetch_price_data_for_range(
            plot_date,
            conn,
            days_before=EXTENDED_WINDOW_DAYS,
            days_after=EXTENDED_WINDOW_DAYS,
        )
        
        # Compute per-event stats
        event_stats = compute_per_event_stats(price_data)
        # Spike/fade stats over ±2 days around event (uses 12h window)
        spike_price_data = candlestick_data if candlestick_data.get('closes') else price_data
        spike_stats = compute_spike_stats(spike_price_data, event.get('event_start') or None)
        perplexity_fade = None
        if use_perplexity and perplexity_available:
            perplexity_fade = get_perplexity_fade(
                event=event,
                spike_stats=spike_stats,
                cache=perplexity_cache,
                cache_path=cache_path,
                force_refresh=args.perplexity_refresh,
            )
            if perplexity_fade is None:
                perplexity_available = False
                logger.warning("Perplexity API key missing; skipping fade lookups.")
        
        narrative_stats = None
        if event.get('time_unknown', True):
            narrative_window = perplexity_fade.get("window") if perplexity_fade else None
            narrative_anchor = parse_narrative_anchor(plot_date, narrative_window)
            if narrative_anchor:
                narrative_stats = compute_spike_stats(
                    spike_price_data,
                    narrative_anchor,
                    window_minutes=SPIKE_WINDOW_MINUTES,
                    pre_minutes=EXTENDED_WINDOW_DAYS * 1440,
                    post_minutes=EXTENDED_WINDOW_DAYS * 1440,
                )

        # Event-window stats (if time known) and ±2d net stats
        event_window_stats = None
        if event.get('event_start') and event.get('event_end'):
            window_slice = slice_price_data_by_time(spike_price_data, event['event_start'], event['event_end'])
            if window_slice:
                event_window_stats = compute_return_vol_for_window(window_slice)

        extended_stats = compute_return_vol_for_window(spike_price_data)

        # Macro events ±2d for context
        macro_events_nearby = []
        try:
            base_dt = datetime.strptime(plot_date, '%Y-%m-%d')
            start_dt = (base_dt - timedelta(days=EXTENDED_WINDOW_DAYS)).strftime('%Y-%m-%d')
            end_dt = (base_dt + timedelta(days=EXTENDED_WINDOW_DAYS)).strftime('%Y-%m-%d')
            macro_events_nearby = fetch_economic_events_for_range(start_dt, end_dt)
        except Exception as ex:
            logger.warning(f"Failed to fetch macro events range: {ex}")
        
        events_data.append({
            'id': event.get('id', 0),
            'name': event['name'],
            'category': event['category'],
            'notes': event['notes'],
            'summary': event.get('summary', ''),
            'source_date': source_date,  # Original event date
            'date': plot_date,           # Date actually plotted
            'date_shifted': date_shifted, # Flag if we shifted to Globex session
            'day_of_week': event.get('day_of_week', ''),
            'us_involved': is_us_involved(event),
            'event_start': event.get('event_start', ''),
            'event_end': event.get('event_end', ''),
            'time_unknown': event.get('time_unknown', True),
            'price_data': price_data,
            'candlestick_data': candlestick_data,  # ±2 days for modal chart
            'economic_events': econ_events,
            'macro_events_nearby': macro_events_nearby,
            'stats': event_stats,  # Per-event max-move stats
            'event_window_stats': event_window_stats,
            'extended_stats': extended_stats,
            'perplexity_fade': perplexity_fade,
            'narrative_window': perplexity_fade.get("window") if perplexity_fade else None,
            'narrative_stats': narrative_stats,
            **spike_stats,
        })
        
        if price_data['closes']:
            shift_note = f" (shifted from {source_date})" if date_shifted else ""
            logger.info(f"  → {len(price_data['closes'])} price bars, {len(econ_events)} econ events{shift_note}")
        else:
            logger.warning(f"  → No price data available")
    
    conn.close()
    
    # Compute distributions
    distributions = compute_returns_and_volatility(events_data)
    logger.info(f"Computed {len(distributions['returns'])} returns, {len(distributions['volatilities'])} volatility values")
    
    # Compute gap up/down for events where event window is outside/on calendar day boundary
    from rules.treasury_price_conversion import points_to_bps
    for e in events_data:
        e['gap_bps'] = None  # Default
        
        event_start = e.get('event_start', '')
        event_date = e.get('date', '')
        
        if not event_start or not event_date:
            continue
        
        try:
            # Parse event_start and determine if it's outside the calendar day
            start_dt = datetime.strptime(event_start, '%Y-%m-%d %H:%M:%S')
            event_dt = datetime.strptime(event_date, '%Y-%m-%d')
            
            # Event is "overnight" if:
            # - event_start is before midnight of event_date (previous day)
            # - event_start is after 11pm on event_date (end of day boundary)
            is_overnight = (start_dt.date() < event_dt.date()) or (start_dt.hour >= 23)
            
            if is_overnight:
                # Calculate gap: current day open - previous day close
                prices = e.get('price_data', {})
                candlestick_prices = e.get('candlestick_data', {})
                
                if prices.get('closes') and prices.get('opens'):
                    # Day open is first candle of event date
                    day_open = prices['opens'][0]
                    
                    # Get prior day close from candlestick data (±2 days)
                    if candlestick_prices.get('timestamps'):
                        # Find the last timestamp before midnight of event date
                        prior_close = None
                        for i, ts in enumerate(candlestick_prices['timestamps']):
                            ts_dt = datetime.fromisoformat(ts.replace('Z', '+00:00').replace('+00:00', ''))
                            if ts_dt.date() < event_dt.date():
                                prior_close = candlestick_prices['closes'][i]
                        
                        if prior_close is not None:
                            gap_points = day_open - prior_close
                            gap_bps = round(float(points_to_bps(gap_points)), 2)
                            e['gap_bps'] = gap_bps
                            logger.info(f"  Gap for {e['name'][:30]}...: {gap_bps:+.1f} bps")
        except Exception as ex:
            logger.warning(f"Could not compute gap for {e.get('name', 'unknown')}: {ex}")
    
    # Generate HTML
    html = generate_html(events_data, distributions)
    
    # Write output
    output_file.write_text(html, encoding='utf-8')
    logger.info(f"Dashboard saved to: {output_file}")
    logger.info(f"Open: file:///{output_file.as_posix()}")


if __name__ == '__main__':
    main()

