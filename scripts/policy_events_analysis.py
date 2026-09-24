#!/usr/bin/env python
"""
Policy & Macro Events Analysis Dashboard
-----------------------------------------
Generates an interactive HTML dashboard showing TY price paths for
policy/macro events across 5 categories:
  1) Yen rate check
  2) Iran
  3) Canada tariff
  4) Shutdown
  5) Fed

Based on geo_analysis.py with category filtering and per-event summaries.

Usage:
    python scripts/policy_events_analysis.py
    python scripts/policy_events_analysis.py --output custom_output.html
"""

import argparse
import json
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_FILE = PROJECT_ROOT / "scripts" / "policy_events_analysis.html"

# =============================================================================
# Event Data - Policy & Macro Events
# Times are stored in US/Eastern (ET). source_tz indicates original timezone.
# For multi-day events, we expand into separate entries per calendar day.
# =============================================================================

POLICY_EVENTS_RAW = [
    # =========================================================================
    # CATEGORY: Shutdown
    # Note: Individual shutdown days are NOT expanded here since the Timeline view
    # provides the consolidated -7/+7 day window analysis for each shutdown episode.
    # Only key marker days are kept for the daily events view.
    # =========================================================================
    # 1. January 2018 DACA shutdown - just the start date
    {
        "id": 1,
        "name": "DACA Shutdown Start",
        "category": "Shutdown",
        "notes": "Three-day shutdown; markets largely viewed as political theater with little economic impact.",
        "summary": """January 2018 DACA shutdown (Jan 20–22, 2018)

This was a very brief three-day shutdown that markets largely viewed as political theater with little economic impact.

Around that period, 10-year yields were already in an uptrend on strong growth and Fed tightening expectations; the shutdown itself did not produce an obvious, lasting move beyond normal volatility.

In practical terms, ZN would have seen only modest, short-lived safe-haven flows during the weekend drama, quickly overwhelmed by the larger macro trend of rising yields once the government reopened.""",
        "date": "2018-01-20",
        "event_time_local": "",
        "source_tz": "America/New_York",
        "day_of_week": "Saturday",
    },
    
    # 2. December 2018-January 2019 wall shutdown - start and end dates only
    {
        "id": 2,
        "name": "Wall Shutdown Start",
        "category": "Shutdown",
        "notes": "35-day shutdown alongside major risk-off episode driven by global growth and Fed tightening concerns.",
        "summary": """December 2018–January 2019 wall shutdown (Dec 22, 2018–Jan 25, 2019)

This occurred alongside a major risk-off episode driven by concerns about global growth and Fed tightening, not just the shutdown itself.

During late December 2018, 10-year yields fell sharply from roughly the low-3% area toward the mid-2% range as equities sold off and investors moved into Treasuries; that meant a strong rally in 10-year prices and ZN futures.

As the shutdown ended in late January 2019 and equities stabilized, yields gradually backed up again, which would have taken some of the steam out of the ZN rally, but the dominant driver remained growth and Fed expectations rather than the shutdown headlines alone.""",
        "date": "2018-12-22",
        "event_time_local": "",
        "source_tz": "America/New_York",
        "day_of_week": "Saturday",
    },
    {
        "id": 2.5,
        "name": "Wall Shutdown End",
        "category": "Shutdown",
        "notes": "35-day shutdown ended; equities stabilized, yields backed up.",
        "summary": """Wall shutdown resolution - Jan 25, 2019

Shutdown resolution announced after 35 days. Some profit-taking in ZN as risk-off flows eased.
Gradual yield backup as growth concerns eased, but ZN stayed bid vs pre-shutdown levels.""",
        "date": "2019-01-25",
        "event_time_local": "",
        "source_tz": "America/New_York",
        "day_of_week": "Friday",
    },
    
    # 3. October 2025 shutdown over ACA subsidies - start and resolution
    {
        "id": 3,
        "name": "ACA Shutdown Start",
        "category": "Shutdown",
        "notes": "Shutdown fight over ACA subsidies added policy uncertainty on top of deficit and rate path worries.",
        "summary": """October 2025 shutdown over ACA subsidies

In 2025 the shutdown fight over ACA subsidies added another layer of policy uncertainty on top of existing worries about deficits and the rate path.

Historically, shutdown risk tends to flatten the curve somewhat in the short run (front-end anchored by Fed expectations, some safe-haven demand in the belly), which is supportive of 10-year prices/ZN while the standoff looks serious.""",
        "date": "2025-10-01",
        "event_time_local": "",
        "source_tz": "America/New_York",
        "day_of_week": "Wednesday",
    },
    {
        "id": 3.5,
        "name": "ACA Shutdown Resolution",
        "category": "Shutdown",
        "notes": "Compromise bill passed; safe-haven demand eased.",
        "summary": """ACA Shutdown Resolution - Nov 5, 2025

Once a compromise funding bill advanced in early November 2025 and it became clear the shutdown would end, safe-haven demand would be expected to ease, allowing 10-year yields to rise somewhat and ZN to retrace part of any prior risk-off rally.""",
        "date": "2025-11-05",
        "event_time_local": "",
        "source_tz": "America/New_York",
        "day_of_week": "Wednesday",
    },
    
    # =========================================================================
    # CATEGORY: Canada tariff
    # =========================================================================
    # 1. Phase 1: C$30B at 25%
    {
        "id": 301,
        "name": "Canada Phase 1: C$30B at 25%",
        "category": "Canada tariff",
        "notes": "ZN rallied on tariff-driven growth fears and Fed cut expectations.",
        "summary": """Canada Retaliatory Tariffs - ZN Market Impact

ZN (10-year Treasury note futures) mainly moved as a "risk-off then partial reversal" trade around those Canada-tariff dates:

Into 4 Mar 2025 (Phase 1 C$30B at 25%): ZN rallied noticeably over the prior couple of weeks as 10-year yields fell on tariff-driven growth fears and expectations of Fed cuts.

Around 13 Mar 2025 (extra 25% on steel/aluminum/misc.): ZN stayed elevated; daily moves were mostly a few ticks, with gains on negative trade headlines and small pullbacks when relief or exemptions were discussed.

Around 9 Apr 2025 (25% on non-CUSMA-compliant autos): ZN remained higher than before the whole tariff episode; yields hovered near just above 4%, so price was still in "risk-off" territory but without a fresh large spike tied only to this auto step.

1 Sep 2025 (Canada removes many retaliatory tariffs): De-escalation modestly supported a "risk-on" tilt, contributing to some softening in ZN (yields edging up), but the move was incremental and part of a broader trade-war repricing rather than a sharp one-day swing.

In short, ZN's big move was the overall Q1-Q2 2025 rally on global tariff and retaliation fears; each specific Canadian retaliation date tended to cause only modest, intraday-scale price changes on top of that trend.""",
        "date": "2025-03-04",
        "event_time_local": "",
        "source_tz": "America/Toronto",
        "day_of_week": "Tuesday",
    },
    # 2. Extra 25% on steel/aluminum/misc
    {
        "id": 302,
        "name": "Canada: Extra 25% on Steel/Aluminum",
        "category": "Canada tariff",
        "notes": "ZN stayed elevated; modest intraday moves on trade headlines.",
        "summary": """Canada Retaliatory Tariffs - ZN Market Impact

ZN (10-year Treasury note futures) mainly moved as a "risk-off then partial reversal" trade around those Canada-tariff dates:

Into 4 Mar 2025 (Phase 1 C$30B at 25%): ZN rallied noticeably over the prior couple of weeks as 10-year yields fell on tariff-driven growth fears and expectations of Fed cuts.

Around 13 Mar 2025 (extra 25% on steel/aluminum/misc.): ZN stayed elevated; daily moves were mostly a few ticks, with gains on negative trade headlines and small pullbacks when relief or exemptions were discussed.

Around 9 Apr 2025 (25% on non-CUSMA-compliant autos): ZN remained higher than before the whole tariff episode; yields hovered near just above 4%, so price was still in "risk-off" territory but without a fresh large spike tied only to this auto step.

1 Sep 2025 (Canada removes many retaliatory tariffs): De-escalation modestly supported a "risk-on" tilt, contributing to some softening in ZN (yields edging up), but the move was incremental and part of a broader trade-war repricing rather than a sharp one-day swing.

In short, ZN's big move was the overall Q1-Q2 2025 rally on global tariff and retaliation fears; each specific Canadian retaliation date tended to cause only modest, intraday-scale price changes on top of that trend.""",
        "date": "2025-03-13",
        "event_time_local": "",
        "source_tz": "America/Toronto",
        "day_of_week": "Thursday",
    },
    # 3. 25% on non-CUSMA-compliant autos
    {
        "id": 303,
        "name": "Canada: 25% on Non-CUSMA Autos",
        "category": "Canada tariff",
        "notes": "ZN remained higher; yields ~4%, still in risk-off territory.",
        "summary": """Canada Retaliatory Tariffs - ZN Market Impact

ZN (10-year Treasury note futures) mainly moved as a "risk-off then partial reversal" trade around those Canada-tariff dates:

Into 4 Mar 2025 (Phase 1 C$30B at 25%): ZN rallied noticeably over the prior couple of weeks as 10-year yields fell on tariff-driven growth fears and expectations of Fed cuts.

Around 13 Mar 2025 (extra 25% on steel/aluminum/misc.): ZN stayed elevated; daily moves were mostly a few ticks, with gains on negative trade headlines and small pullbacks when relief or exemptions were discussed.

Around 9 Apr 2025 (25% on non-CUSMA-compliant autos): ZN remained higher than before the whole tariff episode; yields hovered near just above 4%, so price was still in "risk-off" territory but without a fresh large spike tied only to this auto step.

1 Sep 2025 (Canada removes many retaliatory tariffs): De-escalation modestly supported a "risk-on" tilt, contributing to some softening in ZN (yields edging up), but the move was incremental and part of a broader trade-war repricing rather than a sharp one-day swing.

In short, ZN's big move was the overall Q1-Q2 2025 rally on global tariff and retaliation fears; each specific Canadian retaliation date tended to cause only modest, intraday-scale price changes on top of that trend.""",
        "date": "2025-04-09",
        "event_time_local": "",
        "source_tz": "America/Toronto",
        "day_of_week": "Wednesday",
    },
    # 4. Canada removes many retaliatory tariffs
    {
        "id": 304,
        "name": "Canada Removes Retaliatory Tariffs",
        "category": "Canada tariff",
        "notes": "De-escalation; modest risk-on tilt, ZN softened incrementally.",
        "summary": """Canada Retaliatory Tariffs - ZN Market Impact

ZN (10-year Treasury note futures) mainly moved as a "risk-off then partial reversal" trade around those Canada-tariff dates:

Into 4 Mar 2025 (Phase 1 C$30B at 25%): ZN rallied noticeably over the prior couple of weeks as 10-year yields fell on tariff-driven growth fears and expectations of Fed cuts.

Around 13 Mar 2025 (extra 25% on steel/aluminum/misc.): ZN stayed elevated; daily moves were mostly a few ticks, with gains on negative trade headlines and small pullbacks when relief or exemptions were discussed.

Around 9 Apr 2025 (25% on non-CUSMA-compliant autos): ZN remained higher than before the whole tariff episode; yields hovered near just above 4%, so price was still in "risk-off" territory but without a fresh large spike tied only to this auto step.

1 Sep 2025 (Canada removes many retaliatory tariffs): De-escalation modestly supported a "risk-on" tilt, contributing to some softening in ZN (yields edging up), but the move was incremental and part of a broader trade-war repricing rather than a sharp one-day swing.

In short, ZN's big move was the overall Q1-Q2 2025 rally on global tariff and retaliation fears; each specific Canadian retaliation date tended to cause only modest, intraday-scale price changes on top of that trend.""",
        "date": "2025-09-01",
        "event_time_local": "",
        "source_tz": "America/Toronto",
        "day_of_week": "Monday",
    },
]

