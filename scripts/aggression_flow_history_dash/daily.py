"""ET-date aggregation and bid/ask vs rally diagnostics. Read-only."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.aggression_dashboard.flow_history import query_flow_history
from scripts.aggression_flow_history_dash.events import (
    attach_event_tags,
    describe_calendar_day,
    load_tagged_events,
)
from scripts.aggression_flow_history_dash.session import (
    current_session_day,
    market_phase,
    next_session_day,
    session_bounds,
    session_day,
    to_eastern,
)

EASTERN = ZoneInfo("America/New_York")
TICK_SIZE = 1.0 / 64.0
BPS_PER_TICK = 0.25  # house: 4 ticks (1/64) = 1 bp
MIN_ABS_NET_DV01 = 250_000.0
MIN_ANALOG_DAYS = 3
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
COHORTS = ("all", "auction", "month_end", "tier1", "nfp", "cpi", "fomc") + WEEKDAYS
SLOPE_COHORTS = ("all", "auction", "month_end", "tier1") + WEEKDAYS
COHORT_FLAGS = {
    "auction": "is_auction",
    "month_end": "is_month_end",
    "tier1": "is_tier1",
    "nfp": "is_nfp",
    "cpi": "is_cpi",
    "fomc": "is_fomc",
}


def et_day_bounds(day: date) -> tuple[datetime, datetime]:
    start, end = session_bounds(day)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _bar_session_day(ts_et: Any, ts_utc: Any) -> date | None:
    return session_day(to_eastern(ts_et) or to_eastern(ts_utc))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def aggregate_daily(
    symbol: str = "TY",
    *,
    start: date | None = None,
    end: date | None = None,
    db_path=None,
    events_db_path=None,
) -> list[dict[str, Any]]:
    """Sum 1-minute bid/ask volume to Globex session days and attach event tags."""
    kwargs: dict[str, Any] = {"symbol": symbol}
    if start is not None:
        kwargs["start"] = session_bounds(start)[0]
    if end is not None:
        kwargs["end"] = session_bounds(end)[1]
    if db_path is not None:
        kwargs["db_path"] = db_path
    rows = query_flow_history(**kwargs)
    if not rows:
        return []
    buckets: dict[date, dict[str, Any]] = {}
    for row in rows:
        day = _bar_session_day(row.get("ts_et"), row.get("ts_utc"))
        if day is None:
            continue
        if start is not None and day < start:
            continue
        if end is not None and day > end:
            continue
        bucket = buckets.setdefault(day, {
            "date": day.isoformat(),
            "buy_contracts": 0.0,
            "sell_contracts": 0.0,
            "buy_flow": 0.0,
            "sell_flow": 0.0,
            "trade_count": 0,
            "open_price": None,
            "close_price": None,
        })
        bucket["buy_contracts"] += float(row.get("buy_contracts") or 0.0)
        bucket["sell_contracts"] += float(row.get("sell_contracts") or 0.0)
        bucket["buy_flow"] += float(row.get("buy_flow") or 0.0)
        bucket["sell_flow"] += float(row.get("sell_flow") or 0.0)
        bucket["trade_count"] += int(row.get("trade_count") or 0)
        price = _finite(row.get("trade_price"))
        if price is not None:
            if bucket["open_price"] is None:
                bucket["open_price"] = price
            bucket["close_price"] = price
    daily: list[dict[str, Any]] = []
    for day in sorted(buckets):
        bucket = buckets[day]
        buy = bucket["buy_contracts"]
        sell = bucket["sell_contracts"]
        open_px = bucket["open_price"]
        close_px = bucket["close_price"]
        day_ticks = None
        if open_px is not None and close_px is not None:
            day_ticks = (close_px - open_px) / TICK_SIZE
        daily.append({
            **bucket,
            "net_contracts": buy - sell,
            "bid_dominant": buy > sell,
            "ask_dominant": sell > buy,
            "day_ticks": day_ticks,
            "day_bps": None if day_ticks is None else day_ticks * BPS_PER_TICK,
            "rallied": None if day_ticks is None else day_ticks > 0,
        })
    event_kwargs: dict[str, Any] = {"start": start, "end": end}
    if events_db_path is not None:
        event_kwargs["db_path"] = events_db_path
    events = load_tagged_events(**event_kwargs)
    tagged = attach_event_tags(daily, events)
    for row in tagged:
        row["bps_per_mm"] = day_bps_per_mm(row)
    return tagged


def load_intraday(
    symbol: str,
    day: date,
    *,
    db_path=None,
    events_db_path=None,
) -> dict[str, Any]:
    """One Globex session of 1-minute bid/ask volume plus event markers."""
    start, end = et_day_bounds(day)
    kwargs: dict[str, Any] = {"symbol": symbol, "start": start, "end": end}
    if db_path is not None:
        kwargs["db_path"] = db_path
    rows = query_flow_history(**kwargs)
    event_kwargs: dict[str, Any] = {"start": day, "end": day}
    if events_db_path is not None:
        event_kwargs["db_path"] = events_db_path
    events = load_tagged_events(**event_kwargs)
    return {
        "date": day.isoformat(),
        "symbol": symbol,
        "rows": [
            {
                "ts_utc": row.get("ts_utc"),
                "ts_et": row.get("ts_et"),
                "buy_contracts": float(row.get("buy_contracts") or 0.0),
                "sell_contracts": float(row.get("sell_contracts") or 0.0),
                "buy_flow": float(row.get("buy_flow") or 0.0),
                "sell_flow": float(row.get("sell_flow") or 0.0),
                "trade_price": _finite(row.get("trade_price")),
            }
            for row in rows
        ],
        "events": events,
    }


def _cohort_rows(rows: list[dict[str, Any]], cohort: str) -> list[dict[str, Any]]:
    if cohort == "all":
        return rows
    if cohort in WEEKDAYS:
        return [row for row in rows if row.get("weekday") == cohort]
    flag = COHORT_FLAGS.get(cohort)
    if flag is None:
        return []
    return [row for row in rows if row.get(flag)]


def day_bps_per_mm(row: dict[str, Any]) -> float | None:
    """Signed day bps per $1mm net DV01. None when |net| is below the floor."""
    ticks = row.get("day_ticks")
    if ticks is None:
        return None
    net = float(row.get("buy_flow") or 0.0) - float(row.get("sell_flow") or 0.0)
    if abs(net) < MIN_ABS_NET_DV01:
        return None
    return (float(ticks) * BPS_PER_TICK) * 1e6 / net


def summarize_bps_per_mm(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["bps_per_mm"]) for row in rows if row.get("bps_per_mm") is not None]
    series = pd.Series(values, dtype="float64")
    if series.empty:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None, "min": None, "max": None}
    return {
        "n": int(series.size),
        "mean": float(series.mean()),
        "median": float(series.median()),
        "p25": float(series.quantile(0.25)),
        "p75": float(series.quantile(0.75)),
        "min": float(series.min()),
        "max": float(series.max()),
    }


def _side_stats(rows: list[dict[str, Any]], *, bid_dominant: bool) -> dict[str, Any]:
    picked = [row for row in rows if bool(row.get("bid_dominant")) is bid_dominant]
    ticks = [float(row["day_ticks"]) for row in picked if row.get("day_ticks") is not None]
    rallies = [row for row in picked if row.get("rallied") is True]
    series = pd.Series(ticks, dtype="float64")
    return {
        "n": len(picked),
        "rally_n": len(rallies),
        "rally_share": None if not picked else len(rallies) / len(picked),
        "mean_ticks": None if series.empty else float(series.mean()),
        "median_ticks": None if series.empty else float(series.median()),
    }


def fit_bps_per_mm_dv01(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """OLS of day bps on net $DV01. Slope scaled to bps per $1mm DV01."""
    xs: list[float] = []
    ys: list[float] = []
    for row in rows:
        ticks = row.get("day_ticks")
        if ticks is None:
            continue
        xs.append(float(row.get("buy_flow") or 0.0) - float(row.get("sell_flow") or 0.0))
        ys.append(float(ticks) * BPS_PER_TICK)
    if len(xs) < 2:
        return {"n": len(xs), "slope_bps_per_mm": None, "intercept_bps": None}
    x = pd.Series(xs, dtype="float64")
    y = pd.Series(ys, dtype="float64")
    var_x = float(((x - x.mean()) ** 2).sum())
    if var_x == 0:
        return {"n": len(xs), "slope_bps_per_mm": None, "intercept_bps": float(y.mean())}
    cov = float(((x - x.mean()) * (y - y.mean())).sum())
    slope = cov / var_x
    return {
        "n": len(xs),
        "slope_bps_per_mm": slope * 1e6,
        "intercept_bps": float(y.mean() - slope * x.mean()),
    }


def build_diagnostic(daily_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Descriptive bid-vs-ask confirmation rates. Not a signal."""
    cohorts = {}
    for name in COHORTS:
        subset = _cohort_rows(daily_rows, name)
        cohorts[name] = {
            "n": len(subset),
            "bid_gt_ask": _side_stats(subset, bid_dominant=True),
            "ask_gt_bid": _side_stats(subset, bid_dominant=False),
        }
    return {
        "label": "DESCRIPTIVE / RESEARCH-ONLY / NON-DIRECTIONAL",
        "rule": "bid aggress = buy_contracts (took the ask); ask aggress = sell_contracts (hit the bid)",
        "cohorts": cohorts,
        "bps_per_mm_dv01": {
            name: fit_bps_per_mm_dv01(_cohort_rows(daily_rows, name))
            for name in SLOPE_COHORTS
        },
        "bps_per_mm_distribution": {
            name: summarize_bps_per_mm(_cohort_rows(daily_rows, name))
            for name in SLOPE_COHORTS
        },
        "ratio_floor_dv01": MIN_ABS_NET_DV01,
    }


