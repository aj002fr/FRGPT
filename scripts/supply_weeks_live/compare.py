"""Auction vs same-weekday non-supply slices. Read-only on flow_history."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.aggression_dashboard.flow_history import DB_PATH
from scripts.aggression_flow_history_dash.events import (
    TIER1_FAMILIES,
    is_calendar_month_end,
    load_tagged_events,
)
from scripts.aggression_flow_history_dash.session import to_eastern
from scripts.supply_weeks_dashboard import AUCTION_TIMES, TENORS, is_month_end_supply_week, load_auction_dates
from scripts.supply_weeks_live.flow import _median, _quantile, _read_bars, fit_window_ols
from scripts.supply_weeks_live.weeks import BPS_PER_POINT, is_current_week, parse_auction_et

EASTERN = ZoneInfo("America/New_York")
FLOW_START_YEAR = 2022
SLICE_NAMES = ("into", "after")
INTO_HOURS = 3
CLOSE_TIME = time(17, 0)
# 2Y is mostly 13:00 ET, including when the note prints the next day. The into window is 10:00–13:00.
COMPARE_AUCTION_TIME = {"2Y": time(13, 0)}
DEFAULT_WEEKDAYS = {"2Y": 1, "5Y": 2, "7Y": 3}
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def comparison_stamp(tenor: str, auction_et: datetime) -> datetime:
    """Auction clock used for into/after slices. 2Y is fixed at 13:00 ET."""
    stamp = auction_et if auction_et.tzinfo is not None else auction_et.replace(tzinfo=EASTERN)
    stamp = stamp.astimezone(EASTERN)
    override = COMPARE_AUCTION_TIME.get(tenor)
    if override is None:
        return stamp
    return datetime.combine(stamp.date(), override, tzinfo=EASTERN)


def slice_bounds(auction_et: datetime, *, tenor: str | None = None) -> dict[str, tuple[datetime, datetime]]:
    """Into auction [t−3h, t); auction to 17:00 ET [t, 17:00). 2Y also has 08:00 to t."""
    stamp = auction_et if auction_et.tzinfo is not None else auction_et.replace(tzinfo=EASTERN)
    stamp = stamp.astimezone(EASTERN)
    close = datetime.combine(stamp.date(), CLOSE_TIME, tzinfo=EASTERN)
    bounds = {
        "into": (stamp - timedelta(hours=INTO_HOURS), stamp),
        "after": (stamp, close),
    }
    if tenor == "2Y":
        open_at = datetime.combine(stamp.date(), time(8, 0), tzinfo=EASTERN)
        bounds = {"from_8": (open_at, stamp), **bounds}
    return bounds


def all_auction_dates(*, start_year: int = 2000) -> set[date]:
    """Every 2Y/5Y/7Y auction calendar date in the CSVs, including unmatched weeks."""
    dates: set[date] = set()
    for tenor in TENORS:
        for row in load_auction_dates(tenor, start_year=start_year):
            dates.add(row["date"].date())
    return dates


def supply_events(weeks: list[dict[str, Any]], *, start_year: int = FLOW_START_YEAR) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for week in weeks:
        info = week.get("week_info") or week
        for tenor in TENORS:
            stamp = parse_auction_et(str(info.get(f"{tenor.lower()}_date") or ""))
            if stamp is None or stamp.year < start_year:
                continue
            events.append(
                {
                    "tenor": tenor,
                    "date": stamp.date(),
                    "t": stamp,
                    "weekday": stamp.weekday(),
                    "is_month_end": bool(info.get("is_month_end")),
                    "week_key": week.get("week_key"),
                }
            )
    return events


def modal_clocks(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for tenor in TENORS:
        subset = [row for row in events if row["tenor"] == tenor]
        if not subset:
            override = COMPARE_AUCTION_TIME.get(tenor)
            if override is not None:
                hh, mm = override.hour, override.minute
            else:
                hh, mm = [int(part) for part in AUCTION_TIMES[tenor].split(":")]
            out[tenor] = {"weekday": DEFAULT_WEEKDAYS[tenor], "time": time(hh, mm)}
            continue
        weekday = Counter(int(row["weekday"]) for row in subset).most_common(1)[0][0]
        clock = Counter((row["t"].hour, row["t"].minute) for row in subset).most_common(1)[0][0]
        override = COMPARE_AUCTION_TIME.get(tenor)
        if override is not None:
            clock = (override.hour, override.minute)
        out[tenor] = {"weekday": weekday, "time": time(clock[0], clock[1])}
    return out


def fold_slice(bars: list[dict[str, Any]], start: datetime, end: datetime) -> dict[str, Any]:
    buy = sell = 0.0
    n_bars = 0
    first_px = None
    last_px = None
    for bar in bars:
        stamp = to_eastern(bar.get("ts_et") or bar.get("ts_utc"))
        if stamp is None or stamp < start or stamp >= end:
            continue
        buy += float(bar.get("buy_flow") or 0.0)
        sell += float(bar.get("sell_flow") or 0.0)
        n_bars += 1
        price = bar.get("trade_price")
        if price is None:
            continue
        px = float(price)
        if first_px is None:
            first_px = px
        last_px = px
    window_bps = None
    if first_px is not None and last_px is not None:
        window_bps = (last_px - first_px) * BPS_PER_POINT
    return {
        "buy_flow": buy,
        "sell_flow": sell,
        "net_flow": buy - sell,
        "window_bps": window_bps,
        "n_bars": n_bars,
    }


def _clock_meta(modal: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tenor in TENORS:
        clock = modal[tenor]
        auction = datetime.combine(date(2026, 1, 6), clock["time"], tzinfo=EASTERN)
        bounds = slice_bounds(auction, tenor=tenor)
        meta = {
            "weekday": WEEKDAY_NAMES[int(clock["weekday"])],
            "time": clock["time"].strftime("%H:%M"),
            "into": f"{bounds['into'][0].strftime('%H:%M')}-{bounds['into'][1].strftime('%H:%M')}",
            "after": f"{bounds['after'][0].strftime('%H:%M')}-{bounds['after'][1].strftime('%H:%M')}",
        }
        if "from_8" in bounds:
            meta["from_8"] = f"{bounds['from_8'][0].strftime('%H:%M')}-{bounds['from_8'][1].strftime('%H:%M')}"
        out[tenor] = meta
    return out


def calendar_flags(days: set[date], events: list[dict[str, Any]] | None = None) -> dict[date, dict[str, bool]]:
    """Tier-1 (NFP/CPI/FOMC/PCE/GDP) and last-3-session month-end, same as the 5064 tags."""
    if events is None and days:
        events = load_tagged_events(start=min(days), end=max(days))
    tier1: set[date] = set()
    for event in events or []:
        if event.get("family") not in TIER1_FAMILIES:
            continue
        raw = event.get("date")
        if isinstance(raw, date):
            tier1.add(raw)
            continue
        text = str(raw or "")[:10]
        if text:
            tier1.add(date.fromisoformat(text))
    return {
        day: {
            "is_tier1": day in tier1,
            "is_calendar_month_end": is_calendar_month_end(day),
        }
        for day in days
    }


def _row(
    *,
    tenor: str,
    slice_name: str,
    side: str,
    day: date,
    start_et: datetime,
    end_et: datetime,
    folded: dict[str, Any],
    is_month_end: bool,
    is_tier1: bool = False,
    is_calendar_month_end: bool = False,
) -> dict[str, Any]:
    return {
        "tenor": tenor,
        "slice": slice_name,
        "side": side,
        "date": day.isoformat(),
        "start_et": start_et.isoformat(),
        "end_et": end_et.isoformat(),
        "is_month_end": bool(is_month_end),
        "is_tier1": bool(is_tier1),
        "is_calendar_month_end": bool(is_calendar_month_end),
        **folded,
    }


def _group_by_date(bars: list[dict[str, Any]]) -> dict[date, list[dict[str, Any]]]:
    by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for bar in bars:
        stamp = to_eastern(bar.get("ts_et") or bar.get("ts_utc"))
        if stamp is None:
            continue
        by_date[stamp.date()].append(bar)
    return by_date


def build_compare_rows(
    weeks: list[dict[str, Any]],
    *,
    db_path: Path | None = None,
    now: datetime | None = None,
    auction_dates: set[date] | None = None,
    only_dates: set[date] | None = None,
    modal: dict[str, dict[str, Any]] | None = None,
    day_flags: dict[date, dict[str, bool]] | None = None,
    symbol: str = "TY",
) -> list[dict[str, Any]]:
    clock = now.astimezone(EASTERN) if now is not None else datetime.now(EASTERN)
    events = supply_events(weeks)
    clocks = modal if modal is not None else modal_clocks(events)
    blocked = set(auction_dates) if auction_dates is not None else set(all_auction_dates())
    blocked.update(event["date"] for event in events)
    path = Path(db_path) if db_path is not None else DB_PATH
    if only_dates:
        start = datetime.combine(min(only_dates), time(0, 0), tzinfo=EASTERN)
        end = datetime.combine(max(only_dates) + timedelta(days=1), time(0, 0), tzinfo=EASTERN)
    else:
        start = datetime(FLOW_START_YEAR, 1, 1, tzinfo=EASTERN)
        end = clock + timedelta(days=1)
    by_date = _group_by_date(_read_bars(symbol, start, end, path))
    rows: list[dict[str, Any]] = []

    for event in events:
        day = event["date"]
        if only_dates is not None and day not in only_dates:
            continue
        bars = by_date.get(day) or []
        for slice_name, (lo, hi) in slice_bounds(comparison_stamp(event["tenor"], event["t"]), tenor=event["tenor"]).items():
            folded = fold_slice(bars, lo, hi)
            rows.append(
                _row(
                    tenor=event["tenor"],
                    slice_name=slice_name,
                    side="supply",
                    day=day,
                    start_et=lo,
                    end_et=hi,
                    folded=folded,
                    is_month_end=event["is_month_end"],
                )
            )

    for day, bars in by_date.items():
        if only_dates is not None and day not in only_dates:
            continue
        if day.year < FLOW_START_YEAR or day.weekday() >= 5 or day in blocked:
            continue
        flags = (day_flags or {}).get(day) or {}
        for tenor in TENORS:
            spec = clocks[tenor]
            if day.weekday() != int(spec["weekday"]):
                continue
            auction = datetime.combine(day, spec["time"], tzinfo=EASTERN)
            for slice_name, (lo, hi) in slice_bounds(auction, tenor=tenor).items():
                folded = fold_slice(bars, lo, hi)
                rows.append(
                    _row(
                        tenor=tenor,
                        slice_name=slice_name,
                        side="control",
                        day=day,
                        start_et=lo,
                        end_et=hi,
                        folded=folded,
                        is_month_end=False,
                        is_tier1=bool(flags.get("is_tier1")),
                        is_calendar_month_end=bool(flags.get("is_calendar_month_end")),
                    )
                )
    if day_flags is None:
        control_days = {date.fromisoformat(row["date"]) for row in rows if row["side"] == "control"}
        flags_by_day = calendar_flags(control_days)
        for row in rows:
            if row["side"] != "control":
                continue
            flags = flags_by_day.get(date.fromisoformat(row["date"])) or {}
            row["is_tier1"] = bool(flags.get("is_tier1"))
            row["is_calendar_month_end"] = bool(flags.get("is_calendar_month_end"))
    return rows


def refresh_today_rows(
    rows: list[dict[str, Any]],
    weeks: list[dict[str, Any]],
    *,
    db_path: Path | None = None,
    now: datetime | None = None,
    auction_dates: set[date] | None = None,
    modal: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    clock = now.astimezone(EASTERN) if now is not None else datetime.now(EASTERN)
    today = clock.date()
    kept = [row for row in rows if row.get("date") != today.isoformat()]
    fresh = build_compare_rows(
        weeks,
        db_path=db_path,
        now=clock,
        auction_dates=auction_dates,
        only_dates={today},
        modal=modal,
    )
    return kept + fresh


def cme_week_monday(clock: datetime) -> date | None:
    """Monday of the Globex week that contains clock. None from Fri 17:00 through Sun 18:00."""
    stamp = clock.astimezone(EASTERN)
    weekday = stamp.weekday()
    if weekday == 6:
        if stamp.hour < 18:
            return None
        return stamp.date() + timedelta(days=1)
    if weekday == 5 or (weekday == 4 and (stamp.hour > 17 or (stamp.hour == 17 and stamp.minute > 0))):
        return None
    return stamp.date() - timedelta(days=weekday)


def _tenor_name(name: str) -> str | None:
    compact = str(name or "").upper().replace("-", "").replace(" ", "")
    if compact.startswith("2Y") or compact.startswith("2YEAR"):
        return "2Y"
    if compact.startswith("5Y") or compact.startswith("5YEAR"):
        return "5Y"
    if compact.startswith("7Y") or compact.startswith("7YEAR"):
        return "7Y"
    return None


def event_week_from_hits(hits: list[dict[str, Any]], monday: date) -> dict[str, Any] | None:
    """Build one matched week when the calendar has 2Y, 5Y, and 7Y and the CSV does not yet."""
    found: dict[str, datetime] = {}
    for hit in hits:
        if hit.get("family") != "auction":
            continue
        tenor = _tenor_name(str(hit.get("event_name") or ""))
        if tenor is None or tenor in found:
            continue
        day = date.fromisoformat(str(hit.get("date") or "")[:10])
        if day < monday or day > monday + timedelta(days=4):
            continue
        hour, minute = [int(part) for part in AUCTION_TIMES[tenor].split(":")]
        found[tenor] = datetime(day.year, day.month, day.day, hour, minute)
    if set(found) != set(TENORS):
        return None
    seven = found["7Y"]
    iso = monday.isocalendar()
    info: dict[str, Any] = {
        "year": seven.year,
        "month": seven.month,
        "month_name": seven.strftime("%b"),
        "week_start": monday.isoformat(),
        "is_january": seven.month == 1,
        "is_month_end": is_month_end_supply_week(seven),
        "is_consecutive": True,
    }
    for tenor, stamp in found.items():
        key = tenor.lower()
        info[f"{key}_date"] = stamp.strftime("%Y-%m-%d %H:%M")
        info[f"{key}_day"] = stamp.strftime("%a")
    return {
        "week_key": f"{iso.year}-W{iso.week:02d}",
        "week_info": info,
        "price_data": {"hours": [], "closes": []},
    }


def append_live_event_week(
    weeks: list[dict[str, Any]],
    now: datetime | None = None,
    hits: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    clock = now.astimezone(EASTERN) if now is not None else datetime.now(EASTERN)
    if any(is_current_week(week.get("week_info") or {}, clock) for week in weeks):
        return weeks
    monday = cme_week_monday(clock)
    if monday is None:
        return weeks
    if hits is None:
        hits = load_tagged_events(start=monday, end=monday + timedelta(days=4))
    extra = event_week_from_hits(hits, monday)
    if extra is None:
        return weeks
    if any((week.get("week_info") or {}).get("week_start") == extra["week_info"]["week_start"] for week in weeks):
        return weeks
    return weeks + [extra]


def live_slices(
    rows: list[dict[str, Any]],
    weeks: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Current matched week's six clocks, including a window that is still open."""
    clock = now.astimezone(EASTERN)
    current = next((week for week in weeks if is_current_week(week.get("week_info") or {}, clock)), None)
    if current is None:
        return {"week_key": None, "slices": {}}
    indexed = {
        (row.get("tenor"), row.get("slice"), row.get("date")): row
        for row in rows
        if row.get("side") == "supply"
    }
    slices: dict[str, Any] = {}
    for event in supply_events([current], start_year=2000):
        tenor = event["tenor"]
        slices.setdefault(tenor, {})
        for slice_name, (start_et, end_et) in slice_bounds(comparison_stamp(tenor, event["t"]), tenor=tenor).items():
            row = indexed.get((tenor, slice_name, event["date"].isoformat())) or {}
            if clock < start_et:
                status = "upcoming"
            elif clock < end_et:
                status = "open"
            else:
                status = "done"
            slices[tenor][slice_name] = {
                "status": status,
                "start_et": start_et.isoformat(),
                "end_et": end_et.isoformat(),
                "buy_flow": row.get("buy_flow"),
                "sell_flow": row.get("sell_flow"),
                "net_flow": row.get("net_flow"),
                "window_bps": row.get("window_bps"),
                "n_bars": int(row.get("n_bars") or 0),
            }
    return {"week_key": current.get("week_key"), "slices": slices}


