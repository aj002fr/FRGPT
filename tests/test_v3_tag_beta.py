from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timezone
from pathlib import Path

import numpy as np
import pytest

from scripts.aggression_dashboard_v3 import beta_ref as br
from scripts.aggression_dashboard_v3 import tag_beta as tb
from scripts.aggression_dashboard_v3.core import EASTERN

DV01 = {"TY": 85.0, "FV": 42.0, "TU": 36.0, "US": 125.0}


def _et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=EASTERN)


def _make_db(path: Path, days: dict) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE v1_aggress_flow_1m (ts_utc TEXT, ts_et TEXT, symbol TEXT, buy_contracts REAL, "
        "sell_contracts REAL, buy_flow REAL, sell_flow REAL, trade_count INTEGER, trade_price REAL)"
    )
    conn.execute("CREATE INDEX ix ON v1_aggress_flow_1m (symbol, ts_utc)")
    _add_days(conn, days)
    conn.commit()
    conn.close()


def _add_days(conn: sqlite3.Connection, days: dict) -> None:
    for day, (net, open_px, close_px) in days.items():
        rows = [(_et(day, 9), 0.0, 0.0, open_px), (_et(day, 15), max(net, 0.0), max(-net, 0.0), close_px)]
        for moment, buy, sell, px in rows:
            conn.execute(
                "INSERT INTO v1_aggress_flow_1m VALUES (?, ?, 'TY', ?, ?, 0, 0, 1, ?)",
                (moment.astimezone(timezone.utc).isoformat(timespec="seconds"), "", buy, sell, px),
            )


def _write_events(folder: Path, events: list) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "events_tagged_test.json").write_text(json.dumps({"events": events}), encoding="utf-8")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "load_dv01_table", lambda: dict(DV01))
    monkeypatch.setattr(br, "load_dv01_table", lambda: dict(DV01))
    monkeypatch.setattr(tb, "TAG_BETA_PATH", tmp_path / "tag_beta.json")
    monkeypatch.setattr(tb, "V1_FLOW_DB", tmp_path / "flow.sqlite")
    monkeypatch.setenv(tb.EVENTS_CACHE_DIR_ENV, str(tmp_path / "events"))
    return tmp_path


def test_one_tag_per_day_with_auction_first():
    by_date = tb._by_date([
        {"date": "2026-10-13", "family": "auction", "event_name": "3Y Note Auction"},
        {"date": "2026-10-13", "family": "cpi", "event_name": "CPI"},
        {"date": "2026-10-14", "family": "auction", "event_name": "20Y Bond Auction"},
        {"date": "2026-10-15", "family": "auction", "event_name": "10Y Note Auction"},
        {"date": "2026-10-15", "family": "auction", "event_name": "30Y Bond Auction"},
        {"date": "2026-10-16", "family": "nfp", "event_name": "NFP"},
        {"date": "2026-10-29", "family": "auction", "event_name": "7Y Note Auction"},
        {"date": "2026-10-30", "family": "gdp", "event_name": "GDP"},
    ])
    assert tb.day_tag(date(2026, 10, 13), by_date)[0] == "3Y"
    assert tb.day_tag(date(2026, 10, 14), by_date)[0] == "AUCTION_OTHER"
    assert tb.day_tag(date(2026, 10, 15), by_date)[0] == "AUCTION_OTHER"
    assert tb.day_tag(date(2026, 10, 16), by_date)[0] == "TIER1"
    assert tb.day_tag(date(2026, 10, 29), by_date)[0] == "7Y"
    assert tb.day_tag(date(2026, 10, 30), by_date)[0] == "MONTH_END"
    assert tb.day_tag(date(2026, 10, 28), by_date)[0] == "MONTH_END"
    assert tb.day_tag(date(2026, 10, 27), by_date)[0] == "REGULAR"