def _median(values: list[float]) -> float | None:
    series = pd.Series(values, dtype="float64")
    if series.empty:
        return None
    return float(series.median())


def _analog_candidates(day_info: dict[str, Any]) -> list[list[str]]:
    event_keys = list(day_info.get("analog_keys") or [])
    weekday = str(day_info.get("weekday") or "")
    out: list[list[str]] = []
    if event_keys:
        if weekday:
            out.append([*event_keys, weekday])
        out.append(list(event_keys))
        if len(event_keys) > 1:
            out.extend([[key] for key in event_keys])
    if weekday:
        out.append([weekday])
    out.append(["all"])
    return out


def _pick_analogs(history: list[dict[str, Any]], day_info: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    chosen_keys: list[str] = ["all"]
    chosen_rows = history
    for keys in _analog_candidates(day_info):
        picked = history
        for key in keys:
            picked = _cohort_rows(picked, key)
        if picked:
            chosen_keys, chosen_rows = keys, picked
            if len(picked) >= MIN_ANALOG_DAYS:
                return keys, picked
    return chosen_keys, chosen_rows


def build_today_outlook(
    symbol: str = "TY",
    *,
    day: date | None = None,
    now: datetime | None = None,
    db_path=None,
    events_db_path=None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Historical analog for today's tags vs flow so far. Not a forecast."""
    clock = now.astimezone(EASTERN) if now is not None else datetime.now(EASTERN)
    target = day or current_session_day(clock) or next_session_day(clock)
    event_kwargs: dict[str, Any] = {"start": target, "end": target}
    if events_db_path is not None:
        event_kwargs["db_path"] = events_db_path
    day_info = describe_calendar_day(target, events=load_tagged_events(**event_kwargs))
    history_kwargs: dict[str, Any] = {"symbol": symbol}
    if db_path is not None:
        history_kwargs["db_path"] = db_path
    if events_db_path is not None:
        history_kwargs["events_db_path"] = events_db_path
    full = list(history) if history is not None else aggregate_daily(**history_kwargs)
    target_key = target.isoformat()
    analogs_src = [row for row in full if row.get("date") != target_key]
    analog_keys, analogs = _pick_analogs(analogs_src, day_info)
    expected_buy = _median([float(row.get("buy_flow") or 0.0) for row in analogs])
    expected_sell = _median([float(row.get("sell_flow") or 0.0) for row in analogs])
    expected_net = None if expected_buy is None or expected_sell is None else expected_buy - expected_sell
    expected_total = None if expected_buy is None or expected_sell is None else expected_buy + expected_sell
    expected_bps = _median([float(row["day_bps"]) for row in analogs if row.get("day_bps") is not None])
    slope = fit_bps_per_mm_dv01(analogs)
    implied_bps = None
    if expected_net is not None and slope.get("slope_bps_per_mm") is not None:
        intercept = float(slope.get("intercept_bps") or 0.0)
        implied_bps = intercept + (slope["slope_bps_per_mm"] * expected_net / 1e6)
    today_kwargs = {k: v for k, v in (("db_path", db_path), ("events_db_path", events_db_path)) if v is not None}
    today_rows = aggregate_daily(symbol, start=target, end=target, **today_kwargs)
    so_far = today_rows[0] if today_rows else {
        "buy_flow": 0.0,
        "sell_flow": 0.0,
        "buy_contracts": 0.0,
        "sell_contracts": 0.0,
        "day_bps": None,
        "trade_count": 0,
    }
    so_far_buy = float(so_far.get("buy_flow") or 0.0)
    so_far_sell = float(so_far.get("sell_flow") or 0.0)
    so_far_net = so_far_buy - so_far_sell
    so_far_total = so_far_buy + so_far_sell
    so_far_implied = None
    if so_far_total and slope.get("slope_bps_per_mm") is not None:
        intercept = float(slope.get("intercept_bps") or 0.0)
        so_far_implied = intercept + (slope["slope_bps_per_mm"] * so_far_net / 1e6)
    return {
        "label": "DESCRIPTIVE / HISTORICAL ANALOG / NOT A FORECAST",
        "symbol": symbol,
        "asof_et": clock.isoformat(sep=" ", timespec="minutes"),
        "market": {
            "phase": market_phase(clock),
            "session_date": target.isoformat(),
        },
        "today": day_info,
        "analog": {
            "keys": analog_keys,
            "n": len(analogs),
            "dates": [row["date"] for row in analogs],
        },
        "expected": {
            "buy_flow": expected_buy,
            "sell_flow": expected_sell,
            "net_flow": expected_net,
            "total_flow": expected_total,
            "median_day_bps": expected_bps,
            "implied_bps_from_net": implied_bps,
            "slope_bps_per_mm": slope.get("slope_bps_per_mm"),
        },
        "so_far": {
            "buy_flow": so_far_buy,
            "sell_flow": so_far_sell,
            "net_flow": so_far_net,
            "total_flow": so_far_total,
            "day_bps": so_far.get("day_bps"),
            "implied_bps_from_net": so_far_implied,
            "share_of_typical_total": None if not expected_total else so_far_total / expected_total,
            "trade_count": int(so_far.get("trade_count") or 0),
        },
        "row": today_rows[0] if today_rows else None,
    }
