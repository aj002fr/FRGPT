"""Read-only live 2Y/5Y/7Y supply-weeks viewer."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.aggression_dashboard.core import trades_to_symbol_minute_bars
from scripts.aggression_dashboard.flow_history import persist_completed_bars
from scripts.supply_weeks_live.compare import (
    append_live_event_week,
    build_compare_rows,
    calendar_flags,
    comparison_stamp,
    event_week_from_hits,
    live_slices,
    slice_bounds,
    summarize_compare,
)
from scripts.supply_weeks_live.flow import fit_window_ols, fold_week_flow, residual_bps, summarize_windows
from scripts.supply_weeks_live.weeks import (
    current_window_name,
    hours_from_week_open,
    is_current_week,
    week_close_et,
    week_open_et,
    window_bounds,
)

EASTERN = ZoneInfo("America/New_York")

WEEK = {
    "week_start": "2026-09-21",
    "2y_date": "2026-09-22 11:30",
    "5y_date": "2026-09-23 13:00",
    "7y_date": "2026-09-24 13:00",
    "is_month_end": False,
}


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


def _seed(db: Path, when: datetime, buy: int, sell: int, price: float) -> None:
    utc = when.astimezone(timezone.utc)
    first = _ticks(utc, [(price, "B", buy)])
    second = _ticks(utc + timedelta(minutes=1), [(price + 2 / 32, "A", sell)])
    bar1, _ = trades_to_symbol_minute_bars(first, "TY", dv01_unit=85.0)
    bar2, _ = trades_to_symbol_minute_bars(second, "TY", dv01_unit=85.0)
    persist_completed_bars(
        {"TY": pd.concat([bar1, bar2]).sort_index()},
        before=utc + timedelta(minutes=2),
        source="test",
        db_path=db,
    )


def test_week_clock_and_windows() -> None:
    open_et = week_open_et(WEEK["week_start"])
    close_et = week_close_et(WEEK["week_start"])
    assert open_et.isoformat().startswith("2026-09-20T18:00:00")
    assert close_et.isoformat().startswith("2026-09-25T17:00:00")
    assert abs(hours_from_week_open(open_et, WEEK["week_start"])) < 1e-6
    pre = window_bounds(WEEK, "pre_2y")
    after2 = window_bounds(WEEK, "2y_to_5y")
    after5 = window_bounds(WEEK, "5y_to_7y")
    post = window_bounds(WEEK, "post_7y")
    assert pre[0] == 0.0
    assert abs(pre[1] - 41.5) < 1e-6
    assert after2[0] == pre[1]
    assert after5[0] == after2[1]
    assert post[0] == after5[1]
    assert post[1] == 119.0
    monday = datetime(2026, 9, 21, 10, 0, tzinfo=EASTERN)
    assert current_window_name(WEEK, now=monday) == "pre_2y"
    assert is_current_week(WEEK, now=monday) is True
    assert is_current_week(WEEK, now=datetime(2026, 9, 26, 12, 0, tzinfo=EASTERN)) is False


def test_fold_week_flow_is_read_only(tmp_path: Path) -> None:
    db = tmp_path / "flow_history.sqlite"
    _seed(db, datetime(2026, 9, 21, 10, 0, tzinfo=EASTERN), buy=10, sell=2, price=110.0)
    _seed(db, datetime(2026, 9, 22, 14, 0, tzinfo=EASTERN), buy=4, sell=8, price=110.25)
    before = db.read_bytes()
    folded = fold_week_flow(WEEK, db_path=db)
    assert db.read_bytes() == before
    assert folded["has_flow"] is True
    pre = folded["windows"]["pre_2y"]
    after2 = folded["windows"]["2y_to_5y"]
    assert pre["buy_flow"] == 10 * 85
    assert after2["sell_flow"] == 8 * 85
    assert after2["net_flow"] == (4 - 8) * 85
    clock = folded["clock"]["net"]
    assert len(clock) == 120
    assert clock[16] == (10 - 2) * 85
    assert clock[44] == (10 - 2) * 85 + (4 - 8) * 85
    closed = week_close_et(WEEK["week_start"]) <= datetime.now(EASTERN)
    if closed:
        assert clock[119] == clock[44]
    else:
        assert clock[119] is None


def test_typical_net_is_median_of_window_nets() -> None:
    samples = [
        {"net_flow": -1_000_000.0, "window_bps": -2.0, "buy_flow": 10.0, "sell_flow": 11.0},
        {"net_flow": -100_000.0, "window_bps": -0.5, "buy_flow": 10.0, "sell_flow": 10.1},
        {"net_flow": 200_000.0, "window_bps": 0.4, "buy_flow": 12.0, "sell_flow": 11.8},
    ]
    fit = fit_window_ols(samples)
    assert fit["n"] == 3
    assert abs(fit["median_net"] - (-100_000.0)) < 1e-6
    assert fit["slope_bps_per_mm"] is not None
    assert fit["implied_bps_at_median_net"] is not None
    assert abs(fit["median_buy"] - 10.0) < 1e-6
    assert abs(fit["median_sell"] - 11.0) < 1e-6
    leftover = residual_bps(-0.5, -100_000.0, fit["intercept_bps"], fit["slope_bps_per_mm"])
    assert leftover is not None
    assert abs(fit["residual_median"]) < 1.0


def test_residual_is_leftover_vs_ols() -> None:
    samples = [
        {"net_flow": 0.0, "window_bps": 1.0, "buy_flow": 2.0, "sell_flow": 2.0},
        {"net_flow": 1_000_000.0, "window_bps": 3.0, "buy_flow": 4.0, "sell_flow": 3.0},
    ]
    fit = fit_window_ols(samples)
    assert abs(fit["intercept_bps"] - 1.0) < 1e-6
    assert abs(fit["slope_bps_per_mm"] - 2.0) < 1e-6
    leftover = residual_bps(3.0, 1_000_000.0, 1.0, 2.0)
    assert leftover is not None
    assert abs(leftover) < 1e-9
    assert abs(fit["residual_median"]) < 1e-9
    assert abs(fit["median_buy"] - 3.0) < 1e-6
    assert abs(fit["median_sell"] - 2.5) < 1e-6


def test_summarize_windows_month_end_filter() -> None:
    weeks = [
        {"week_info": {"is_month_end": True}, "flow": {"windows": {"pre_2y": {"n_bars": 3, "net_flow": 1.0, "window_bps": 1.0}}}},
        {"week_info": {"is_month_end": False}, "flow": {"windows": {"pre_2y": {"n_bars": 4, "net_flow": 2.0, "window_bps": 2.0}}}},
        {"week_info": {"is_month_end": False}, "flow": {"windows": {"pre_2y": {"n_bars": 0, "net_flow": 0.0, "window_bps": 9.0}}}},
    ]
    all_rows = summarize_windows(weeks, month_end="all")
    me = summarize_windows(weeks, month_end="month_end")
    assert all_rows["pre_2y"]["n"] == 2
    assert me["pre_2y"]["n"] == 1
    assert abs(all_rows["pre_2y"]["median_net"] - 1.5) < 1e-6


def test_api_is_read_only(tmp_path: Path, monkeypatch) -> None:
    from scripts.supply_weeks_live import app as live_app

    db = tmp_path / "flow_history.sqlite"
    sqlite3.connect(db).close()
    before = db.read_bytes()
    monkeypatch.setattr(
        live_app,
        "build_payload",
        lambda month_end="all", drop_tier1=False, drop_month_end=False, force=False: {
            "weeks": [
                {
                    "week_key": "2026-W12",
                    "week_info": {"week_start": "2026-03-16"},
                    "price_data": {"hours": [0], "closes": [110]},
                    "flow": {
                        "has_flow": True,
                        "windows": {"pre_2y": {"n_bars": 2, "net_flow": 1.0, "window_bps": 0.2}},
                        "flow_path": {"hours": [0, 1], "signed_flow": [0, 1]},
                    },
                    "is_current": False,
                }
            ],
            "windows": {},
            "compare": {"rows": {}, "clocks": {}},
            "current": None,
            "last_bar_ts": None,
        },
    )
    monkeypatch.setattr(live_app, "latest_bar_ts", lambda *a, **k: None)
    monkeypatch.setattr(live_app, "feed_status", lambda *a, **k: {"state": "NOT_LIVE"})
    client = live_app.app.test_client()
    boot = client.get("/api/bootstrap")
    live = client.get("/api/live")
    assert boot.status_code == 200
    body = boot.get_json()
    assert body["success"] is True
    assert live.get_json()["success"] is True
    assert "flow_path" not in (body["weeks"][0].get("flow") or {})
    assert "compare" in body
    assert db.read_bytes() == before


def _weeks(*rows: dict) -> list[dict]:
    return [{"week_key": row.get("week_key", "W"), "week_info": row} for row in rows]


def test_compare_slice_bounds() -> None:
    two = datetime(2026, 9, 22, 11, 30, tzinfo=EASTERN)
    five = datetime(2026, 9, 23, 13, 0, tzinfo=EASTERN)
    bounds2 = slice_bounds(two)
    bounds5 = slice_bounds(five)
    assert bounds2["into"][0].isoformat().startswith("2026-09-22T08:30:00")
    assert bounds2["into"][1].isoformat().startswith("2026-09-22T11:30:00")
    assert bounds2["after"][0] == bounds2["into"][1]
    assert bounds2["after"][1].isoformat().startswith("2026-09-22T17:00:00")
    assert bounds5["into"][0].isoformat().startswith("2026-09-23T10:00:00")
    assert bounds5["after"][1].isoformat().startswith("2026-09-23T17:00:00")
    two_at_one = slice_bounds(comparison_stamp("2Y", two), tenor="2Y")
    assert two_at_one["from_8"][0].isoformat().startswith("2026-09-22T08:00:00")
    assert two_at_one["from_8"][1].isoformat().startswith("2026-09-22T13:00:00")
    assert two_at_one["into"][0].isoformat().startswith("2026-09-22T10:00:00")
    assert two_at_one["into"][1].isoformat().startswith("2026-09-22T13:00:00")
    assert two_at_one["after"][0] == two_at_one["into"][1]


def test_compare_excludes_auctions_and_medians(tmp_path: Path) -> None:
    db = tmp_path / "flow_history.sqlite"
    supply_a = datetime(2026, 9, 22, 10, 0, tzinfo=EASTERN)
    supply_b = datetime(2026, 8, 25, 10, 0, tzinfo=EASTERN)
    control = datetime(2026, 9, 15, 10, 0, tzinfo=EASTERN)
    other_auction = datetime(2026, 9, 8, 10, 0, tzinfo=EASTERN)
    weekend = datetime(2026, 9, 19, 10, 0, tzinfo=EASTERN)
    _seed(db, supply_a, buy=10, sell=2, price=110.0)
    _seed(db, datetime(2026, 9, 22, 14, 0, tzinfo=EASTERN), buy=3, sell=9, price=110.25)
    _seed(db, supply_b, buy=4, sell=8, price=111.0)
    _seed(db, control, buy=6, sell=6, price=109.0)
    _seed(db, other_auction, buy=99, sell=1, price=108.0)
    _seed(db, weekend, buy=50, sell=1, price=107.0)
    before = db.read_bytes()
    weeks = _weeks(
        {
            "week_key": "2026-W39",
            "week_start": "2026-09-21",
            "2y_date": "2026-09-22 11:30",
            "5y_date": "2026-09-23 13:00",
            "7y_date": "2026-09-24 13:00",
            "is_month_end": False,
        },
        {
            "week_key": "2026-W35",
            "week_start": "2026-08-24",
            "2y_date": "2026-08-25 11:30",
            "5y_date": "2026-08-26 13:00",
            "7y_date": "2026-08-27 13:00",
            "is_month_end": True,
        },
    )
    blocked = {date(2026, 9, 22), date(2026, 8, 25), date(2026, 9, 8), date(2026, 9, 23), date(2026, 9, 24)}
    now = datetime(2026, 9, 25, 12, 0, tzinfo=EASTERN)
    rows = build_compare_rows(weeks, db_path=db, now=now, auction_dates=blocked, day_flags={})
    assert db.read_bytes() == before
    summary = summarize_compare(rows, month_end="all", now=now)
    into = summary["rows"]["2Y"]["into"]
    after = summary["rows"]["2Y"]["after"]
    assert into["supply"]["n"] == 2
    assert into["control"]["n"] == 1
    assert abs(into["supply"]["median_buy"] - 7 * 85) < 1e-6
    assert abs(into["supply"]["median_sell"] - 5 * 85) < 1e-6
    assert abs(into["supply"]["median_net"] - 2 * 85) < 1e-6
    assert abs(into["control"]["median_buy"] - 6 * 85) < 1e-6
    assert abs(into["control"]["median_net"]) < 1e-6
    assert after["supply"]["n"] == 1
    assert after["control"]["n"] == 0
    me = summarize_compare(rows, month_end="month_end", now=now)
    assert me["rows"]["2Y"]["into"]["supply"]["n"] == 1
    assert abs(me["rows"]["2Y"]["into"]["supply"]["median_buy"] - 4 * 85) < 1e-6
    assert me["rows"]["2Y"]["into"]["control"]["n"] == 1
    unfinished = summarize_compare(rows, now=datetime(2026, 9, 22, 10, 30, tzinfo=EASTERN))
    assert unfinished["rows"]["2Y"]["into"]["supply"]["n"] == 1
    assert unfinished["rows"]["2Y"]["after"]["supply"]["n"] == 0


def test_compare_percentiles_and_control_drops() -> None:
    flags = calendar_flags(
        {date(2026, 9, 4), date(2026, 9, 30)},
        events=[{"date": "2026-09-04", "family": "nfp"}],
    )
    assert flags[date(2026, 9, 4)]["is_tier1"] is True
    assert flags[date(2026, 9, 4)]["is_calendar_month_end"] is False
    assert flags[date(2026, 9, 30)]["is_calendar_month_end"] is True
    assert flags[date(2026, 9, 30)]["is_tier1"] is False

    def row(day: str, net: float, bps: float, *, tier1: bool = False, month_end: bool = False) -> dict:
        return {
            "tenor": "2Y",
            "slice": "into",
            "side": "control",
            "date": day,
            "end_et": "2026-09-01T11:30:00-04:00",
            "n_bars": 2,
            "buy_flow": net,
            "sell_flow": 0.0,
            "net_flow": net,
            "window_bps": bps,
            "is_tier1": tier1,
            "is_calendar_month_end": month_end,
            "is_month_end": False,
        }

    rows = [
        row("2026-09-01", 1.0, 1.0),
        row("2026-09-08", 2.0, 2.0),
        row("2026-09-15", 4.0, 4.0),
        row("2026-09-04", 100.0, 9.0, tier1=True),
        row("2026-09-30", 200.0, 8.0, month_end=True),
    ]
    now = datetime(2026, 10, 1, 12, 0, tzinfo=EASTERN)
    plain = summarize_compare(rows, now=now)
    cell = plain["rows"]["2Y"]["into"]["control"]
    assert cell["n"] == 5
    assert abs(cell["median_net"] - 4.0) < 1e-6
    assert abs(cell["p25_net"] - 2.0) < 1e-6
    assert abs(cell["p75_net"] - 100.0) < 1e-6
    dropped = summarize_compare(rows, now=now, drop_tier1=True, drop_month_end=True)
    kept = dropped["rows"]["2Y"]["into"]["control"]
    assert kept["n"] == 3
    assert abs(kept["p25_net"] - 1.5) < 1e-6
    assert abs(kept["median_net"] - 2.0) < 1e-6
    assert abs(kept["p75_net"] - 3.0) < 1e-6
    assert abs(kept["median_bps"] - 2.0) < 1e-6


def test_flow_implied_is_normal_day_line_at_auction_net() -> None:
    def row(day: str, net: float, bps: float, side: str) -> dict:
        return {
            "tenor": "2Y",
            "slice": "from_8",
            "side": side,
            "date": day,
            "end_et": "2026-09-01T13:00:00-04:00",
            "n_bars": 2,
            "buy_flow": net if net > 0 else 0.0,
            "sell_flow": 0.0 if net > 0 else -net,
            "net_flow": net,
            "window_bps": bps,
            "is_tier1": False,
            "is_calendar_month_end": False,
            "is_month_end": False,
        }

    rows = [
        row("2026-09-01", 1_000_000, 2.0, "control"),
        row("2026-09-08", 0.0, 0.0, "control"),
        row("2026-09-15", -1_000_000, -2.0, "control"),
        row("2026-09-22", -92_000, 0.31, "supply"),
    ]
    summary = summarize_compare(rows, now=datetime(2026, 10, 1, 12, tzinfo=EASTERN))
    cell = summary["rows"]["2Y"]["from_8"]
    assert abs(cell["slope_bps_per_mm"] - 2.0) < 1e-9
    assert abs(cell["flow_implied_bps"] - (-0.184)) < 1e-9
    assert abs(cell["supply"]["median_bps"] - 0.31) < 1e-9


def test_live_slice_stays_out_of_the_median_until_it_closes() -> None:
    weeks = _weeks(WEEK)
    now = datetime(2026, 9, 22, 10, 0, tzinfo=EASTERN)
    rows = [
        {
            "tenor": "2Y",
            "slice": "into",
            "side": "supply",
            "date": "2026-09-22",
            "start_et": "2026-09-22T08:30:00-04:00",
            "end_et": "2026-09-22T11:30:00-04:00",
            "n_bars": 4,
            "buy_flow": 100.0,
            "sell_flow": 40.0,
            "net_flow": 60.0,
            "window_bps": 0.5,
            "is_month_end": False,
        }
    ]
    summary = summarize_compare(rows, weeks=weeks, now=now)
    assert summary["rows"]["2Y"]["into"]["supply"]["n"] == 0
    live = summary["live"]
    assert live["slices"]["2Y"]["into"]["status"] == "open"
    assert live["slices"]["2Y"]["into"]["start_et"].startswith("2026-09-22T10:00:00")
    assert live["slices"]["2Y"]["into"]["end_et"].startswith("2026-09-22T13:00:00")
    assert live["slices"]["2Y"]["into"]["net_flow"] == 60.0
    assert live["slices"]["2Y"]["after"]["status"] == "upcoming"
    midday = live_slices(rows, weeks, datetime(2026, 9, 22, 12, 0, tzinfo=EASTERN))
    assert midday["slices"]["2Y"]["into"]["status"] == "open"
    closed = live_slices(rows, weeks, datetime(2026, 9, 22, 13, 0, tzinfo=EASTERN))
    assert closed["slices"]["2Y"]["into"]["status"] == "done"


def test_event_calendar_week_is_current_when_csv_lags() -> None:
    monday = date(2026, 9, 21)
    hits = [
        {"family": "auction", "event_name": "2Y Note Auction", "date": "2026-09-22"},
        {"family": "auction", "event_name": "5Y Note Auction", "date": "2026-09-23"},
        {"family": "auction", "event_name": "7Y Note Auction", "date": "2026-09-24"},
    ]
    extra = event_week_from_hits(hits, monday)
    assert extra is not None
    assert extra["week_key"] == "2026-W39"
    assert extra["week_info"]["2y_date"] == "2026-09-22 11:30"
    assert extra["week_info"]["is_month_end"] is True
    now = datetime(2026, 9, 21, 15, 20, tzinfo=EASTERN)
    weeks = append_live_event_week([], now=now, hits=hits)
    assert len(weeks) == 1
    assert is_current_week(weeks[0]["week_info"], now) is True
    again = append_live_event_week(weeks, now=now, hits=hits)
    assert len(again) == 1
    live = live_slices([], weeks, now)
    assert live["week_key"] == "2026-W39"
    assert live["slices"]["2Y"]["into"]["status"] == "upcoming"
    assert live["slices"]["2Y"]["into"]["start_et"].startswith("2026-09-22T10:00:00")
    assert live["slices"]["2Y"]["from_8"]["status"] == "upcoming"
    assert live["slices"]["2Y"]["from_8"]["start_et"].startswith("2026-09-22T08:00:00")
    assert live["slices"]["2Y"]["from_8"]["end_et"].startswith("2026-09-22T13:00:00")
    assert live["slices"]["2Y"]["into"]["end_et"].startswith("2026-09-22T13:00:00")


def test_return_distribution_has_5y_auction_day() -> None:
    root = Path(__file__).resolve().parents[1] / "scripts" / "supply_weeks_live"
    html = (root / "templates" / "index.html").read_text(encoding="utf-8")
    js = (root / "static" / "dash.js").read_text(encoding="utf-8")
    assert "2Y auction day" in html
    assert "5Y auction day" in html
    assert 'dateKey: "2y_date"' in js
    assert 'dateKey: "5y_date"' in js
    for node in ("distro-5y-from_8", "distro-5y-into", "distro-5y-after"):
        assert f'id="{node}"' in html
        assert f'"{node[7:]}"' in js