def _band(values: list[float], key: str) -> dict[str, float | None]:
    return {
        f"p25_{key}": _quantile(values, 0.25),
        f"median_{key}": _median(values),
        f"p75_{key}": _quantile(values, 0.75),
    }


def _stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    buys = [float(row.get("buy_flow") or 0.0) for row in samples]
    sells = [float(row.get("sell_flow") or 0.0) for row in samples]
    nets = [float(row.get("net_flow") or 0.0) for row in samples]
    bps = [float(row["window_bps"]) for row in samples if row.get("window_bps") is not None]
    out: dict[str, Any] = {"n": len(samples)}
    out.update(_band(buys, "buy"))
    out.update(_band(sells, "sell"))
    out.update(_band(nets, "net"))
    out.update(_band(bps, "bps"))
    return out


def _flow_implied(samples: list[dict[str, Any]], net: float | None) -> dict[str, float | None]:
    """Normal-day line: bps = intercept + slope × net. Slope is bps per $1mm."""
    fit = fit_window_ols(samples)
    slope = fit.get("slope_bps_per_mm")
    intercept = fit.get("intercept_bps")
    implied = None
    if slope is not None and intercept is not None and net is not None:
        implied = float(intercept) + float(slope) * float(net) / 1e6
    return {
        "slope_bps_per_mm": slope,
        "intercept_bps": intercept,
        "flow_implied_bps": implied,
    }


