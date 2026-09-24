"""Economic events from Postgres: parity with the SQLite reader, cache fallbacks, bounded waits."""

from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path

import pytest

from scripts.aggression_flow_history_dash import events as ev

GOLDEN_SQLITE = Path(os.environ.get("EVENTS_GOLDEN_SQLITE", r"C:\dev\mdp_golden\events\events_20260923_1226.sqlite"))
WINDOWS = [
    (None, None),
    (date(2010, 1, 1), date(2014, 12, 31)),
    (date(2020, 3, 1), date(2020, 4, 30)),
    (date(2026, 9, 1), date(2026, 9, 30)),
    (date(2026, 9, 24), date(2026, 9, 24)),
    (date(2026, 10, 1), date(2026, 12, 31)),
]


def _pg_rows_from_sqlite(path: Path) -> list[tuple]:
    """Rows as psycopg2 returns them from the Postgres copy: native date/datetime, event_id = load order."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        parents = list(ev.TAGGED_PARENTS)
        rows = conn.execute(
            f"SELECT PARENT_EVENT, EVENT_NAME, COUNTRY_NAME, RELEASE_DATE, RELEASE_DATE_TIME, rowid FROM events "
            f"WHERE RELEASE_DATE IS NOT NULL AND PARENT_EVENT IN ({','.join('?' * len(parents))})",
            parents,
        ).fetchall()
    out = []
    for parent, name, country, rel_date, rel_dt, rowid in rows:
        out.append((
            parent,
            name,
            country,
            datetime.strptime(rel_date, "%Y-%m-%d %H:%M:%S").date(),
            datetime.strptime(rel_dt, "%Y-%m-%d %H:%M:%S") if rel_dt else None,
            rowid,
        ))
    random.Random(7).shuffle(out)
    return out


def _window(events: list[dict], lo: date | None, hi: date | None) -> list[dict]:
    return [
        e for e in events
        if (lo is None or e["date"] >= lo.isoformat()) and (hi is None or e["date"] <= hi.isoformat())
    ]


def _synthetic_sqlite(path: Path) -> Path:
    rows = [
        ("7Y Note Auction", "7Y Bid/Cover", "United States", "2026-09-24 00:00:00", "2026-09-24 13:00:00"),
        ("NFP", "Change in Nonfarm Payrolls", "United States", "2026-10-02 00:00:00", "2026-10-02 08:30:00"),
        ("NFP", "Unemployment Rate", "United States", "2026-10-02 00:00:00", "2026-10-02 08:31:00"),
        ("CPI", "CPI MoM", "United States", "2026-10-14 00:00:00", None),
        ("CPI", "CPI YoY", "Canada", "2026-10-14 00:00:00", "2026-10-14 08:30:00"),
        ("GDP Third", "GDP QoQ", "", "2026-09-30 00:00:00", "2026-09-30 08:30:00"),
        ("5Y Note Auction", "5Y High Yield", "United States", "2026-09-30 00:00:00", "2026-09-30 13:00:00"),
        ("FOMC Rate Decision", "Upper Bound", "United States", "2026-10-28 00:00:00", "2026-10-28 14:00:00"),
        ("FOMC Rate Decision", "Upper Bound", "United States", "2026-10-28 00:00:00", "2026-10-28 14:00:00"),
        ("4W Bill Auction", "4W Bid/Cover", "United States", "2026-09-24 00:00:00", "2026-09-24 11:30:00"),
    ]
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE events (PARENT_EVENT TEXT, EVENT_NAME TEXT, COUNTRY_NAME TEXT, RELEASE_DATE TEXT, "
            "RELEASE_DATE_TIME TEXT, TYPE TEXT, TICKER TEXT, TIER TEXT)"
        )
        conn.executemany("INSERT INTO events VALUES (?,?,?,?,?,NULL,NULL,NULL)", rows)
        # Same indexes as Yaman's events.sqlite: the parent index decides the reader's same-day order.
        for name, column in (("dt", "RELEASE_DATE_TIME"), ("type", "TYPE"), ("ticker", "TICKER"),
                             ("name", "EVENT_NAME"), ("parent", "PARENT_EVENT"), ("tier", "TIER")):
            conn.execute(f"CREATE INDEX ix_events_{name} ON events ({column})")
    return path


def test_pg_row_mapping_equals_sqlite_reader_synthetic(tmp_path: Path) -> None:
    db = _synthetic_sqlite(tmp_path / "events.sqlite")
    pg_events = ev.events_from_pg_rows(_pg_rows_from_sqlite(db))
    for lo, hi in WINDOWS:
        assert _window(pg_events, lo, hi) == ev.load_tagged_events(start=lo, end=hi, db_path=db)


@pytest.mark.skipif(not GOLDEN_SQLITE.exists(), reason=f"no events snapshot at {GOLDEN_SQLITE}")
def test_pg_row_mapping_equals_sqlite_reader_on_loaded_snapshot() -> None:
    pg_events = ev.events_from_pg_rows(_pg_rows_from_sqlite(GOLDEN_SQLITE))
    assert len(pg_events) > 2000
    for lo, hi in WINDOWS:
        assert _window(pg_events, lo, hi) == ev.load_tagged_events(start=lo, end=hi, db_path=GOLDEN_SQLITE)


@pytest.mark.skipif(
    os.environ.get("EVENTS_PG_PARITY") != "1" or not GOLDEN_SQLITE.exists(),
    reason="live Postgres parity is opt-in: EVENTS_PG_PARITY=1 and ECON_EVENTS_PG_DSN",
)
def test_live_postgres_equals_loaded_sqlite_snapshot() -> None:
    dsn = ev._pg_dsn()
    assert dsn, "ECON_EVENTS_PG_DSN not configured"
    pg_events, facts = ev.fetch_pg_events(dsn, application_name="mdp_events_parity_test")
    assert facts["table_rows"] > 40000
    for lo, hi in WINDOWS:
        assert _window(pg_events, lo, hi) == ev.load_tagged_events(start=lo, end=hi, db_path=GOLDEN_SQLITE)


def _events(*days: str) -> list[dict]:
    return [{"date": d, "family": "nfp", "label": "NFP", "event_name": "NFP", "ts_et": f"{d} 08:30:00"} for d in days]


FACTS = {"host": "stub", "table_rows": 3, "max_event_id": 3, "latest_actual_et": "2026-09-23 10:00:00"}


def test_failed_refresh_keeps_last_good(tmp_path: Path) -> None:
    results = [(_events("2026-10-02"), FACTS), RuntimeError("server closed the connection")]

    def fetch(_dsn, **_kw):
        item = results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    cache = ev.EventsCache(fetch=fetch, dsn_source=lambda: "postgresql://stub", cache_dir=lambda: tmp_path)
    assert cache.refresh() is True
    assert cache.refresh() is False
    assert cache.events() == _events("2026-10-02")
    status = cache.status()
    assert status["ok"] is False and "server closed" in status["last_error"]
    assert status["rows"] == 1 and status["calendar_horizon"] == "2026-10-02"
    assert status["feed_age_days"] > 0


def test_cold_start_waits_at_most_the_bound_then_uses_last_good_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ev, "EVENTS_COLD_WAIT_SEC", 0.5)
    release = threading.Event()

    def slow_fetch(_dsn, **_kw):
        release.wait(10)
        return _events("2026-12-09"), FACTS

    cache = ev.EventsCache(fetch=slow_fetch, dsn_source=lambda: "postgresql://stub", cache_dir=lambda: tmp_path)
    (tmp_path / "events_tagged_mdp_test.json").write_text(
        json.dumps({"saved_utc": "2026-09-24T12:00:00+00:00", "facts": FACTS, "events": _events("2026-10-02")}),
        encoding="utf-8",
    )
    cache.start("mdp_test")
    started = time.monotonic()
    got = cache.events()
    assert time.monotonic() - started < 2.0
    assert got == _events("2026-10-02")
    assert "last-good file" in cache.status()["fallback"]
    release.set()


def test_first_load_failure_uses_file_and_success_writes_it(tmp_path: Path) -> None:
    cache = ev.EventsCache(fetch=lambda *_a, **_k: (_events("2026-11-06"), FACTS), dsn_source=lambda: "x", cache_dir=lambda: tmp_path)
    cache._app_name = "mdp_x"
    assert cache.refresh() is True
    saved = json.loads((tmp_path / "events_tagged_mdp_x.json").read_text(encoding="utf-8"))
    assert saved["events"] == _events("2026-11-06")

    def broken(*_a, **_k):
        raise OSError("connection refused")

    restarted = ev.EventsCache(fetch=broken, dsn_source=lambda: "x", cache_dir=lambda: tmp_path)
    restarted._app_name = "mdp_x"
    assert restarted.refresh() is False
    assert restarted.events() == _events("2026-11-06")


def test_missing_dsn_is_reported(tmp_path: Path) -> None:
    cache = ev.EventsCache(fetch=lambda *_a, **_k: ([], FACTS), dsn_source=lambda: None, cache_dir=lambda: tmp_path)
    assert cache.refresh() is False
    assert ev.EVENTS_PG_DSN_ENV in cache.status()["last_error"]
    assert cache.events() == []


def test_load_tagged_events_filters_the_cache_and_returns_copies(monkeypatch, tmp_path: Path) -> None:
    cache = ev.EventsCache(fetch=lambda *_a, **_k: (_events("2026-09-24", "2026-10-02"), FACTS), dsn_source=lambda: "x", cache_dir=lambda: tmp_path)
    cache.refresh()
    monkeypatch.setattr(ev, "_EVENTS_CACHE", cache)
    got = ev.load_tagged_events(start=date(2026, 10, 1), end=datetime(2026, 10, 2, 23, 0))
    assert [e["date"] for e in got] == ["2026-10-02"]
    got[0]["date"] = "mutated"
    assert ev.load_tagged_events()[1]["date"] == "2026-10-02"
