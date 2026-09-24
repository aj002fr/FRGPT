"""Matched 2Y/5Y/7Y weeks and house auction-clock windows."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from scripts.supply_weeks_dashboard import build_auction_weeks, fetch_market_data_for_week

EASTERN = ZoneInfo("America/New_York")
WINDOW_NAMES = ("pre_2y", "2y_to_5y", "5y_to_7y", "post_7y")
DISTRO_WINDOWS = WINDOW_NAMES + ("full", "auction_day_2y", "auction_day_5y", "auction_day_7y")
WEEK_END_HOURS = 119.0
BPS_PER_POINT = 8.0


def week_start_date(week_start: str) -> date:
    return datetime.strptime(str(week_start)[:10], "%Y-%m-%d").date()


def week_open_et(week_start: str | date) -> datetime:
    monday = week_start if isinstance(week_start, date) else week_start_date(week_start)
    return datetime.combine(monday - timedelta(days=1), time(18, 0), tzinfo=EASTERN)


def week_close_et(week_start: str | date) -> datetime:
    monday = week_start if isinstance(week_start, date) else week_start_date(week_start)
    return datetime.combine(monday + timedelta(days=4), time(17, 0), tzinfo=EASTERN)


def hours_from_week_open(stamp: datetime, week_start: str | date) -> float:
    clock = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=EASTERN)
    return (clock.astimezone(EASTERN) - week_open_et(week_start)).total_seconds() / 3600.0


def parse_auction_et(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw[:19] if len(raw) >= 19 else raw, fmt).replace(tzinfo=EASTERN)
        except ValueError:
            continue
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=EASTERN)
    except ValueError:
        return None


def auction_hours(info: dict[str, Any]) -> dict[str, float]:
    """Hours from Sunday 18:00 ET. Matches the static dashboard (6 + dayOffset*24 + clock)."""
    monday = week_start_date(info["week_start"])
    out: dict[str, float] = {}
    defaults = {"2y": 41.5, "5y": 61.0, "7y": 85.0}
    for tenor in ("2y", "5y", "7y"):
        stamp = parse_auction_et(str(info.get(f"{tenor}_date") or ""))
        if stamp is None:
            out[tenor] = defaults[tenor]
            continue
        day_offset = (stamp.date() - monday).days
        out[tenor] = 6.0 + day_offset * 24.0 + stamp.hour + stamp.minute / 60.0
    return out


def window_bounds(info: dict[str, Any], window: str) -> tuple[float, float]:
    auc = auction_hours(info)
    auc2, auc5, auc7 = auc["2y"], auc["5y"], auc["7y"]
    mapping = {
        "full": (0.0, WEEK_END_HOURS),
        "pre_2y": (0.0, auc2),
        "2y_to_5y": (auc2, auc5),
        "5y_to_7y": (auc5, auc7),
        "post_7y": (auc7, WEEK_END_HOURS),
        "auction_day_2y": (auc2 - 6.0, auc2 + 6.0),
        "auction_day_5y": (auc5 - 6.0, auc5 + 6.0),
        "auction_day_7y": (auc7 - 6.0, auc7 + 6.0),
    }
    return mapping.get(window, (0.0, WEEK_END_HOURS))


def current_window_name(info: dict[str, Any], now: datetime | None = None) -> str | None:
    clock = now.astimezone(EASTERN) if now is not None else datetime.now(EASTERN)
    hours = hours_from_week_open(clock, info["week_start"])
    if hours < 0 or hours >= WEEK_END_HOURS:
        return None
    for name in WINDOW_NAMES:
        start, end = window_bounds(info, name)
        if start <= hours < end:
            return name
    return "post_7y"


def is_current_week(info: dict[str, Any], now: datetime | None = None) -> bool:
    clock = now.astimezone(EASTERN) if now is not None else datetime.now(EASTERN)
    return week_open_et(info["week_start"]) <= clock < week_close_et(info["week_start"])


def list_matched_weeks(*, start_year: int = 2015, min_size: float = 40_000_000_000) -> list[dict[str, Any]]:
    return build_auction_weeks(start_year=start_year, min_size=min_size)


def attach_price(week: dict[str, Any], conn) -> dict[str, Any]:
    info = week["week_info"]
    market = fetch_market_data_for_week(info["week_start"], conn)
    week["price_data"] = market.get("price_data") or {"hours": [], "closes": []}
    return week


def window_price_bps(price_data: dict[str, Any], start_h: float, end_h: float) -> float | None:
    hours = price_data.get("hours") or []
    closes = price_data.get("closes") or []
    picked = [float(closes[i]) for i, hour in enumerate(hours) if start_h <= float(hour) < end_h and i < len(closes)]
    if len(picked) < 2:
        return None
    return (picked[-1] - picked[0]) * BPS_PER_POINT