# Category colors for visual differentiation
CATEGORY_COLORS = {
    "Shutdown": "#ef4444",  # red
    "Canada tariff": "#3b82f6",      # blue
}

# =============================================================================
# Canada Tariff Timeline Episodes (-7/+7 calendar day windows)
# Each episode has before/after/fade narratives and status (imposed/not_imposed)
# =============================================================================

CANADA_TARIFF_TIMELINE = [
    {
        "id": 1,
        "label": "25% Tariff Imposition",
        "event_date": "2025-03-04",
        "status": "imposed",
        "period_label": "Late Jan–Feb 4/Mar 4, 2025 (25%)",
        "before_text": "Yields stable ~4.5%; ZN flat/slightly down on inflation fears.",
        "after_text": "Yields fell 30–40bp into early Mar (ZN strong rally on growth scares). Post-Mar 4: +3bp yield bounce (ZN dipped modestly).",
        "fade_text": "4–6 weeks total (late Jan–early Apr).",
    },
    {
        "id": 2,
        "label": "Pre-Deadline Rally",
        "event_date": "2025-03-04",
        "status": "imposed",
        "period_label": "Feb 27–Mar 3 → Mar 4, 2025",
        "before_text": "Yields slid to ~4.16%; ZN up sharply pre-deadline.",
        "after_text": "Yield +3bp to 4.216% on Mar 4 (ZN small pullback).",
        "fade_text": "1 week (eve to post-tariff).",
    },
    {
        "id": 3,
        "label": "Steel/Aluminum Double",
        "event_date": "2025-03-12",
        "status": "not_imposed",
        "period_label": "Mar 11–12, 2025 (steel/alum double)",
        "before_text": "Brief ZN tick gains on escalation talk.",
        "after_text": "Not imposed; ZN quickly stabilized (muted).",
        "fade_text": "2–3 days.",
    },
    {
        "id": 4,
        "label": "DST Tariffs",
        "event_date": "2025-06-27",
        "status": "not_imposed",
        "period_label": "Jun 27, 2025 (DST tariffs)",
        "before_text": "Minor safe-haven bid (ZN + few ticks).",
        "after_text": "Not imposed; resolved fast.",
        "fade_text": "2 days.",
    },
    {
        "id": 5,
        "label": "35% Tariff Threat",
        "event_date": "2025-08-01",
        "status": "not_imposed",
        "period_label": "Jul 10–Aug 1, 2025 (35%)",
        "before_text": "Yields dipped modestly (~10bp); ZN mildly higher.",
        "after_text": "Deadline passed w/o action; ZN gave back gains.",
        "fade_text": "1–2 weeks.",
    },
    {
        "id": 6,
        "label": "10% Hike",
        "event_date": "2025-10-25",
        "status": "imposed",
        "period_label": "Oct 25, 2025 (10% hike)",
        "before_text": "ZN up on retaliation fears.",
        "after_text": "Implemented; short yield backup (ZN softened).",
        "fade_text": "3–4 days.",
    },
    {
        "id": 7,
        "label": "100% Tariff Threat",
        "event_date": "2026-01-24",
        "status": "not_imposed",
        "period_label": "Jan 23–24, 2026 (100%)",
        "before_text": "Fresh threat; ZN reaction emerging (yields ~4.24%, stable as of Jan 26).",
        "after_text": "Not yet imposed; ongoing monitoring.",
        "fade_text": "Ongoing (~3 days so far).",
    },
]

# =============================================================================
# Shutdown Timeline Episodes
# Each episode represents a major shutdown period with before/after/fade narratives
# =============================================================================