def test_session_date_follows_the_globex_roll():
    assert tb.session_date_for(_et(date(2026, 10, 9), 10)) == date(2026, 10, 9)
    assert tb.session_date_for(_et(date(2026, 10, 8), 17, 30)) == date(2026, 10, 8)
    assert tb.session_date_for(_et(date(2026, 10, 8), 18, 1)) == date(2026, 10, 9)
    assert tb.session_date_for(_et(date(2026, 10, 9), 18, 30)) == date(2026, 10, 9)
    assert tb.session_date_for(_et(date(2026, 10, 10), 12)) == date(2026, 10, 9)
    assert tb.session_date_for(_et(date(2026, 10, 11), 18, 30)) == date(2026, 10, 12)


def test_tag_beta_is_flow_history_day_ols_on_earlier_days_of_that_tag(env):
    regular = {
        date(2026, 9, 14): (100.0, 110.0, 110.0 + 4 / 64),
        date(2026, 9, 15): (-50.0, 110.0, 110.0 - 1 / 64),
        date(2026, 9, 16): (200.0, 110.0, 110.0 + 6 / 64),
        date(2026, 9, 17): (0.0, 110.0, 110.0 + 1 / 64),
    }
    auction = {date(2026, 9, 18): (300.0, 110.0, 110.0 - 8 / 64)}
    _make_db(env / "flow.sqlite", {**regular, **auction})
    _write_events(env / "events", [
        {"date": "2026-09-18", "family": "auction", "event_name": "7Y Note Auction"},
        {"date": "2026-09-24", "family": "auction", "event_name": "7Y Note Auction"},
    ])
    now = _et(date(2026, 9, 22), 10)
    out = br.ensure_beta_ref(price_symbol="TY", aggression_symbols=("TY",), dv01_adjust=True, now=now)
    x = np.array([v[0] * 85.0 for v in regular.values()])
    y = np.array([(v[2] - v[1]) * 16.0 for v in regular.values()])
    slope = np.polyfit(x, y, 1)[0]
    assert out["ok"] and out["tag"] == "REGULAR"
    assert out["n_sessions_used"] == 4
    assert out["slope_bps_per_mm"] == pytest.approx(slope * 1e6)
    assert out["beta_ref"] == pytest.approx(slope * 1e6 * 4 * (1 / 64) / 1e6)
    assert out["last_tag_day"] == "2026-09-17"
    assert not out["cached"]

    seven = tb.beta_for_session(price_symbol="TY", aggression_symbols=("TY",), dv01_adjust=True,
                                now=_et(date(2026, 9, 24), 10))
    assert seven["stale"]

    # A one-day tag warms and does not borrow the regular beta.
    tb.update_table(price_symbol="TY", aggression_symbols=("TY",), dv01_adjust=True, now=_et(date(2026, 9, 24), 10))
    seven = br.ensure_beta_ref(price_symbol="TY", aggression_symbols=("TY",), dv01_adjust=True,
                               now=_et(date(2026, 9, 24), 10), compute=False)
    assert seven["tag"] == "7Y" and not seven["ok"] and seven["beta_ref"] is None
    assert seven["n_sessions_used"] == 1


def test_close_update_reads_only_the_recent_days_and_refits_that_tag(env, monkeypatch):
    days = {
        date(2026, 9, 21): (100.0, 110.0, 110.0 + 4 / 64),
        date(2026, 9, 22): (-50.0, 110.0, 110.0 - 1 / 64),
        date(2026, 9, 23): (200.0, 110.0, 110.0 + 6 / 64),
    }
    _make_db(env / "flow.sqlite", days)
    _write_events(env / "events", [])
    kwargs = dict(price_symbol="TY", aggression_symbols=("TY",), dv01_adjust=True)
    first = tb.update_table(**kwargs, now=_et(date(2026, 9, 24), 10))
    assert [p["date"] for p in first["points"]] == ["2026-09-21", "2026-09-22", "2026-09-23"]

    conn = sqlite3.connect(env / "flow.sqlite")
    _add_days(conn, {date(2026, 9, 24): (-300.0, 110.0, 110.0 - 9 / 64)})
    conn.commit()
    conn.close()

    seen = []
    real = tb.read_day_points

    def spy(first_day, last_day, **kw):
        seen.append((first_day, last_day))
        return real(first_day, last_day, **kw)

    monkeypatch.setattr(tb, "read_day_points", spy)
    peek = br.ensure_beta_ref(**kwargs, now=_et(date(2026, 9, 24), 18, 5), compute=False)
    assert peek["stale"] and not seen
    after = br.ensure_beta_ref(**kwargs, now=_et(date(2026, 9, 24), 18, 5))
    assert seen == [(date(2026, 9, 18), date(2026, 9, 24))]
    assert after["ok"] and after["n_sessions_used"] == 4 and after["last_tag_day"] == "2026-09-24"
    again = br.ensure_beta_ref(**kwargs, now=_et(date(2026, 9, 25), 9))
    assert again["cached"] and len(seen) == 1


