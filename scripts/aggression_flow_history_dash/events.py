"""Read-only auction / supply / month-end / tier-1 tags for the flow-history viewer."""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVENTS_DB = Path(r"Y:\Yaman\Fundamentals\Economic Events\Data\events.sqlite")
MONTH_END_DAYS = 3

AUCTION_NAMES = (
    "2-Year Note Auction",
    "3-Year Note Auction",
    "5-Year Note Auction",
    "7-Year Note Auction",
    "10-Year Note Auction",
    "20-Year Bond Auction",
    "30-Year Bond Auction",
    "2Y Auction",
    "3Y Auction",
    "5Y Auction",
    "7Y Auction",
    "10Y Auction",
    "20Y Auction",
    "30Y Auction",
)

COUPON_SUPPLY_PARENTS = (
    "2Y Note Auction",
    "3Y Note Auction",
    "5Y Note Auction",
    "7Y Note Auction",
    "10Y Note Auction",
    "20Y Bond Auction",
    "30Y Bond Auction",
)

TIER1_PARENTS = {
    "NFP": "nfp",
    "CPI": "cpi",
    "FOMC Rate Decision": "fomc",
    "PCE": "pce",
    "GDP Advance": "gdp",
    "GDP Second": "gdp",
    "GDP Third": "gdp",
}

MACRO_FAMILIES: dict[str, tuple[str, ...]] = {
    "nfp": ("NFP",),
    "cpi": ("CPI MoM", "Core CPI MoM", "CPI"),
    "fomc": ("FOMC", "Fed Press Conference", "FOMC Rate Decision"),
}
AUCTION_NAME_SET = set(AUCTION_NAMES) | set(COUPON_SUPPLY_PARENTS)
AUCTION_51030_NAMES = {
    "5-Year Note Auction",
    "10-Year Note Auction",
    "30-Year Bond Auction",
    "5Y Auction",
    "10Y Auction",
    "30Y Auction",
    "5Y Note Auction",
    "10Y Note Auction",
    "30Y Bond Auction",
}
_COUPON_ALIAS_RE = re.compile(
    r"^(2|3|5|7|10|20|30)\s*-?\s*Y(?:EAR)?(?:\s+(?:NOTE|BOND))?\s+AUCTION$",
    re.I,
)
_TIPS_FRN_BILL_RE = re.compile(r"\b(TIPS|FRN|BILL|4W|8W|13W|26W|52W|3M|6M|4M)\b", re.I)

FAMILY_LABELS = {
    "auction": "AUCTION/SUPPLY",
    "month_end": "MONTH-END",
    "tier1": "TIER-1",
    "nfp": "NFP",
    "cpi": "CPI",
    "fomc": "FOMC",
    "pce": "PCE",
    "gdp": "GDP",
}
TIER1_FAMILIES = frozenset({"nfp", "cpi", "fomc", "pce", "gdp", "tier1"})

logger = logging.getLogger(__name__)


def trailing_business_days(year: int, month: int, n: int = MONTH_END_DAYS) -> set[date]:
    """Last n business days of a calendar month."""
    first_day = pd.Timestamp(year=year, month=month, day=1)
    last_calendar_day = first_day + pd.offsets.MonthEnd(0)
    business_days = pd.bdate_range(first_day, last_calendar_day)
    if business_days.empty:
        return set()
    return {pd.Timestamp(day).date() for day in business_days[-n:]}


def is_calendar_month_end(day: date, month_end_days: int = MONTH_END_DAYS) -> bool:
    """True if day is one of the last n business days of its calendar month."""
    return day in trailing_business_days(day.year, day.month, month_end_days)