SHUTDOWN_TIMELINE = [
    {
        "id": 101,
        "label": "DACA Shutdown",
        "event_date": "2018-01-20",
        "status": "shutdown",
        "duration_label": "3d",
        "shutdown_start_date": "2018-01-20",
        "shutdown_end_date": "2018-01-22",
        "shutdown_start_ts": None,
        "shutdown_end_ts": None,
        "period_label": "Jan 20–22, 2018 (3-day shutdown)",
        "before_text": "ZN sold off into the deadline as funding lapsed; politics dominated on DACA.",
        "after_text": "Safe-haven demand stabilized Treasuries during the shutdown and into Jan 22.",
        "fade_text": "ZN rebounded into Jan 23; move was more risk-off than fiscal.",
        "politics_bullets": [
            "Senate Democrats blocked a short-term CR, demanding DACA protections.",
            "Shutdown began at midnight Jan 20; resolved Jan 22 after McConnell promised an immigration debate."
        ],
        "econ_auction_bullets": [
            "Light economic calendar with no major Treasury auctions in the window.",
            "Market focus centered on politics and risk sentiment rather than supply."
        ],
        "price_action_bullets": [
            "Jan 19: ZN sold off into the deadline (RSI oversold ~31).",
            "Jan 20–22: Treasuries stabilized and rebounded as shutdown began.",
            "Post-reopen: ZN bounced roughly +12 ticks on risk-off flows."
        ],
        "approx_move_ticks": 12,
        "approx_move_bps": round(ticks_to_bps(12), 2),
    },
    {
        "id": 102,
        "label": "Rand Paul 9h Shutdown",
        "event_date": "2018-02-09",
        "status": "shutdown",
        "duration_label": "9h",
        "shutdown_start_date": "2018-02-09",
        "shutdown_end_date": "2018-02-09",
        "shutdown_start_ts": "2018-02-09 00:01:00",
        "shutdown_end_ts": "2018-02-09 05:30:00",
        "period_label": "Feb 9, 2018 (9-hour overnight shutdown)",
        "before_text": "ZN weakened into the Feb 8 30y auction and deficit-expanding budget deal.",
        "after_text": "Overnight shutdown headlines triggered a brief safe-haven spike early Feb 9.",
        "fade_text": "Gains faded; ZN drifted lower as issuance/deficit concerns reasserted.",
        "politics_bullets": [
            "Triggered when Sen. Rand Paul objected to deficit impact of a bipartisan budget deal.",
            "Delay pushed the vote past midnight, causing a technical shutdown.",
            "Resolved before U.S. markets opened (deal passed ~5:30 a.m. EST).",
            "Deal raised defense + domestic caps (~$300bn over 2 years)."
        ],
        "econ_auction_bullets": [
            "Feb 8: 30y auction tailed badly (3.12% vs ~2.87% prior).",
            "Feb 9 calendar: only Wholesale Inventories (not impactful).",
            "Macro input dominated by weak auction + expectations of larger issuance."
        ],
        "price_action_bullets": [
            "Leading into Feb 8 auction: ZN sold off (yields up).",
            "Early Feb 9 (~06:00–10:00): quick spike up on shutdown headlines.",
            "Later in the day: gains faded and ZN drifted lower on deficit/issuance focus."
        ],
        "approx_move_ticks": -27,
        "approx_move_bps": round(ticks_to_bps(-27), 2),
    },
    {
        "id": 103,
        "label": "Wall Shutdown",
        "event_date": "2018-12-22",
        "status": "shutdown",
        "duration_label": "35d",
        "shutdown_start_date": "2018-12-22",
        "shutdown_end_date": "2019-01-25",
        "shutdown_start_ts": None,
        "shutdown_end_ts": None,
        "period_label": "Dec 22, 2018 – Jan 25, 2019 (35-day shutdown)",
        "before_text": "Strong Dec 21 data + looming shutdown pushed ZN lower into 121'16.",
        "after_text": "Prolonged rally as shutdown dragged and Powell pivoted dovish.",
        "fade_text": "By Jan 25, ZN remained well above pre-shutdown levels despite some easing.",
        "politics_bullets": [
            "Triggered by refusal to sign a CR without $5.7bn for border wall funding.",
            "Standoff lasted until Jan 25, 2019 when funding resumed."
        ],
        "econ_auction_bullets": [
            "Dec 21 data strong (Durable goods +0.8%, GDP +3.4%, Sentiment 98.3).",
            "Shutdown led to data blackout (Census/BEA releases suspended).",
            "January auctions generally cleared well; foreign/real-money demand present.",
            "Jan 4 Powell pivoted to a more patient stance."
        ],
        "price_action_bullets": [
            "Dec 21: ZN sold off into shutdown (~121'16).",
            "Late Dec–Jan: equities slumped, volatility spiked, Treasuries rallied.",
            "By Jan 25: ZN traded near 123'16 (~+2 points from late Dec)."
        ],
        "approx_move_ticks": 65,
        "approx_move_bps": round(ticks_to_bps(65), 2),
    },
    {
        "id": 105,
        "label": "Wall Shutdown Reopens",
        "event_date": "2019-01-25",
        "status": "shutdown",
        "duration_label": "35d",
        "shutdown_start_date": "2018-12-22",
        "shutdown_end_date": "2019-01-25",
        "shutdown_start_ts": None,
        "shutdown_end_ts": None,
        "period_label": "Jan 25, 2019 (Reopening)",
        "before_text": "ZN remained elevated from the December–January rally.",
        "after_text": "Shutdown resolution sparked modest profit-taking in risk-off positioning.",
        "fade_text": "Yields backed up gradually, but ZN stayed above pre-shutdown levels.",
        "politics_bullets": [
            "Funding restored after the 35-day impasse over border wall funding.",
            "Reopening occurred under public pressure and economic disruption."
        ],
        "econ_auction_bullets": [
            "Data blackout ended as agencies reopened.",
            "Auctions in January had generally cleared well; no funding stress."
        ],
        "price_action_bullets": [
            "Risk-off premium eased after reopening.",
            "ZN gave back a slice of the rally but held elevated levels."
        ],
        "approx_move_ticks": None,
        "approx_move_bps": None,
    },
    {
        "id": 104,
        "label": "FY2026 Shutdown",
        "event_date": "2025-10-01",
        "status": "shutdown",
        "duration_label": "6w",
        "shutdown_start_date": "2025-10-01",
        "shutdown_end_date": "2025-11-12",
        "shutdown_start_ts": None,
        "shutdown_end_ts": None,
        "period_label": "Oct 1 – Nov 12, 2025 (~6 weeks)",
        "before_text": "Shutdown widely telegraphed; late-September pricing already reflected risk.",
        "after_text": "Initial safe-haven bid then range trading as macro repricing dominated.",
        "fade_text": "Late-Oct breakdown driven by term-premium/growth, not shutdown escalation.",
        "politics_bullets": [
            "Failure to pass FY2026 appropriations by Oct 1 deadline.",
            "Dispute centered on spending levels and policy riders; no clean CR.",
            "Reopened mid-November on stopgap funding; no structural tightening."
        ],
        "econ_auction_bullets": [
            "Some government data delayed, but private indicators filled gaps.",
            "Treasury issuance continued uninterrupted; auctions cleared normally.",
            "Market focus stayed on Fed path, term premium, and growth slowdown."
        ],
        "price_action_bullets": [
            "Initial Oct 1 safe-haven rally (~7 bps).",
            "Thereafter ZN range traded; shutdown largely discounted.",
            "Net move modest as macro repricing set the tone."
        ],
        "approx_move_ticks": 9,
        "approx_move_bps": round(ticks_to_bps(9), 2),
    },
    {
        "id": 106,
        "label": "FY2026 Shutdown Reopens",
        "event_date": "2025-11-12",
        "status": "shutdown",
        "duration_label": "6w",
        "shutdown_start_date": "2025-10-01",
        "shutdown_end_date": "2025-11-12",
        "shutdown_start_ts": None,
        "shutdown_end_ts": None,
        "period_label": "Nov 12, 2025 (Reopening)",
        "before_text": "ZN held a modest bid into the reopening as risk-off had been priced.",
        "after_text": "Stopgap funding removed shutdown premium without a structural shift in fiscal stance.",
        "fade_text": "Macro repricing and term premium remained the dominant drivers.",
        "politics_bullets": [
            "Government reopened mid-November on a stopgap funding agreement.",
            "No material fiscal tightening; disputes were deferred."
        ],
        "econ_auction_bullets": [
            "Data releases normalized as agencies reopened.",
            "Treasury issuance and auction cadence had stayed steady throughout."
        ],
        "price_action_bullets": [
            "Shutdown premium faded without a sharp reversal.",
            "ZN remained range-bound as macro drivers dominated."
        ],
        "approx_move_ticks": None,
        "approx_move_bps": None,
    },
]