def test_peek_never_opens_v1(env, monkeypatch):
    _write_events(env / "events", [])

    def boom(*args, **kwargs):
        raise AssertionError("peek must not open V1")

    monkeypatch.setattr(tb, "_open_v1", boom)
    out = br.ensure_beta_ref(price_symbol="TY", aggression_symbols=("TY",), dv01_adjust=True,
                             now=_et(date(2026, 9, 24), 10), compute=False)
    assert not out["ok"] and out["stale"]


def test_unreadable_v1_writes_nothing(env):
    _write_events(env / "events", [])
    out = br.ensure_beta_ref(price_symbol="TY", aggression_symbols=("TY",), dv01_adjust=True,
                             now=_et(date(2026, 9, 24), 10))
    assert not out["ok"] and "V1 flow store" in out["reason"]
    assert not (env / "tag_beta.json").exists()
    assert not (env / "flow.sqlite").exists()


def test_ty_only_and_curve_keep_separate_tag_betas(env):
    now = _et(date(2026, 9, 23), 10)
    curve = ("FV", "TU", "TY", "US")
    store = {
        "version": tb.TAG_BETA_VERSION,
        "entries": {
            tb._identity("TY", curve, True): {
                "checked_through": "2026-09-22",
                "points": [],
                "tags": {"REGULAR": {"n": 603, "slope_bps_per_mm": 1.06, "beta_ref": 6.62e-08,
                                     "last_day": "2026-09-18", "reason": None}},
            },
            tb._identity("TY", ("TY",), True): {
                "checked_through": "2026-09-22",
                "points": [],
                "tags": {"REGULAR": {"n": 603, "slope_bps_per_mm": 1.39, "beta_ref": 8.67e-08,
                                     "last_day": "2026-09-18", "reason": None}},
            },
        },
    }
    (env / "tag_beta.json").write_text(json.dumps(store), encoding="utf-8")
    _write_events(env / "events", [])
    curve_beta = br.ensure_beta_ref(price_symbol="TY", aggression_symbols=curve, dv01_adjust=True,
                                    now=now, compute=False)
    ty_beta = br.ensure_beta_ref(price_symbol="TY", aggression_symbols=("TY",), dv01_adjust=True,
                                 now=now, compute=False)
    assert curve_beta["ok"] and curve_beta["tag"] == "REGULAR"
    assert ty_beta["ok"] and ty_beta["tag"] == "REGULAR"
    assert curve_beta["beta_ref"] == pytest.approx(6.62e-08)
    assert ty_beta["beta_ref"] == pytest.approx(8.67e-08)
    assert curve_beta["beta_ref"] != ty_beta["beta_ref"]


def test_nonpositive_slope_warms(env):
    days = {
        date(2026, 9, 21): (100.0, 110.0, 110.0 - 4 / 64),
        date(2026, 9, 22): (-50.0, 110.0, 110.0 + 1 / 64),
    }
    _make_db(env / "flow.sqlite", days)
    _write_events(env / "events", [])
    out = br.ensure_beta_ref(price_symbol="TY", aggression_symbols=("TY",), dv01_adjust=True,
                             now=_et(date(2026, 9, 23), 10))
    assert out["tag"] == "REGULAR" and not out["ok"] and out["slope_bps_per_mm"] < 0