def describe_calendar_day(
    day: date,
    *,
    events: list[dict[str, Any]] | None = None,
    db_path: Path = EVENTS_DB,
    month_end_days: int = MONTH_END_DAYS,
) -> dict[str, Any]:
    """Tag a calendar date from the events DB even when that day has no flow yet."""
    hits = list(events) if events is not None else load_tagged_events(start=day, end=day, db_path=db_path)
    families = {event["family"] for event in hits}
    is_month_end = is_calendar_month_end(day, month_end_days)
    if is_month_end:
        families.add("month_end")
    is_auction = "auction" in families
    is_tier1 = bool(families & TIER1_FAMILIES)
    if is_tier1:
        families.add("tier1")
    labels = [
        FAMILY_LABELS[name]
        for name in ("auction", "month_end", "tier1", "nfp", "cpi", "fomc", "pce", "gdp")
        if name in families
    ]
    weekday = day.strftime("%a")
    analog_keys = [name for name in ("auction", "month_end", "tier1") if name in families]
    auction_names = [event["event_name"] for event in hits if event.get("family") == "auction"]
    tier1_names = [event["event_name"] for event in hits if event.get("family") in TIER1_FAMILIES]
    considered = [
        {"key": "auction", "label": "AUCTION/SUPPLY", "on": is_auction, "detail": ", ".join(auction_names)},
        {"key": "month_end", "label": "MONTH-END", "on": is_month_end, "detail": "last 3 session days" if is_month_end else ""},
        {"key": "tier1", "label": "TIER-1", "on": is_tier1, "detail": ", ".join(tier1_names)},
        {"key": "weekday", "label": weekday.upper(), "on": True, "detail": "session weekday"},
    ]
    return {
        "date": day.isoformat(),
        "weekday": weekday,
        "is_auction": is_auction,
        "is_month_end": is_month_end,
        "is_tier1": is_tier1,
        "tags": labels,
        "analog_keys": analog_keys,
        "considered": considered,
        "events": hits,
    }


def tag_month_end_days(dates: list[date], month_end_days: int = MONTH_END_DAYS) -> set[date]:
    """Tag trailing business days only when that month is complete in the sample."""
    if month_end_days <= 0:
        return set()
    observed = sorted({pd.Timestamp(day).date() for day in dates if day is not None})
    if not observed:
        return set()
    by_month: dict[tuple[int, int], list[date]] = {}
    for day in observed:
        by_month.setdefault((day.year, day.month), []).append(day)
    tagged: set[date] = set()
    for (year, month), month_days in by_month.items():
        trailing = trailing_business_days(year, month, month_end_days)
        if not trailing:
            continue
        last_required = max(trailing)
        if max(month_days) < last_required:
            continue
        tagged.update(day for day in month_days if day in trailing)
    return tagged