def summarize_compare(
    rows: list[dict[str, Any]],
    *,
    month_end: str = "all",
    drop_tier1: bool = False,
    drop_month_end: bool = False,
    weeks: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    modal: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clock = now.astimezone(EASTERN) if now is not None else datetime.now(EASTERN)
    clocks = modal if modal else modal_clocks([])
    usable: list[dict[str, Any]] = []
    for row in rows:
        if int(row.get("n_bars") or 0) <= 0:
            continue
        end_et = to_eastern(row.get("end_et"))
        if end_et is not None and clock < end_et:
            continue
        if row.get("side") == "supply":
            if month_end == "month_end" and not row.get("is_month_end"):
                continue
            if month_end == "non_month_end" and row.get("is_month_end"):
                continue
        if row.get("side") == "control":
            if drop_tier1 and row.get("is_tier1"):
                continue
            if drop_month_end and row.get("is_calendar_month_end"):
                continue
        usable.append(row)

    out: dict[str, Any] = {
        "clocks": _clock_meta(clocks),
        "rows": {},
        "live": live_slices(rows, weeks or [], clock),
    }
    for tenor in TENORS:
        out["rows"][tenor] = {}
        names = ("from_8", "into", "after") if tenor == "2Y" else SLICE_NAMES
        for slice_name in names:
            supply = [row for row in usable if row["tenor"] == tenor and row["slice"] == slice_name and row["side"] == "supply"]
            control = [row for row in usable if row["tenor"] == tenor and row["slice"] == slice_name and row["side"] == "control"]
            supply_stats = _stats(supply)
            control_stats = _stats(control)
            out["rows"][tenor][slice_name] = {
                "supply": supply_stats,
                "control": control_stats,
                **_flow_implied(control, supply_stats.get("median_net")),
            }
    return out