# Combined timelines by category
CATEGORY_TIMELINES = {
    "Canada tariff": CANADA_TARIFF_TIMELINE,
    "Shutdown": SHUTDOWN_TIMELINE,
}

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
    Expand POLICY_EVENTS_RAW into a list of single-day events.
    Multi-day events are split into one entry per calendar day.
    Each event gets event_start/event_end in ET (if time is known).
    """
    expanded = []
    
    for raw_event in POLICY_EVENTS_RAW:
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
                "summary": raw_event.get("summary", ""),
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
POLICY_EVENTS = expand_events()


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


def fetch_timeline_window(event_date: str, conn, days_before: int = 7, days_after: int = 7) -> Dict[str, Any]:
    """
    Fetch daily OHLC data for -N/+N calendar day window around an event date.
    Returns daily data (resampled from 1-min) for timeline charts.
    """
    cur = conn.cursor()
    
    dt = datetime.strptime(event_date, '%Y-%m-%d')
    query_start = dt - timedelta(days=days_before)
    query_end = dt + timedelta(days=days_after + 1)
    
    # Get daily OHLC by grouping 1-min data
    cur.execute("""
        SELECT 
            date(ts_event) as trade_date,
            (array_agg(open ORDER BY ts_event))[1] as open,
            MAX(high) as high,
            MIN(low) as low,
            (array_agg(close ORDER BY ts_event DESC))[1] as close,
            SUM(volume) as volume
        FROM ohlcv_1m 
        WHERE symbol = 'TY' 
          AND ts_event >= %s 
          AND ts_event < %s
        GROUP BY date(ts_event)
        ORDER BY trade_date
    """, (query_start, query_end))
    
    rows = cur.fetchall()
    cur.close()
    
    if not rows:
        return {'dates': [], 'opens': [], 'highs': [], 'lows': [], 'closes': [], 'volumes': []}
    
    return {
        'dates': [row[0].isoformat() for row in rows],
        'opens': [float(row[1]) for row in rows],
        'highs': [float(row[2]) for row in rows],
        'lows': [float(row[3]) for row in rows],
        'closes': [float(row[4]) for row in rows],
        'volumes': [int(row[5]) if row[5] else 0 for row in rows],
    }


def fetch_timeline_candlestick(event_date: str, conn, days_before: int = 7, days_after: int = 7) -> Dict[str, Any]:
    """
    Fetch 1-minute TY price data for -N/+N calendar day window.
    Used for timeline candlestick modal with replay slider.
    """
    cur = conn.cursor()
    
    dt = datetime.strptime(event_date, '%Y-%m-%d')
    query_start = dt - timedelta(days=days_before)
    query_end = dt + timedelta(days=days_after + 1)
    
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


def fetch_timeline_window_for_interval(start_date: str, end_date: str, conn, pad_days: int = 5) -> Dict[str, Any]:
    """
    Fetch daily OHLC data for a shutdown interval, padded by +/- pad_days.
    Returns daily data (resampled from 1-min) for timeline charts.
    """
    cur = conn.cursor()
    
    start_dt = datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=pad_days)
    end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=pad_days)
    query_start = start_dt
    query_end = end_dt + timedelta(days=1)
    
    cur.execute("""
        SELECT 
            date(ts_event) as trade_date,
            (array_agg(open ORDER BY ts_event))[1] as open,
            MAX(high) as high,
            MIN(low) as low,
            (array_agg(close ORDER BY ts_event DESC))[1] as close,
            SUM(volume) as volume
        FROM ohlcv_1m 
        WHERE symbol = 'TY' 
          AND ts_event >= %s 
          AND ts_event < %s
        GROUP BY date(ts_event)
        ORDER BY trade_date
    """, (query_start, query_end))
    
    rows = cur.fetchall()
    cur.close()
    
    if not rows:
        return {'dates': [], 'opens': [], 'highs': [], 'lows': [], 'closes': [], 'volumes': []}
    
    return {
        'dates': [row[0].isoformat() for row in rows],
        'opens': [float(row[1]) for row in rows],
        'highs': [float(row[2]) for row in rows],
        'lows': [float(row[3]) for row in rows],
        'closes': [float(row[4]) for row in rows],
        'volumes': [int(row[5]) if row[5] else 0 for row in rows],
    }


def fetch_timeline_candlestick_for_interval(start_date: str, end_date: str, conn, pad_days: int = 5) -> Dict[str, Any]:
    """
    Fetch 1-minute TY price data for a shutdown interval, padded by +/- pad_days.
    """
    cur = conn.cursor()
    
    start_dt = datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=pad_days)
    end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=pad_days)
    query_start = start_dt
    query_end = end_dt + timedelta(days=1)
    
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


def compute_timeline_metrics(window_data: Dict[str, Any], event_date: str) -> Dict[str, Any]:
    """
    Compute metrics for a timeline window:
    - pre_move: bps change from window start to event date
    - post_move: bps change from event date to window end
    - max_up: max favorable bps move in window (from start)
    - max_down: max adverse bps move in window (from start)
    - panic_fade: post-event peak move vs end-of-window reversal
    """
    dates = window_data.get('dates', [])
    closes = window_data.get('closes', [])
    
    if len(closes) < 2:
        return {
            'pre_move': 0, 'post_move': 0, 'max_up': 0, 'max_down': 0,
            'panic_fade': 0, 'has_data': False
        }
    
    # Find event date index
    event_idx = None
    for i, d in enumerate(dates):
        if d == event_date:
            event_idx = i
            break
    
    # If event date not in data, find nearest
    if event_idx is None:
        event_dt = datetime.strptime(event_date, '%Y-%m-%d')
        for i, d in enumerate(dates):
            dt = datetime.strptime(d, '%Y-%m-%d')
            if dt >= event_dt:
                event_idx = i
                break
        if event_idx is None:
            event_idx = len(dates) - 1
    
    ref_price = closes[0]
    
    # Compute bps changes from window start
    bps_changes = [points_to_bps(c - ref_price) for c in closes]
    
    # Pre move: window start to event date
    pre_move = round(bps_changes[event_idx], 2) if event_idx < len(bps_changes) else 0
    
    # Post move: event date to window end
    post_move = round(bps_changes[-1] - bps_changes[event_idx], 2) if event_idx < len(bps_changes) else 0
    
    # Max moves
    max_up = round(max(bps_changes), 2)
    max_down = round(min(bps_changes), 2)
    
    # Panic fade: peak after event vs end reversal
    post_event_bps = bps_changes[event_idx:] if event_idx < len(bps_changes) else []
    if len(post_event_bps) >= 2:
        post_peak = max(post_event_bps) if post_event_bps else 0
        post_trough = min(post_event_bps) if post_event_bps else 0
        end_value = post_event_bps[-1]
        # Fade is how much of the peak move reversed
        if abs(post_peak - post_event_bps[0]) > abs(post_trough - post_event_bps[0]):
            panic_fade = round(post_peak - end_value, 2)
        else:
            panic_fade = round(end_value - post_trough, 2)
    else:
        panic_fade = 0
    
    return {
        'pre_move': pre_move,
        'post_move': post_move,
        'max_up': max_up,
        'max_down': max_down,
        'panic_fade': panic_fade,
        'has_data': True,
        'bps_path': bps_changes,
        'dates': dates,
        'event_idx': event_idx,
    }


def compute_shutdown_duration_days(start_date: str, end_date: str, start_ts: Optional[str], end_ts: Optional[str]) -> float:
    """
    Compute shutdown duration in days. Uses intraday timestamps when provided,
    otherwise uses inclusive calendar days.
    """
    if start_ts and end_ts:
        try:
            start_dt = datetime.fromisoformat(start_ts)
            end_dt = datetime.fromisoformat(end_ts)
            if start_dt.tzinfo:
                start_dt = start_dt.replace(tzinfo=None)
            if end_dt.tzinfo:
                end_dt = end_dt.replace(tzinfo=None)
            delta = end_dt - start_dt
            return max(delta.total_seconds() / 86400.0, 0.0)
        except Exception:
            pass
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    return max((end_dt - start_dt).days + 1, 0)


def get_shutdown_duration_bucket(duration_days: float) -> str:
    """
    Bucket shutdown durations for distro filters.
    """
    if duration_days < 1:
        return "lt1d"
    if duration_days <= 3:
        return "0-3"
    if duration_days <= 7:
        return "0-7"
    if duration_days <= 14:
        return "0-14"
    return "gt14d"


def compute_shutdown_metrics(
    window_data: Dict[str, Any],
    candlestick_data: Dict[str, Any],
    start_date: str,
    end_date: str,
    start_ts: Optional[str],
    end_ts: Optional[str]
) -> Dict[str, Any]:
    """
    Compute metrics strictly inside the shutdown interval.
    Uses intraday timestamps when provided, else uses inclusive calendar dates.
    """
    if start_ts and end_ts and candlestick_data.get('timestamps'):
        try:
            start_dt = datetime.fromisoformat(start_ts)
            end_dt = datetime.fromisoformat(end_ts)
            if start_dt.tzinfo:
                start_dt = start_dt.replace(tzinfo=None)
            if end_dt.tzinfo:
                end_dt = end_dt.replace(tzinfo=None)
        except Exception:
            start_dt = None
            end_dt = None
        if start_dt and end_dt:
            indices = []
            for i, ts in enumerate(candlestick_data['timestamps']):
                try:
                    ts_dt = datetime.fromisoformat(ts)
                    if ts_dt.tzinfo:
                        ts_dt = ts_dt.replace(tzinfo=None)
                except Exception:
                    continue
                if start_dt <= ts_dt <= end_dt:
                    indices.append(i)
            if indices:
                start_idx = indices[0]
                end_idx = indices[-1]
                start_price = candlestick_data['opens'][start_idx]
                end_price = candlestick_data['closes'][end_idx]
                highs = [candlestick_data['highs'][i] for i in indices]
                lows = [candlestick_data['lows'][i] for i in indices]
                max_high = max(highs)
                min_low = min(lows)
                return {
                    'period_return_bps': round(points_to_bps(end_price - start_price), 2),
                    'period_volatility_bps': round(points_to_bps(max_high - min_low), 2),
                    'max_up_bps': round(points_to_bps(max_high - start_price), 2),
                    'max_down_bps': round(points_to_bps(min_low - start_price), 2),
                    'has_data': True,
                }
    dates = window_data.get('dates', [])
    opens = window_data.get('opens', [])
    highs = window_data.get('highs', [])
    lows = window_data.get('lows', [])
    closes = window_data.get('closes', [])
    if not dates:
        return {'has_data': False}
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    indices = []
    for i, d in enumerate(dates):
        day_dt = datetime.strptime(d, '%Y-%m-%d')
        if start_dt <= day_dt <= end_dt:
            indices.append(i)
    if not indices:
        return {'has_data': False}
    start_idx = indices[0]
    end_idx = indices[-1]
    start_price = opens[start_idx]
    end_price = closes[end_idx]
    max_high = max(highs[i] for i in indices)
    min_low = min(lows[i] for i in indices)
    return {
        'period_return_bps': round(points_to_bps(end_price - start_price), 2),
        'period_volatility_bps': round(points_to_bps(max_high - min_low), 2),
        'max_up_bps': round(points_to_bps(max_high - start_price), 2),
        'max_down_bps': round(points_to_bps(min_low - start_price), 2),
        'has_data': True,
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
    """
    Fetch major economic events from SQLite database for a date range.
    Filters for high-importance events like NFP, CPI, PMI, FOMC, GDP, etc.
    """
    import sqlite3
    
    db_path = PROJECT_ROOT / "workspace" / "economic_events.db"
    if not db_path.exists():
        logger.warning(f"Economic events DB not found: {db_path}")
        return []
    
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    
    # Keywords for major market-moving events
    major_event_keywords = [
        'Nonfarm Payroll', 'NFP', 'Employment Change', 'Unemployment Rate',
        'CPI', 'Consumer Price Index', 'Inflation',
        'PMI', 'ISM Manufacturing', 'ISM Services',
        'FOMC', 'Fed Interest Rate', 'Federal Reserve', 'Fed Chair',
        'GDP', 'Gross Domestic Product',
        'Retail Sales', 'Durable Goods', 'Trade Balance',
        'Initial Jobless Claims', 'ADP Employment',
        'PCE', 'Personal Consumption', 'Core PCE'
    ]
    
    # Build OR clause for keywords
    keyword_conditions = ' OR '.join([f"event_name LIKE '%{kw}%'" for kw in major_event_keywords])
    
    cur.execute(f"""
        SELECT event_name, event_date, importance, actual, consensus, previous
        FROM economic_events
        WHERE date(event_date) >= ?
          AND date(event_date) <= ?
          AND country LIKE '%United States%'
          AND ({keyword_conditions} OR importance = 'high')
        ORDER BY event_date
    """, (start_date, end_date))
    
    rows = cur.fetchall()
    conn.close()
    
    events = []
    for row in rows:
        events.append({
            'name': row[0],
            'timestamp': row[1],
            'importance': row[2] or 'high',
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
        highs = price_data.get('highs', [])
        lows = price_data.get('lows', [])
        
        if len(closes) >= 2:
            # Return = close - open in bps
            open_price = closes[0]
            close_price = closes[-1]
            ret_bps = points_to_bps(close_price - open_price)
            returns.append(ret_bps)
            
            # Volatility = (max high - min low) in bps
            if highs and lows:
                vol_bps = points_to_bps(max(highs) - min(lows))
                volatilities.append(vol_bps)
            
            # Compute window statistics
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
        import numpy as np
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


def generate_html(events_data: List[Dict], distributions: Dict, categories: List[str], timeline_data: List[Dict] = None) -> str:
    """Generate the HTML dashboard."""
    
    # Serialize data for JavaScript
    events_json = json.dumps(events_data, indent=2)
    distributions_json = json.dumps(distributions, indent=2)
    categories_json = json.dumps(categories, indent=2)
    category_colors_json = json.dumps(CATEGORY_COLORS, indent=2)
    timeline_json = json.dumps(timeline_data or [], indent=2)
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Policy & Macro Events Analysis - TY Price Paths</title>
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
        
        /* Category filter pills */
        .category-filter {{
            display: flex;
            gap: 10px;
            margin-top: 20px;
            flex-wrap: wrap;
        }}
        
        .category-pill {{
            padding: 8px 16px;
            border-radius: 20px;
            border: 1px solid var(--border-color);
            background: var(--bg-tertiary);
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.2s;
            font-size: 0.85rem;
            font-weight: 500;
        }}
        
        .category-pill:hover {{
            border-color: var(--accent-cyan);
            color: var(--text-primary);
        }}
        
        .category-pill.active {{
            background: var(--accent-teal);
            border-color: var(--accent-teal);
            color: white;
        }}
        
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
            grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
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
        
        .event-card.hidden {{ display: none; }}
        
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
        
        .event-summary-snippet {{
            font-size: 0.8rem;
            color: var(--text-secondary);
            margin-top: 8px;
            padding: 10px;
            background: rgba(20, 184, 166, 0.05);
            border-radius: 6px;
            border-left: 3px solid var(--accent-teal);
        }}
        
        /* Distribution controls */
        .distro-controls {{
            display: flex;
            flex-wrap: wrap;
            gap: 20px;
            align-items: center;
            margin-bottom: 20px;
            padding: 15px;
            background: var(--bg-tertiary);
            border-radius: 8px;
        }}
        
        .distro-control-group {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .distro-label {{
            font-size: 0.9rem;
            font-weight: 600;
            color: var(--text-primary);
        }}
        
        .distro-range {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .range-label {{
            font-size: 0.85rem;
            color: var(--text-secondary);
        }}
        
        .distro-range select, .distro-control-group select {{
            padding: 6px 10px;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            color: var(--text-primary);
            font-size: 0.85rem;
            cursor: pointer;
        }}
        
        .distro-range select:focus, .distro-control-group select:focus {{
            outline: none;
            border-color: var(--accent-blue);
        }}
        
        #range-controls {{
            display: flex;
            align-items: center;
            gap: 15px;
        }}
        
        .distro-checkbox {{
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            font-size: 0.9rem;
            color: var(--text-secondary);
        }}
        
        .distro-checkbox input {{
            width: 18px;
            height: 18px;
            accent-color: var(--accent-blue);
            cursor: pointer;
        }}
        
        .distro-info {{
            font-size: 0.85rem;
            color: var(--accent-cyan);
            font-family: 'JetBrains Mono', monospace;
            padding: 6px 12px;
            background: rgba(6, 182, 212, 0.1);
            border-radius: 4px;
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
        
        .modal-summary {{
            margin-top: 15px;
            padding: 15px;
            background: var(--bg-card);
            border-radius: 8px;
            border-left: 3px solid var(--accent-teal);
            white-space: pre-wrap;
            font-size: 0.9rem;
            color: var(--text-secondary);
            line-height: 1.7;
            max-height: 250px;
            overflow-y: auto;
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
        
        /* Timeline styles */
        .timeline-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(450px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }}
        
        .timeline-card {{
            background: var(--bg-card);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid var(--border-color);
            transition: all 0.25s ease;
        }}
        
        .timeline-card:hover {{
            border-color: var(--accent-blue);
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(59, 130, 246, 0.15);
        }}
        
        .timeline-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }}
        
        .timeline-label {{
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--text-primary);
        }}
        
        .timeline-date {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            color: var(--accent-amber);
        }}
        
        .timeline-status {{
            font-size: 0.75rem;
            padding: 4px 10px;
            border-radius: 12px;
            font-weight: 500;
        }}
        
        .timeline-status.imposed {{
            background: rgba(239, 68, 68, 0.2);
            color: #ef4444;
        }}
        
        .timeline-status.not_imposed {{
            background: rgba(34, 197, 94, 0.2);
            color: #22c55e;
        }}

        .timeline-status.duration {{
            background: rgba(251, 191, 36, 0.2);
            color: #fbbf24;
        }}
        
        .timeline-period {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin-bottom: 12px;
            padding: 8px;
            background: var(--bg-tertiary);
            border-radius: 6px;
        }}
        
        .timeline-narrative {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            margin-bottom: 12px;
        }}
        
        .narrative-box {{
            padding: 10px;
            border-radius: 6px;
            font-size: 0.8rem;
        }}
        
        .narrative-box.before {{
            background: rgba(251, 191, 36, 0.1);
            border-left: 3px solid #fbbf24;
        }}
        
        .narrative-box.after {{
            background: rgba(59, 130, 246, 0.1);
            border-left: 3px solid #3b82f6;
        }}
        
        .narrative-box.fade {{
            background: rgba(168, 85, 247, 0.1);
            border-left: 3px solid #a855f7;
        }}
        
        .narrative-title {{
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 4px;
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .narrative-text {{
            color: var(--text-secondary);
            line-height: 1.4;
        }}
        
        .timeline-metrics {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 8px;
            margin-bottom: 12px;
            padding: 10px;
            background: var(--bg-tertiary);
            border-radius: 6px;
        }}
        
        .metric-item {{
            text-align: center;
        }}
        
        .metric-label {{
            font-size: 0.7rem;
            color: var(--text-muted);
            text-transform: uppercase;
            margin-bottom: 2px;
        }}
        
        .metric-value {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            font-weight: 600;
        }}
        
        .metric-value.positive {{ color: var(--accent-green); }}
        .metric-value.negative {{ color: var(--accent-red); }}
        .metric-value.neutral {{ color: var(--text-secondary); }}
        
        .timeline-chart {{
            height: 120px;
            margin-top: 10px;
        }}
        
        /* Events tabs */
        .events-tabs {{
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
        }}
        
        .events-tab {{
            padding: 10px 20px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 0.95rem;
            font-weight: 500;
            transition: all 0.2s;
        }}
        
        .events-tab:hover {{
            background: var(--bg-card);
            color: var(--text-primary);
        }}
        
        .events-tab.active {{
            background: var(--accent-blue);
            border-color: var(--accent-blue);
            color: white;
        }}
        
        .timeline-card {{
            cursor: pointer;
        }}
        
        .timeline-card.selected {{
            border-color: var(--accent-amber);
            box-shadow: 0 0 15px rgba(251, 191, 36, 0.3);
        }}
        
        /* Replay slider styles */
        .replay-container {{
            display: flex;
            align-items: center;
            gap: 15px;
            margin: 15px 0;
            padding: 15px;
            background: var(--bg-tertiary);
            border-radius: 8px;
        }}
        
        .replay-label {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            min-width: 80px;
        }}
        
        .replay-slider {{
            flex: 1;
            height: 8px;
            -webkit-appearance: none;
            background: linear-gradient(to right, var(--accent-blue) 0%, var(--bg-card) 0%);
            border-radius: 4px;
            outline: none;
        }}
        
        .replay-slider::-webkit-slider-thumb {{
            -webkit-appearance: none;
            width: 18px;
            height: 18px;
            background: var(--accent-amber);
            border-radius: 50%;
            cursor: pointer;
            border: 2px solid white;
            box-shadow: 0 2px 6px rgba(0,0,0,0.3);
        }}
        
        .replay-slider::-moz-range-thumb {{
            width: 18px;
            height: 18px;
            background: var(--accent-amber);
            border-radius: 50%;
            cursor: pointer;
            border: 2px solid white;
        }}
        
        .replay-time {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            color: var(--accent-cyan);
            min-width: 160px;
            text-align: right;
        }}
        
        .replay-controls {{
            display: flex;
            gap: 8px;
        }}
        
        .replay-btn {{
            padding: 6px 12px;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            color: var(--text-primary);
            cursor: pointer;
            font-size: 0.9rem;
            transition: all 0.2s;
        }}
        
        .replay-btn:hover {{
            background: var(--accent-blue);
            border-color: var(--accent-blue);
        }}
        
        .replay-btn.playing {{
            background: var(--accent-green);
            border-color: var(--accent-green);
        }}
        
        .replay-speed {{
            padding: 6px 10px;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            color: var(--text-primary);
            font-size: 0.85rem;
            cursor: pointer;
            font-family: 'JetBrains Mono', monospace;
        }}
        
        .replay-speed:focus {{
            outline: none;
            border-color: var(--accent-blue);
        }}
        
        /* Dynamic notes panel for replay */
        .replay-notes {{
            margin-top: 15px;
            padding: 15px;
            background: var(--bg-tertiary);
            border-radius: 8px;
            border-left: 4px solid var(--accent-amber);
        }}
        
        .replay-notes-title {{
            font-weight: 600;
            color: var(--accent-amber);
            margin-bottom: 8px;
            font-size: 0.9rem;
        }}
        
        .replay-notes-content {{
            color: var(--text-secondary);
            font-size: 0.85rem;
            line-height: 1.5;
        }}
        
        .replay-econ-events {{
            margin-top: 10px;
            padding: 10px;
            background: rgba(59, 130, 246, 0.1);
            border-radius: 6px;
            border-left: 3px solid var(--accent-blue);
        }}
        
        .replay-econ-title {{
            font-weight: 600;
            color: var(--accent-blue);
            font-size: 0.85rem;
            margin-bottom: 6px;
        }}
        
        .replay-econ-event {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 4px 0;
            font-size: 0.8rem;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}
        
        .replay-econ-event:last-child {{
            border-bottom: none;
        }}
        
        .replay-econ-name {{
            color: var(--text-primary);
            font-weight: 500;
        }}
        
        .replay-econ-time {{
            color: var(--text-muted);
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.75rem;
        }}
        
        .replay-econ-values {{
            color: var(--accent-cyan);
            font-family: 'JetBrains Mono', monospace;
        }}
        
        /* Live stats during replay */
        .replay-live-stats {{
            margin: 10px 0;
            padding: 12px 15px;
            background: linear-gradient(135deg, rgba(59, 130, 246, 0.1), rgba(168, 85, 247, 0.1));
            border-radius: 8px;
            border: 1px solid rgba(59, 130, 246, 0.3);
        }}
        
        .live-stats-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
        }}
        
        .live-stat-item {{
            text-align: center;
        }}
        
        .live-stat-label {{
            display: block;
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}
        
        .live-stat-value {{
            display: block;
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--text-primary);
        }}
        
        .live-stat-value.positive {{ color: #22c55e; }}
        .live-stat-value.negative {{ color: #ef4444; }}
        .live-stat-value.spike-up {{ 
            color: #22c55e; 
            animation: pulse-green 0.5s ease-out;
        }}
        .live-stat-value.spike-down {{ 
            color: #ef4444; 
            animation: pulse-red 0.5s ease-out;
        }}
        
        @keyframes pulse-green {{
            0% {{ transform: scale(1); background: rgba(34, 197, 94, 0.3); }}
            50% {{ transform: scale(1.1); }}
            100% {{ transform: scale(1); background: transparent; }}
        }}
        
        @keyframes pulse-red {{
            0% {{ transform: scale(1); background: rgba(239, 68, 68, 0.3); }}
            50% {{ transform: scale(1.1); }}
            100% {{ transform: scale(1); background: transparent; }}
        }}
        
        @media (max-width: 600px) {{
            .live-stats-grid {{
                grid-template-columns: repeat(2, 1fr);
            }}
        }}
        
        @media (max-width: 1000px) {{
            .timeline-narrative {{
                grid-template-columns: 1fr;
            }}
            .timeline-metrics {{
                grid-template-columns: repeat(3, 1fr);
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 Policy & Macro Events Analysis</h1>
            <p class="subtitle">TY Treasury Futures price paths around policy/macro events - Calendar day view with event windows highlighted</p>
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
            
            <div class="category-filter" id="category-filter">
                <button class="category-pill active" data-category="all">All Categories</button>
            </div>
        </header>
        
        <div class="section">
            <div class="section-title">Normalized Price Paths (bps change from session start)</div>
            <div id="price-chart"></div>
        </div>
        
        <div class="section">
            <div class="section-title">Events & Timeline (Click to view details)</div>
            <div class="events-tabs" id="events-tabs">
                <button class="events-tab active" data-tab="events" onclick="switchEventsTab('events')">Daily Events</button>
                <button class="events-tab" data-tab="timeline" id="timeline-tab" onclick="switchEventsTab('timeline')" style="display: none;">Timeline (-7/+7d)</button>
            </div>
            <div class="events-grid" id="events-grid"></div>
            <div class="timeline-grid" id="timeline-grid" style="display: none;"></div>
        </div>
        
        <div class="section">
            <div class="section-title">Return & Volatility Distributions</div>
            <div class="distro-controls">
                <div class="distro-control-group" id="distro-mode-group">
                    <label class="distro-label">Mode:</label>
                    <select id="distro-mode" onchange="updateDistroMode()">
                        <option value="range" selected>Date Range</option>
                        <option value="single">Single Day</option>
                    </select>
                </div>
                <div class="distro-control-group" id="shutdown-duration-controls" style="display: none;">
                    <label class="distro-label">Shutdown length:</label>
                    <select id="shutdown-duration" onchange="updateShutdownDurationFilter()">
                        <option value="lt1d" selected>&lt;1 day</option>
                        <option value="0-3">0–3 days</option>
                        <option value="0-7">0–7 days</option>
                        <option value="0-14">0–14 days</option>
                        <option value="gt14d">&gt;14 days</option>
                    </select>
                </div>
                <div class="distro-control-group" id="shutdown-anchor-controls" style="display: none;">
                    <label class="distro-label">Event day:</label>
                    <select id="shutdown-anchor" onchange="updateShutdownDurationFilter()">
                        <option value="start" selected>Shutdown start</option>
                        <option value="end">Shutdown end</option>
                    </select>
                </div>
                <div class="distro-control-group" id="range-controls">
                    <div class="distro-range">
                        <span class="range-label">From:</span>
                        <select id="distro-from" onchange="updateDistroWindow()">
                            <option value="-7">-7 days</option>
                            <option value="-5">-5 days</option>
                            <option value="-3">-3 days</option>
                            <option value="-1">-1 day</option>
                            <option value="0" selected>Event day</option>
                        </select>
                        <span class="range-label">To:</span>
                        <select id="distro-to" onchange="updateDistroWindow()">
                            <option value="0" selected>Event day</option>
                            <option value="1">+1 day</option>
                            <option value="3">+3 days</option>
                            <option value="5">+5 days</option>
                            <option value="7">+7 days</option>
                        </select>
                    </div>
                    <label class="distro-checkbox">
                        <input type="checkbox" id="include-event-day" checked onchange="updateDistroWindow()">
                        <span>Include event day</span>
                    </label>
                </div>
                <div class="distro-control-group" id="single-controls" style="display: none;">
                    <span class="range-label">Day:</span>
                    <select id="distro-single-day" onchange="updateDistroWindow()">
                        <option value="-7">-7 days</option>
                        <option value="-6">-6 days</option>
                        <option value="-5">-5 days</option>
                        <option value="-4">-4 days</option>
                        <option value="-3">-3 days</option>
                        <option value="-2">-2 days</option>
                        <option value="-1">-1 day</option>
                        <option value="0" selected>Event day (0)</option>
                        <option value="1">+1 day</option>
                        <option value="2">+2 days</option>
                        <option value="3">+3 days</option>
                        <option value="4">+4 days</option>
                        <option value="5">+5 days</option>
                        <option value="6">+6 days</option>
                        <option value="7">+7 days</option>
                    </select>
                </div>
                <div class="distro-control-group">
                    <span class="distro-info" id="distro-info">Showing: Event day only</span>
                </div>
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
                <div style="flex: 1;">
                    <div class="modal-title" id="modal-title">Event Details</div>
                    <div class="modal-notes" id="modal-notes"></div>
                    <div class="modal-summary" id="modal-summary"></div>
                </div>
                <div style="display: flex; align-items: flex-start;">
                    <button class="modal-expand" id="expand-btn" onclick="toggleExpand()" title="Expand/Collapse">⛶</button>
                    <button class="modal-close" onclick="closeModal()">&times;</button>
                </div>
            </div>
            <div class="modal-body">
                <div class="replay-container" id="replay-container" style="display: none;">
                    <span class="replay-label">Replay:</span>
                    <div class="replay-controls">
                        <button class="replay-btn" id="replay-play" onclick="toggleReplay()">▶ Play</button>
                        <button class="replay-btn" id="replay-reset" onclick="resetReplay()">↺ Reset</button>
                        <select class="replay-speed" id="replay-speed" onchange="updateReplaySpeed()">
                            <option value="200">0.25x</option>
                            <option value="100">0.5x</option>
                            <option value="50" selected>1x</option>
                            <option value="25">2x</option>
                            <option value="10">5x</option>
                            <option value="5">10x</option>
                        </select>
                    </div>
                    <input type="range" class="replay-slider" id="replay-slider" min="0" max="100" value="100" oninput="updateReplay(this.value)">
                    <span class="replay-time" id="replay-time">-</span>
                </div>
                <div class="replay-notes" id="replay-notes" style="display: none;">
                    <div class="replay-notes-title">📋 Timeline Context</div>
                    <div class="replay-notes-content" id="replay-notes-content"></div>
                    <div class="replay-econ-events" id="replay-econ-events" style="display: none;">
                        <div class="replay-econ-title">📊 Major Economic Events</div>
                        <div id="replay-econ-list"></div>
                    </div>
                </div>
                <div class="replay-live-stats" id="replay-live-stats" style="display: none;">
                    <div class="live-stats-grid">
                        <div class="live-stat-item">
                            <span class="live-stat-label">Current Move</span>
                            <span class="live-stat-value" id="live-current-move">-</span>
                        </div>
                        <div class="live-stat-item">
                            <span class="live-stat-label">Max Up</span>
                            <span class="live-stat-value positive" id="live-max-up">-</span>
                        </div>
                        <div class="live-stat-item">
                            <span class="live-stat-label">Max Down</span>
                            <span class="live-stat-value negative" id="live-max-down">-</span>
                        </div>
                        <div class="live-stat-item">
                            <span class="live-stat-label">Latest Spike</span>
                            <span class="live-stat-value" id="live-spike">-</span>
                        </div>
                    </div>
                </div>
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
        const categories = {categories_json};
        const categoryColors = {category_colors_json};
        const timelineData = {timeline_json};
        
        let activeCategory = 'all';
        
        // Extended color palette for individual event traces (50 distinct colors)
        const colors = [
            '#14b8a6', '#22d3ee', '#a855f7', '#f97316', '#22c55e',
            '#3b82f6', '#ef4444', '#fbbf24', '#ec4899', '#8b5cf6',
            '#06b6d4', '#84cc16', '#f43f5e', '#6366f1', '#eab308',
            '#10b981', '#f59e0b', '#8b5cf6', '#06b6d4', '#d946ef',
            '#0ea5e9', '#65a30d', '#e11d48', '#7c3aed', '#ca8a04',
            '#059669', '#d97706', '#7c3aed', '#0891b2', '#c026d3',
            '#0284c7', '#4d7c0f', '#be123c', '#6d28d9', '#a16207',
            '#047857', '#b45309', '#6d28d9', '#0e7490', '#a21caf',
            '#0369a1', '#3f6212', '#9f1239', '#5b21b6', '#92400e',
            '#065f46', '#9a3412', '#5b21b6', '#155e75', '#86198f',
            '#075985', '#365314', '#881337', '#4c1d95', '#78350f',
            '#064e3b', '#7c2d12', '#4c1d95', '#164e63', '#701a75'
        ];
        
        let activeEventsTab = 'events';
        
        // Categories that have timeline data
        const categoriesWithTimeline = [...new Set(timelineData.map(t => t.category))];
        
        // Check if current category has timeline
        function categoryHasTimeline(category) {{
            if (category === 'all') return false;
            return categoriesWithTimeline.includes(category);
        }}
        
        // Get timeline label for category
        function getTimelineLabel(category) {{
            if (category === 'Canada tariff') return 'Tariff Timeline (-7/+7d)';
            if (category === 'Shutdown') return 'Shutdown Timeline (±5d around start/end)';
            return 'Timeline (-7/+7d)';
        }}
        
        // Update timeline tab visibility based on category
        function updateTimelineTabVisibility() {{
            const timelineTab = document.getElementById('timeline-tab');
            if (categoryHasTimeline(activeCategory)) {{
                timelineTab.style.display = 'inline-block';
                timelineTab.textContent = getTimelineLabel(activeCategory);
            }} else {{
                timelineTab.style.display = 'none';
                // Switch to events tab if timeline tab was active
                if (activeEventsTab === 'timeline') {{
                    switchEventsTab('events');
                }}
            }}
        }}
        
        // Switch between events and timeline tabs
        function switchEventsTab(tab) {{
            activeEventsTab = tab;
            
            // Update tab buttons
            document.querySelectorAll('.events-tab').forEach(t => {{
                if (t.dataset.tab === tab) {{
                    t.classList.add('active');
                }} else {{
                    t.classList.remove('active');
                }}
            }});
            
            // Show/hide grids
            const eventsGrid = document.getElementById('events-grid');
            const timelineGrid = document.getElementById('timeline-grid');
            
            if (tab === 'events') {{
                eventsGrid.style.display = 'grid';
                timelineGrid.style.display = 'none';
            }} else {{
                eventsGrid.style.display = 'none';
                timelineGrid.style.display = 'grid';
                renderTimeline();
            }}
        }}
        
        // Build category filter pills
        const filterContainer = document.getElementById('category-filter');
        categories.forEach(cat => {{
            const pill = document.createElement('button');
            pill.className = 'category-pill';
            pill.dataset.category = cat;
            pill.textContent = cat;
            pill.style.borderColor = categoryColors[cat] || '#374151';
            pill.onclick = () => filterByCategory(cat);
            filterContainer.appendChild(pill);
        }});
        
        function filterByCategory(category) {{
            activeCategory = category;
            
            // Update pill states
            document.querySelectorAll('.category-pill').forEach(p => {{
                if (p.dataset.category === category) {{
                    p.classList.add('active');
                    p.style.background = categoryColors[category] || 'var(--accent-teal)';
                    p.style.borderColor = categoryColors[category] || 'var(--accent-teal)';
                    p.style.color = 'white';
                }} else {{
                    p.classList.remove('active');
                    p.style.background = 'var(--bg-tertiary)';
                    p.style.borderColor = categoryColors[p.dataset.category] || '#374151';
                    p.style.color = 'var(--text-secondary)';
                }}
            }});
            
            // Update cards visibility
            document.querySelectorAll('.event-card').forEach(card => {{
                const idx = parseInt(card.dataset.idx);
                const event = eventsData[idx];
                if (category === 'all' || event.category === category) {{
                    card.classList.remove('hidden');
                }} else {{
                    card.classList.add('hidden');
                }}
            }});
            
            // Replot with filtered data
            plotAllPaths();
            updateDistroControlsForCategory();
            
            // Use window-based distributions if category has timeline, else regular
            if (categoryHasTimeline(category)) {{
                if (category === 'Shutdown') {{
                    updateShutdownDurationFilter();
                }} else {{
                    plotDistributionsWithWindow();
                }}
            }} else {{
                plotDistributions();
            }}
            
            updateStats();
            
            // Update timeline tab visibility and re-render if needed
            updateTimelineTabVisibility();
            if (activeEventsTab === 'timeline' && categoryHasTimeline(category)) {{
                renderTimeline();
            }}
        }}
        
        // Compute distributions from events (for filtered views)
        function computeDistributionsFromEvents(events) {{
            const returns = [];
            const volatilities = [];
            const BPS_PER_POINT = 8;
            
            events.forEach(event => {{
                const prices = event.price_data;
                if (!prices || !prices.closes || prices.closes.length < 2) return;
                
                const openPrice = prices.closes[0];
                const closePrice = prices.closes[prices.closes.length - 1];
                const retBps = (closePrice - openPrice) * BPS_PER_POINT;
                returns.push(retBps);
                
                if (prices.highs && prices.lows && prices.highs.length > 0 && prices.lows.length > 0) {{
                    const volBps = (Math.max(...prices.highs) - Math.min(...prices.lows)) * BPS_PER_POINT;
                    volatilities.push(volBps);
                }}
            }});
            
            return {{ returns, volatilities }};
        }}
        
        // Get filtered events based on active category
        function getFilteredEvents() {{
            if (activeCategory === 'all') {{
                return eventsData.filter(e => e.price_data && e.price_data.closes && e.price_data.closes.length > 0);
            }}
            return eventsData.filter(e => 
                e.category === activeCategory && 
                e.price_data && e.price_data.closes && e.price_data.closes.length > 0
            );
        }}
        
        // Update stats bar based on filtered events
        function updateStats() {{
            const filtered = getFilteredEvents();
            document.getElementById('total-events').textContent = filtered.length;
            
            if (filtered.length > 0) {{
                const dates = filtered.map(e => e.date).sort();
                document.getElementById('date-range').textContent = `${{dates[0]}} - ${{dates[dates.length-1]}}`;
                
                const distro = computeDistributionsFromEvents(filtered);
                
                if (distro.returns.length > 0) {{
                    const avgRet = distro.returns.reduce((a, b) => a + b, 0) / distro.returns.length;
                    document.getElementById('avg-return').textContent = avgRet.toFixed(1) + ' bps';
                }} else {{
                    document.getElementById('avg-return').textContent = '-';
                }}
                
                if (distro.volatilities.length > 0) {{
                    const avgVol = distro.volatilities.reduce((a, b) => a + b, 0) / distro.volatilities.length;
                    document.getElementById('avg-volatility').textContent = avgVol.toFixed(1) + ' bps';
                }} else {{
                    document.getElementById('avg-volatility').textContent = '-';
                }}
            }} else {{
                document.getElementById('date-range').textContent = '-';
                document.getElementById('avg-return').textContent = '-';
                document.getElementById('avg-volatility').textContent = '-';
            }}
        }}
        
        // Initialize stats
        updateStats();
        
        // Truncate text for summary snippet
        function truncate(text, maxLen) {{
            if (!text) return '';
            if (text.length <= maxLen) return text;
            return text.substring(0, maxLen).trim() + '...';
        }}
        
        // Render event cards
        const grid = document.getElementById('events-grid');
        eventsData.forEach((event, idx) => {{
            const hasData = event.price_data && event.price_data.closes && event.price_data.closes.length > 0;
            const card = document.createElement('div');
            card.className = 'event-card' + (hasData ? '' : ' no-data');
            card.dataset.idx = idx;
            
            // Category color
            const catColor = categoryColors[event.category] || '#a855f7';
            
            // Show date shift indicator if source_date differs from plot date
            const dateShiftNote = event.date_shifted 
                ? `<div style="color: #f97316; font-size: 0.75rem; margin-top: 2px;">📅 Event: ${{event.source_date}} → Plotted: ${{event.date}}</div>`
                : '';
            
            // Show time unknown indicator
            const timeNote = event.time_unknown 
                ? '<span style="color: #6b7280; font-size: 0.7rem; margin-left: 6px;">(time unknown)</span>' 
                : '';
            
            // Per-event stats display
            const stats = event.stats || {{}};
            const statsDisplay = hasData && stats.has_data
                ? `<div class="event-stats" style="margin-top: 8px; font-size: 0.75rem; font-family: 'JetBrains Mono', monospace; color: #9ca3af;">
                    <span style="color: ${{stats.max_up_day >= 0 ? '#22c55e' : '#ef4444'}}">↑${{stats.max_up_day > 0 ? '+' : ''}}${{stats.max_up_day}}</span> | 
                    <span style="color: ${{stats.max_down_day <= 0 ? '#ef4444' : '#22c55e'}}">↓${{stats.max_down_day}}</span> bps
                    <span style="margin-left: 8px; color: #6b7280;">Vol: ${{stats.day_volatility}} bps</span>
                   </div>`
                : '';
            
            // Summary snippet (first ~180 chars)
            const summarySnippet = event.summary 
                ? `<div class="event-summary-snippet">${{truncate(event.summary, 180)}}</div>` 
                : '';
            
            card.innerHTML = `
                <div class="event-date">${{event.source_date || event.date}}${{timeNote}}</div>
                ${{dateShiftNote}}
                <span class="event-category" style="background: ${{catColor}}22; color: ${{catColor}}">${{event.category}}</span>
                <div class="event-name">${{event.name}}</div>
                <div class="event-notes" style="font-size: 0.8rem; color: #9ca3af; margin: 4px 0;">${{event.notes}}</div>
                <div class="event-day" style="font-size: 0.75rem; color: #6b7280;">${{event.day_of_week || ''}}</div>
                ${{statsDisplay}}
                ${{summarySnippet}}
                ${{!hasData ? '<div style="color: #ef4444; font-size: 0.8rem; margin-top: 8px;">⚠ No price data available</div>' : ''}}
            `;
            if (hasData) {{
                card.onclick = () => selectEvent(idx);
            }}
            grid.appendChild(card);
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
        
        // Convert hour to time label (0 = 12am, 12 = 12pm, etc.)
        function hourToTimeLabel(hour) {{
            const isPM = hour >= 12;
            const displayHour = Math.floor(hour) % 12 || 12;
            return `${{displayHour}}${{isPM ? 'pm' : 'am'}}`;
        }}
        
        // Plot all normalized price paths
        function plotAllPaths() {{
            const filteredEvents = activeCategory === 'all' 
                ? eventsData 
                : eventsData.filter(e => e.category === activeCategory);
            
            const traces = filteredEvents.map((event, idx) => {{
                const prices = event.price_data;
                if (!prices || !prices.closes || prices.closes.length === 0) return null;
                
                const refPrice = prices.closes[0];
                // From rules/treasury_price_conversion.py:
                // 1 point = 32 ticks, 4 ticks = 1 bp, so 1 point = 8 bps
                const BPS_PER_POINT = 8;
                const bpsChanges = prices.closes.map(p => (p - refPrice) * BPS_PER_POINT);
                
                // Normalize to calendar day hours (0-24)
                const dayHours = normalizeToCalendarDay(prices.timestamps);
                
                // Use unique color per event (not per category)
                const eventColor = colors[idx % colors.length];
                
                return {{
                    x: dayHours,
                    y: bpsChanges,
                    type: 'scatter',
                    mode: 'lines',
                    name: `${{event.date}} - ${{event.name.substring(0, 25)}}`,
                    opacity: 0.7,
                    line: {{ width: 2, color: eventColor }},
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
        function selectEvent(idx) {{
            // Update card selection
            document.querySelectorAll('.event-card').forEach(c => c.classList.remove('selected'));
            document.querySelectorAll('.event-card')[idx].classList.add('selected');
            
            const event = eventsData[idx];
            
            // Update modal content
            const dateDisplay = event.date_shifted 
                ? `${{event.source_date}} → ${{event.date}}` 
                : event.source_date || event.date;
            document.getElementById('modal-title').textContent = `${{dateDisplay}} - ${{event.name}}`;
            
            const timeInfo = event.time_unknown 
                ? '(time unknown - no event window shading)' 
                : `Event window: ${{event.event_start}} to ${{event.event_end}}`;
            
            // Stats for modal
            const stats = event.stats || {{}};
            const statsInfo = stats.has_data
                ? `<div style="margin-top: 10px; padding: 10px; background: #1f2937; border-radius: 6px; font-family: 'JetBrains Mono', monospace; font-size: 0.85rem;">
                    <div style="margin-bottom: 6px; color: #a5b4fc; font-weight: 600;">📊 Day Statistics (bps)</div>
                    <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px;">
                        <span>Return: <strong style="color: ${{stats.day_return >= 0 ? '#22c55e' : '#ef4444'}}">${{stats.day_return >= 0 ? '+' : ''}}${{stats.day_return}}</strong></span>
                        <span>Volatility: <strong style="color: #fbbf24">${{stats.day_volatility}}</strong></span>
                        <span>Max Up: <strong style="color: #22c55e">+${{stats.max_up_day}}</strong></span>
                        <span>Max Down: <strong style="color: #ef4444">${{stats.max_down_day}}</strong></span>
                        <span>5m Max: <strong style="color: #22d3ee">+${{stats.max_up_5m}}/${{stats.max_down_5m}}</strong></span>
                        <span>1h Max: <strong style="color: #a855f7">+${{stats.max_up_1h}}/${{stats.max_down_1h}}</strong></span>
                    </div>
                   </div>`
                : '';
            
            document.getElementById('modal-notes').innerHTML = `
                <strong>Category:</strong> ${{event.category}} | <strong>Day:</strong> ${{event.day_of_week || 'N/A'}}<br>
                <strong>Notes:</strong> ${{event.notes}}<br>
                <span style="color: #9ca3af; font-size: 0.85rem;">${{timeInfo}}</span>
                ${{statsInfo}}
            `;
            
            // Full summary in modal
            const summaryDiv = document.getElementById('modal-summary');
            if (event.summary) {{
                summaryDiv.textContent = event.summary;
                summaryDiv.style.display = 'block';
            }} else {{
                summaryDiv.style.display = 'none';
            }}
            
            // Hide replay container, notes, and live stats (only used for timeline events)
            document.getElementById('replay-container').style.display = 'none';
            document.getElementById('replay-notes').style.display = 'none';
            document.getElementById('replay-live-stats').style.display = 'none';
            
            // Show economic events list
            document.getElementById('econ-events-list').style.display = 'block';
            
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
            const prices = event.candlestick_data || event.price_data;
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
            
            const order = prices.timestamps.map((ts, i) => ({{ ts, i }}))
                .sort((a, b) => new Date(a.ts) - new Date(b.ts));
            const sorted = {{
                timestamps: order.map(o => prices.timestamps[o.i]),
                opens: order.map(o => prices.opens[o.i]),
                highs: order.map(o => prices.highs[o.i]),
                lows: order.map(o => prices.lows[o.i]),
                closes: order.map(o => prices.closes[o.i])
            }};
            
            for (let i = 0; i < sorted.timestamps.length; i += minutes) {{
                const end = Math.min(i + minutes, sorted.timestamps.length);
                const slice = {{
                    opens: sorted.opens.slice(i, end),
                    highs: sorted.highs.slice(i, end),
                    lows: sorted.lows.slice(i, end),
                    closes: sorted.closes.slice(i, end)
                }};
                
                if (slice.opens.length > 0) {{
                    result.timestamps.push(sorted.timestamps[i]);
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
            
            // Reset replay state
            if (replayInterval) {{
                clearInterval(replayInterval);
                replayInterval = null;
            }}
            currentTimelineEpisode = null;
            replayCandles = null;
            document.getElementById('replay-play').textContent = '▶ Play';
            document.getElementById('replay-play').classList.remove('playing');
            
            // Hide replay notes and live stats
            document.getElementById('replay-notes').style.display = 'none';
            document.getElementById('replay-econ-events').style.display = 'none';
            document.getElementById('replay-live-stats').style.display = 'none';
            
            // Reset spike tracking
            prevBpsMove = 0;
            
            // Deselect timeline cards
            document.querySelectorAll('.timeline-card').forEach(c => c.classList.remove('selected'));
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
        
        // Plot distribution charts (uses filtered events based on activeCategory)
        function plotDistributions() {{
            const filtered = getFilteredEvents();
            const distro = computeDistributionsFromEvents(filtered);
            
            // Category label for title
            const catLabel = activeCategory === 'all' ? 'All Categories' : activeCategory;
            
            // Returns distribution with KDE
            if (distro.returns && distro.returns.length > 0) {{
                const returnsTrace = {{
                    x: distro.returns,
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
                const kde = computeKDE(distro.returns, 50);
                const kdeTrace = {{
                    x: kde.x,
                    y: kde.y,
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Density',
                    line: {{ color: '#22d3ee', width: 2 }}
                }};
                
                // Stats (shapes only, no annotations) - include zero percentile for returns
                const statsResult = computeDistroStats(distro.returns, true);
                
                Plotly.newPlot('returns-distro', [returnsTrace, kdeTrace], {{
                    paper_bgcolor: '#111827',
                    plot_bgcolor: '#111827',
                    font: {{ color: '#f9fafb', family: 'Space Grotesk' }},
                    title: {{
                        text: `Returns Distribution - ${{catLabel}} (bps)`,
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
                // Clear chart if no data
                Plotly.purge('returns-distro');
                document.getElementById('returns-stats-box').innerHTML = '<h4>Returns Stats</h4><div style="color: #6b7280;">No data for selected category</div>';
            }}
            
            // Volatility distribution with KDE
            if (distro.volatilities && distro.volatilities.length > 0) {{
                const volTrace = {{
                    x: distro.volatilities,
                    type: 'histogram',
                    name: 'Volatility',
                    marker: {{
                        color: 'rgba(168, 85, 247, 0.6)',
                        line: {{ color: '#a855f7', width: 1 }}
                    }},
                    nbinsx: 15,
                    histnorm: 'probability density'
                }};
                
                const kde = computeKDE(distro.volatilities, 50);
                const kdeTrace = {{
                    x: kde.x,
                    y: kde.y,
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Density',
                    line: {{ color: '#ec4899', width: 2 }}
                }};
                
                // Stats (shapes only, no annotations)
                const statsResult = computeDistroStats(distro.volatilities);
                
                Plotly.newPlot('volatility-distro', [volTrace, kdeTrace], {{
                    paper_bgcolor: '#111827',
                    plot_bgcolor: '#111827',
                    font: {{ color: '#f9fafb', family: 'Space Grotesk' }},
                    title: {{
                        text: `Volatility Distribution - ${{catLabel}} (bps)`,
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
                // Clear chart if no data
                Plotly.purge('volatility-distro');
                document.getElementById('volatility-stats-box').innerHTML = '<h4>Volatility Stats</h4><div style="color: #6b7280;">No data for selected category</div>';
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
        
        // Render timeline cards
        function renderTimeline() {{
            const container = document.getElementById('timeline-grid');
            
            // Filter timeline data by active category
            const filteredTimeline = activeCategory === 'all' 
                ? timelineData 
                : timelineData.filter(t => t.category === activeCategory);
            
            if (!filteredTimeline || filteredTimeline.length === 0) {{
                container.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 40px;">No timeline data available for this category</p>';
                return;
            }}
            
            container.innerHTML = '';
            
            filteredTimeline.forEach((episode, idx) => {{
                const metrics = episode.metrics || {{}};
                const hasData = metrics.has_data;