def _parse_event_dt(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip().replace("Z", "")
    if not raw:
        return None
    for candidate in (raw, raw.replace("T", " ")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _is_us(country: str) -> bool:
    text = str(country or "").strip()
    if not text:
        return True
    return "United States" in text or text.upper() in {"US", "USA"}


def _is_coupon_auction(name: str) -> bool:
    title = str(name or "").strip()
    if not title or _TIPS_FRN_BILL_RE.search(title):
        return False
    if title in AUCTION_NAME_SET:
        return True
    return bool(_COUPON_ALIAS_RE.match(title))


def classify_event_name(name: str) -> str | None:
    """Map a calendar title to auction / nfp / cpi / fomc / pce / gdp."""
    title = str(name or "").strip()
    if not title:
        return None
    if _is_coupon_auction(title):
        return "auction"
    parent_family = TIER1_PARENTS.get(title)
    if parent_family:
        return parent_family
    lower = title.lower()
    if title == "NFP" or title.startswith("NFP ") or "nonfarm payroll" in lower or "non farm payroll" in lower:
        return "nfp"
    if title in {"CPI", "CPI MoM", "Core CPI MoM"}:
        return "cpi"
    if title.startswith("CPI ") and "Regional" not in title:
        return "cpi"
    if title in {"FOMC", "Fed Press Conference", "FOMC Rate Decision"} or title.startswith("FOMC Rate Decision"):
        return "fomc"
    if "pce" in lower:
        return "pce"
    if "gdp" in lower:
        return "gdp"
    return None


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def _row_to_event(day: date, family: str, name: str, stamp: datetime) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "family": family,
        "label": FAMILY_LABELS.get(family, family.upper()),
        "event_name": name,
        "ts_et": stamp.isoformat(sep=" "),
    }


def _load_yaman_events(
    conn: sqlite3.Connection,
    *,
    start: date | None,
    end: date | None,
) -> list[dict[str, Any]]:
    parents = list(COUPON_SUPPLY_PARENTS) + list(TIER1_PARENTS)
    placeholders = ", ".join("?" for _ in parents)
    sql = f"""
        SELECT PARENT_EVENT, EVENT_NAME, COUNTRY_NAME, RELEASE_DATE, RELEASE_DATE_TIME
        FROM events
        WHERE RELEASE_DATE IS NOT NULL
          AND PARENT_EVENT IN ({placeholders})
        ORDER BY RELEASE_DATE ASC
    """
    rows = conn.execute(sql, parents).fetchall()
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        if not _is_us(row["COUNTRY_NAME"]):
            continue
        stamp = _parse_event_dt(row["RELEASE_DATE_TIME"]) or _parse_event_dt(row["RELEASE_DATE"])
        if stamp is None:
            continue
        day = stamp.date()
        if start is not None and day < start:
            continue
        if end is not None and day > end:
            continue
        parent = str(row["PARENT_EVENT"] or "").strip()
        family = TIER1_PARENTS.get(parent) or ("auction" if parent in COUPON_SUPPLY_PARENTS else None)
        if family is None:
            continue
        name = parent or str(row["EVENT_NAME"] or "")
        key = (day.isoformat(), family, name)
        if key in seen:
            continue
        seen.add(key)
        out.append(_row_to_event(day, family, name, stamp))
    return out


def _load_legacy_events(
    conn: sqlite3.Connection,
    *,
    start: date | None,
    end: date | None,
) -> list[dict[str, Any]]:
    names = list(AUCTION_NAMES)
    for family_names in MACRO_FAMILIES.values():
        names.extend(family_names)
    placeholders = ", ".join("?" for _ in names)
    sql = f"""
        SELECT event_name, event_date, country
        FROM economic_events
        WHERE event_date IS NOT NULL
          AND event_date <> ''
          AND (
            event_name IN ({placeholders})
            OR event_name LIKE 'NFP %'
            OR event_name LIKE 'CPI %'
          )
        ORDER BY event_date ASC
    """
    rows = conn.execute(sql, names).fetchall()
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        if not _is_us(row["country"]):
            continue
        stamp = _parse_event_dt(row["event_date"])
        if stamp is None:
            continue
        day = stamp.date()
        if start is not None and day < start:
            continue
        if end is not None and day > end:
            continue
        name = str(row["event_name"])
        family = classify_event_name(name)
        if family is None:
            continue
        key = (day.isoformat(), family, name)
        if key in seen:
            continue
        seen.add(key)
        out.append(_row_to_event(day, family, name, stamp))
    return out


def load_tagged_events(
    *,
    start: date | None = None,
    end: date | None = None,
    db_path: Path = EVENTS_DB,
) -> list[dict[str, Any]]:
    """Load US coupon-supply and tier-1 rows. Empty list if the DB is missing."""
    if not db_path.exists():
        logger.warning("events db missing at %s", db_path)
        return []
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            tables = _table_names(conn)
            if "events" in tables:
                return _load_yaman_events(conn, start=start, end=end)
            if "economic_events" in tables:
                return _load_legacy_events(conn, start=start, end=end)
    except Exception:
        logger.exception("Failed to read events db")
        return []
    logger.warning("events db has neither events nor economic_events: %s", db_path)
    return []


def attach_event_tags(
    daily_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    month_end_days: int = MONTH_END_DAYS,
) -> list[dict[str, Any]]:
    """Add family flags and display tags to daily rows."""
    dates = [date.fromisoformat(str(row["date"])) for row in daily_rows if row.get("date")]
    month_end = tag_month_end_days(dates, month_end_days)
    by_date: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_date.setdefault(event["date"], []).append(event)
    tagged: list[dict[str, Any]] = []
    for row in daily_rows:
        day = date.fromisoformat(str(row["date"]))
        hits = list(by_date.get(row["date"], []))
        families = {event["family"] for event in hits}
        is_month_end = day in month_end
        if is_month_end:
            families.add("month_end")
        is_auction = "auction" in families
        is_tier1 = bool(families & TIER1_FAMILIES)
        if is_tier1:
            families.add("tier1")
        weekday = day.strftime("%a")
        labels = [
            FAMILY_LABELS[name]
            for name in ("auction", "month_end", "tier1", "nfp", "cpi", "fomc", "pce", "gdp")
            if name in families
        ]
        tagged.append({
            **row,
            "weekday": weekday,
            "is_month_end": is_month_end,
            "is_auction": is_auction,
            "is_supply": is_auction,
            "is_auction_51030": any(event.get("event_name") in AUCTION_51030_NAMES for event in hits),
            "is_tier1": is_tier1,
            "is_nfp": "nfp" in families,
            "is_cpi": "cpi" in families,
            "is_fomc": "fomc" in families,
            "tags": labels,
            "events": hits,
        })
    return tagged
