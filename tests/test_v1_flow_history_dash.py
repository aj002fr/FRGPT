"""Read-only V1 flow-history viewer aggregation and APIs."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.aggression_dashboard.core import trades_to_symbol_minute_bars
from scripts.aggression_dashboard.flow_history import persist_completed_bars
from scripts.aggression_flow_history_dash.daily import aggregate_daily, build_diagnostic
from scripts.aggression_flow_history_dash.events import tag_month_end_days

EASTERN = ZoneInfo("America/New_York")


def _ticks(start: datetime, rows: list[tuple[float, str, int]]) -> pd.DataFrame:
    out = []
    for i, (price, side, size) in enumerate(rows):
        out.append({
            "ts": start + timedelta(seconds=i * 5),
            "price": price,
            "size": size,
            "side": side,
            "instrument_id": "TY",
            "raw_symbol": "ZNU6",
            "dashboard_symbol": "TY",
        })
    return pd.DataFrame(out)


def _seed_day(db: Path, day: date, buy: int, sell: int, open_px: float, close_px: float) -> None:
    start = datetime(day.year, day.month, day.day, 14, 0, tzinfo=EASTERN).astimezone(timezone.utc)
    first = _ticks(start, [(open_px, "B", buy)])
    second = _ticks(start + timedelta(minutes=1), [(close_px, "A", sell)])
    bar1, _ = trades_to_symbol_minute_bars(first, "TY", dv01_unit=85.0)
    bar2, _ = trades_to_symbol_minute_bars(second, "TY", dv01_unit=85.0)
    persist_completed_bars(
        {"TY": pd.concat([bar1, bar2]).sort_index()},
        before=start + timedelta(minutes=2),
        source="test",
        db_path=db,
    )


def test_daily_splits_buy_and_sell(tmp_path: Path) -> None:
    db = tmp_path / "flow_history.sqlite"
    events = tmp_path / "missing.db"
    _seed_day(db, date(2026, 9, 10), buy=10, sell=4, open_px=110.0, close_px=110.0 + 3 / 64)
    rows = aggregate_daily("TY", db_path=db, events_db_path=events)
    assert len(rows) == 1
    row = rows[0]
    assert row["date"] == "2026-09-10"
    assert row["buy_contracts"] == 10.0
    assert row["sell_contracts"] == 4.0
    assert row["bid_dominant"] is True
    assert row["rallied"] is True
    assert abs(row["day_ticks"] - 3.0) < 1e-6
    assert row["tags"] == []
    assert row["weekday"] == "Thu"
    assert row["bps_per_mm"] is None


def test_session_day_is_globex_not_midnight() -> None:
    from scripts.aggression_flow_history_dash.session import market_phase, session_day

    assert session_day(datetime(2026, 9, 20, 18, 6, tzinfo=EASTERN)) == date(2026, 9, 21)
    assert session_day(datetime(2026, 9, 21, 10, 0, tzinfo=EASTERN)) == date(2026, 9, 21)
    assert session_day(datetime(2026, 9, 21, 17, 30, tzinfo=EASTERN)) is None
    assert session_day(datetime(2026, 9, 21, 18, 30, tzinfo=EASTERN)) == date(2026, 9, 22)
    assert session_day(datetime(2026, 9, 18, 16, 0, tzinfo=EASTERN)) == date(2026, 9, 18)
    assert session_day(datetime(2026, 9, 18, 17, 30, tzinfo=EASTERN)) is None
    assert session_day(datetime(2026, 9, 20, 17, 50, tzinfo=EASTERN)) is None
    assert market_phase(datetime(2026, 9, 20, 18, 6, tzinfo=EASTERN)) == "OPEN"
    assert market_phase(datetime(2026, 9, 20, 17, 50, tzinfo=EASTERN)) == "CLOSED"


def test_feed_status_live_only_when_open_and_fresh() -> None:
    from scripts.aggression_flow_history_dash.session import feed_status

    now = datetime(2026, 9, 20, 18, 10, tzinfo=EASTERN)
    live = feed_status(now - timedelta(seconds=30), now=now)
    stale = feed_status(now - timedelta(minutes=10), now=now)
    closed = feed_status(now, now=datetime(2026, 9, 20, 17, 50, tzinfo=EASTERN))
    assert live["state"] == "LIVE"
    assert live["session_date"] == "2026-09-21"
    assert stale["state"] == "NOT_LIVE"
    assert closed["state"] == "CLOSED"


def test_sunday_evening_rolls_into_monday_session(tmp_path: Path) -> None:
    db = tmp_path / "flow_history.sqlite"
    events = tmp_path / "missing.db"
    start = datetime(2026, 9, 20, 19, 0, tzinfo=EASTERN).astimezone(timezone.utc)
    first = _ticks(start, [(110.0, "B", 6)])
    second = _ticks(start + timedelta(minutes=1), [(110.0 + 2 / 64, "A", 2)])
    bar1, _ = trades_to_symbol_minute_bars(first, "TY", dv01_unit=85.0)
    bar2, _ = trades_to_symbol_minute_bars(second, "TY", dv01_unit=85.0)
    persist_completed_bars(
        {"TY": pd.concat([bar1, bar2]).sort_index()},
        before=start + timedelta(minutes=2),
        source="test",
        db_path=db,
    )
    rows = aggregate_daily("TY", db_path=db, events_db_path=events)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-09-21"
    assert rows[0]["weekday"] == "Mon"


def test_classifies_short_calendar_aliases() -> None:
    from scripts.aggression_flow_history_dash.events import classify_event_name

    assert classify_event_name("10Y Auction") == "auction"
    assert classify_event_name("10Y Note Auction") == "auction"
    assert classify_event_name("NFP JUL") == "nfp"
    assert classify_event_name("Change in Nonfarm Payrolls") == "nfp"
    assert classify_event_name("CPI JUN") == "cpi"
    assert classify_event_name("FOMC Rate Decision (Upper Bound)") == "fomc"
    assert classify_event_name("Fed Press Conference") == "fomc"
    assert classify_event_name("CPI (Regional level) JUN") is None


def test_yaman_events_tag_supply_and_tier1(tmp_path: Path) -> None:
    from scripts.aggression_flow_history_dash.events import attach_event_tags, load_tagged_events

    db = tmp_path / "events.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE events (PARENT_EVENT TEXT, EVENT_NAME TEXT, COUNTRY_NAME TEXT, "
            "RELEASE_DATE TEXT, RELEASE_DATE_TIME TEXT)"
        )
        conn.executemany(
            "INSERT INTO events VALUES (?,?,?,?,?)",
            [
                ("10Y Note Auction", "10Y Bid/Cover Ratio", "United States", "2026-06-10 00:00:00", "2026-06-10 13:00:00"),
                ("NFP", "Change in Nonfarm Payrolls", "United States", "2026-07-02 00:00:00", "2026-07-02 08:30:00"),
                ("4W Bill Auction", "4W Bid/Cover Ratio", "United States", "2026-06-11 00:00:00", "2026-06-11 11:30:00"),
            ],
        )
    events = load_tagged_events(db_path=db)
    families = {(row["date"], row["family"], row["event_name"]) for row in events}
    assert ("2026-06-10", "auction", "10Y Note Auction") in families
    assert ("2026-07-02", "nfp", "NFP") in families
    assert not any(row["family"] == "auction" and "Bill" in row["event_name"] for row in events)
    tagged = attach_event_tags(
        [{"date": "2026-06-10"}, {"date": "2026-07-02"}, {"date": "2026-06-11"}],
        events,
        month_end_days=3,
    )
    by_date = {row["date"]: row for row in tagged}
    assert by_date["2026-06-10"]["is_auction"] is True
    assert by_date["2026-06-10"]["is_supply"] is True
    assert by_date["2026-07-02"]["is_tier1"] is True
    assert by_date["2026-07-02"]["is_nfp"] is True
    assert by_date["2026-06-11"]["is_auction"] is False


def test_day_bps_per_mm_uses_net_floor() -> None:
    from scripts.aggression_flow_history_dash.daily import day_bps_per_mm

    fat = {"buy_flow": 2_000_000, "sell_flow": 0, "day_ticks": 8.0}
    thin = {"buy_flow": 100_000, "sell_flow": 0, "day_ticks": 8.0}
    assert abs(day_bps_per_mm(fat) - 1.0) < 1e-9
    assert day_bps_per_mm(thin) is None


def test_ols_bps_per_mm_dv01() -> None:
    from scripts.aggression_flow_history_dash.daily import fit_bps_per_mm_dv01

    rows = [
        {"buy_flow": 2_000_000, "sell_flow": 0, "day_ticks": 8.0},
        {"buy_flow": 0, "sell_flow": 2_000_000, "day_ticks": -8.0},
    ]
    fit = fit_bps_per_mm_dv01(rows)
    assert fit["n"] == 2
    assert abs(fit["slope_bps_per_mm"] - 1.0) < 1e-9


def test_flags_51030_auctions_only() -> None:
    from scripts.aggression_flow_history_dash.events import attach_event_tags

    rows = attach_event_tags(
        [{"date": "2026-08-12"}, {"date": "2026-06-16"}, {"date": "2026-06-23"}],
        [
            {"date": "2026-08-12", "family": "auction", "event_name": "10Y Auction", "label": "AUCTION", "ts_et": ""},
            {"date": "2026-06-16", "family": "auction", "event_name": "20Y Auction", "label": "AUCTION", "ts_et": ""},
            {"date": "2026-06-23", "family": "auction", "event_name": "2Y Auction", "label": "AUCTION", "ts_et": ""},
        ],
        month_end_days=3,
    )
    by_date = {row["date"]: row for row in rows}
    assert by_date["2026-08-12"]["is_auction_51030"] is True
    assert by_date["2026-06-16"]["is_auction_51030"] is False
    assert by_date["2026-06-23"]["is_auction_51030"] is False


def test_month_end_tags_last_three_business_days() -> None:
    tagged = tag_month_end_days([
        date(2026, 8, 27),
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 1),
    ])
    assert date(2026, 8, 27) in tagged
    assert date(2026, 8, 28) in tagged
    assert date(2026, 8, 31) in tagged
    assert date(2026, 9, 1) not in tagged


def test_describe_calendar_day_reads_yaman_and_month_end(tmp_path: Path) -> None:
    from scripts.aggression_flow_history_dash.events import describe_calendar_day

    db = tmp_path / "events.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE events (PARENT_EVENT TEXT, EVENT_NAME TEXT, COUNTRY_NAME TEXT, "
            "RELEASE_DATE TEXT, RELEASE_DATE_TIME TEXT)"
        )
        conn.execute(
            "INSERT INTO events VALUES (?,?,?,?,?)",
            ("10Y Note Auction", "10Y Bid/Cover Ratio", "United States", "2026-09-09 00:00:00", "2026-09-09 13:00:00"),
        )
    auction = describe_calendar_day(date(2026, 9, 9), db_path=db)
    assert auction["is_auction"] is True
    assert auction["weekday"] == "Wed"
    assert "AUCTION/SUPPLY" in auction["tags"]
    month_end = describe_calendar_day(date(2026, 9, 30), db_path=db)
    assert month_end["is_month_end"] is True
    untagged = describe_calendar_day(date(2026, 9, 20), db_path=db)
    assert untagged["weekday"] == "Sun"
    assert untagged["tags"] == []
    assert untagged["is_auction"] is False
    assert untagged["is_tier1"] is False
    keys = [item["key"] for item in auction["considered"]]
    assert keys == ["auction", "month_end", "tier1", "weekday"]
    by_key = {item["key"]: item for item in auction["considered"]}
    assert by_key["auction"]["on"] is True
    assert by_key["month_end"]["on"] is False
    assert by_key["weekday"]["on"] is True


def test_today_outlook_uses_weekday_analogs(tmp_path: Path) -> None:
    from scripts.aggression_flow_history_dash.daily import build_today_outlook

    db = tmp_path / "flow_history.sqlite"
    events = tmp_path / "missing.db"
    _seed_day(db, date(2026, 9, 1), buy=20, sell=10, open_px=110.0, close_px=110.0 + 8 / 64)
    _seed_day(db, date(2026, 9, 8), buy=18, sell=8, open_px=110.0, close_px=110.0 + 4 / 64)
    _seed_day(db, date(2026, 9, 15), buy=22, sell=12, open_px=110.0, close_px=110.0 + 12 / 64)
    _seed_day(db, date(2026, 9, 9), buy=5, sell=15, open_px=110.0, close_px=110.0 - 4 / 64)
    out = build_today_outlook("TY", day=date(2026, 9, 22), db_path=db, events_db_path=events)
    assert out["today"]["weekday"] == "Tue"
    assert out["analog"]["keys"] == ["Tue"]
    assert out["analog"]["n"] == 3
    assert out["so_far"]["total_flow"] == 0.0
    assert out["expected"]["buy_flow"] == 20 * 85
    assert out["expected"]["sell_flow"] == 10 * 85


def test_diagnostic_bid_heavy_rally_rate(tmp_path: Path) -> None:
    db = tmp_path / "flow_history.sqlite"
    events = tmp_path / "missing.db"
    _seed_day(db, date(2026, 9, 8), buy=12, sell=3, open_px=110.0, close_px=110.0 + 2 / 64)
    _seed_day(db, date(2026, 9, 9), buy=2, sell=9, open_px=110.0, close_px=110.0 - 4 / 64)
    rows = aggregate_daily("TY", db_path=db, events_db_path=events)
    diag = build_diagnostic(rows)
    bid = diag["cohorts"]["all"]["bid_gt_ask"]
    ask = diag["cohorts"]["all"]["ask_gt_bid"]
    assert bid["n"] == 1
    assert bid["rally_share"] == 1.0
    assert ask["n"] == 1
    assert ask["rally_share"] == 0.0


def test_apis_are_read_only(tmp_path: Path, monkeypatch) -> None:
    from scripts.aggression_flow_history_dash import app as dash_app

    db = tmp_path / "flow_history.sqlite"
    events = tmp_path / "events.db"
    _seed_day(db, date(2026, 9, 10), buy=5, sell=1, open_px=110.0, close_px=110.0)
    sqlite3.connect(events).close()
    before = db.read_bytes()

    def daily(*args, **kwargs):
        return aggregate_daily("TY", db_path=db, events_db_path=events)

    monkeypatch.setattr(dash_app, "cached_daily", lambda *a, **k: daily())
    monkeypatch.setattr(dash_app, "cached_history", lambda *a, **k: daily())
    monkeypatch.setattr(dash_app, "latest_bar_ts", lambda *a, **k: None)
    monkeypatch.setattr(
        dash_app,
        "load_intraday",
        lambda symbol, day, **kwargs: {
            "date": day.isoformat(),
            "symbol": symbol,
            "rows": [],
            "events": [],
        },
    )
    monkeypatch.setattr(dash_app, "load_tagged_events", lambda **kwargs: [])
    monkeypatch.setattr(
        dash_app,
        "build_today_outlook",
        lambda *a, **k: {
            "label": "DESCRIPTIVE / HISTORICAL ANALOG / NOT A FORECAST",
            "today": {"date": "2026-09-10", "weekday": "Thu", "tags": []},
            "analog": {"keys": ["all"], "n": 0, "dates": []},
            "expected": {},
            "so_far": {},
        },
    )
    client = dash_app.app.test_client()
    daily_resp = client.get("/api/daily?symbol=TY")
    diag_resp = client.get("/api/diagnostic?symbol=TY")
    intra_resp = client.get("/api/intraday?symbol=TY&date=2026-09-10")
    events_resp = client.get("/api/events")
    today_resp = client.get("/api/today?symbol=TY")
    boot_resp = client.get("/api/bootstrap?symbol=TY")
    assert daily_resp.status_code == 200
    assert daily_resp.get_json()["success"] is True
    assert diag_resp.get_json()["success"] is True
    assert intra_resp.get_json()["success"] is True
    assert events_resp.get_json()["success"] is True
    assert today_resp.get_json()["success"] is True
    assert boot_resp.get_json()["success"] is True
    assert "rows" in boot_resp.get_json()
    assert db.read_bytes() == before
    assert "persist_completed_bars" not in dash_app.__dict__


def test_accessapi_trades_map_to_front_month_bars() -> None:
    from scripts.aggression_flow_history_dash.backfill_accessapi import (
        fold_session,
        trades_to_v1_frame,
        weekday_sessions,
    )

    raw = pd.DataFrame(
        {
            "ts_event": [
                "2022-01-03T14:00:01Z",
                "2022-01-03T14:00:02Z",
                "2022-01-03T14:01:01Z",
            ],
            "symbol": ["ZNH2", "ZNM2", "ZNH2"],
            "price": [129.78, 129.50, 129.79],
            "size": [10, 2, 4],
            "side": ["B", "A", "A"],
        }
    )
    frame = trades_to_v1_frame(raw, "TY")
    assert set(frame["instrument_id"]) == {"ZNH2", "ZNM2"}
    bars = fold_session(frame, "TY", 85.0)
    assert float(bars.iloc[0]["buy_contracts"]) == 10.0
    assert float(bars.iloc[0]["sell_contracts"]) == 0.0
    assert weekday_sessions(date(2022, 1, 1), date(2022, 1, 7))[0] == date(2022, 1, 3)


def test_cached_daily_keeps_closed_days_for_the_session(tmp_path: Path, monkeypatch) -> None:
    from scripts.aggression_flow_history_dash import cache as daily_cache

    db = tmp_path / "flow_history.sqlite"
    events = tmp_path / "missing.db"
    _seed_day(db, date(2026, 9, 9), buy=4, sell=2, open_px=110.0, close_px=110.0)
    _seed_day(db, date(2026, 9, 10), buy=5, sell=1, open_px=110.0, close_px=110.0)
    daily_cache.clear_daily_cache()
    calls = {"full": 0, "today": 0}
    real = daily_cache.aggregate_daily

    def wrapped(*args, **kwargs):
        if kwargs.get("start") is None:
            calls["full"] += 1
        else:
            calls["today"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(daily_cache, "current_session_day", lambda now=None: date(2026, 9, 10))
    monkeypatch.setattr(daily_cache, "aggregate_daily", wrapped)
    first = daily_cache.cached_daily("TY", db_path=db, events_db_path=events)
    second = daily_cache.cached_daily("TY", db_path=db, events_db_path=events)
    assert [row["date"] for row in first] == [row["date"] for row in second]
    assert calls["full"] == 1
    assert calls["today"] >= 1
    assert daily_cache.latest_bar_ts("TY", db_path=db) is not None


def test_latest_bar_ts_opens_plain_path(tmp_path: Path) -> None:
    from scripts.aggression_flow_history_dash import cache as daily_cache

    db = tmp_path / "flow_history.sqlite"
    _seed_day(db, date(2026, 9, 10), buy=5, sell=1, open_px=110.0, close_px=110.0)
    stamp = daily_cache.latest_bar_ts("TY", db_path=db)
    assert stamp
    assert "2026-09-10" in stamp or "2026-09-11" in stamp


def test_today_outlook_reuses_passed_history(tmp_path: Path) -> None:
    from scripts.aggression_flow_history_dash.daily import aggregate_daily, build_today_outlook

    db = tmp_path / "flow_history.sqlite"
    events = tmp_path / "missing.db"
    _seed_day(db, date(2026, 9, 1), buy=20, sell=10, open_px=110.0, close_px=110.0 + 8 / 64)
    _seed_day(db, date(2026, 9, 8), buy=18, sell=8, open_px=110.0, close_px=110.0 + 4 / 64)
    _seed_day(db, date(2026, 9, 15), buy=22, sell=12, open_px=110.0, close_px=110.0 + 12 / 64)
    history = aggregate_daily("TY", db_path=db, events_db_path=events)
    out = build_today_outlook("TY", day=date(2026, 9, 22), db_path=db, events_db_path=events, history=history)
    assert out["analog"]["keys"] == ["Tue"]
    assert out["analog"]["n"] == 3


def test_today_outlook_rereads_session_from_store(tmp_path: Path) -> None:
    from scripts.aggression_flow_history_dash.daily import aggregate_daily, build_today_outlook

    db = tmp_path / "flow_history.sqlite"
    events = tmp_path / "missing.db"
    _seed_day(db, date(2026, 9, 10), buy=8, sell=2, open_px=110.0, close_px=110.0 + 4 / 64)
    history = aggregate_daily("TY", db_path=db, events_db_path=events)
    stale = [{**row, "buy_flow": 1.0, "sell_flow": 1.0} if row["date"] == "2026-09-10" else row for row in history]
    out = build_today_outlook("TY", day=date(2026, 9, 10), db_path=db, events_db_path=events, history=stale)
    assert out["so_far"]["buy_flow"] == 8 * 85
    assert out["so_far"]["sell_flow"] == 2 * 85
    assert out["row"]["date"] == "2026-09-10"
