from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v21 import app as app_module
from scripts.aggression_dashboard_v21.event_flow import load_events, release_id
from scripts.aggression_dashboard_v21.event_store import EventPaperStore, STORE_DIR, TICK_SIZE


def _schedule(release: pd.Timestamp) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "release_id": release_id(release),
                "event_ts_utc": release,
                "event_ts_et": release.tz_convert("America/New_York"),
                "et_date": release.tz_convert("America/New_York").strftime("%Y-%m-%d"),
                "event_name": "CPI MoM | Core CPI MoM",
                "component_count": 2,
            }
        ]
    )


def _features(release: pd.Timestamp, flow: float = 100.0, minutes: int = 40) -> pd.DataFrame:
    index = pd.date_range(release - pd.Timedelta(minutes=1), periods=minutes + 2, freq="1min", tz="UTC")
    price = 110.0 + np.arange(len(index)) * TICK_SIZE
    agg = np.zeros(len(index))
    for minute in range(1, 6):
        agg[index == release + pd.Timedelta(minutes=minute)] = flow
    frame = pd.DataFrame({"price": price, "agg_1m": agg}, index=index)
    frame["agg_5m"] = frame["agg_1m"].rolling(5, min_periods=1).sum()
    return frame


def test_load_events_collapses_timestamp_and_localizes_et(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE economic_events(event_date TEXT, event_name TEXT, country TEXT)")
        conn.executemany(
            "INSERT INTO economic_events VALUES(?,?,?)",
            [
                ("2026-07-09 08:30:00", "CPI MoM", "United States"),
                ("2026-07-09 08:30:00", "Core CPI MoM", "US"),
                ("2026-07-09 09:00:00", "Foreign Event", "Canada"),
            ],
        )
    events, status = load_events(db)
    assert status["status"] == "ok"
    assert len(events) == 1
    assert int(events.iloc[0]["component_count"]) == 2
    assert events.iloc[0]["event_ts_utc"] == pd.Timestamp("2026-07-09T12:30:00Z")


def test_t5_lock_and_post_lock_outcomes(tmp_path: Path) -> None:
    release = pd.Timestamp("2026-07-09T12:30:00Z")
    store = EventPaperStore(tmp_path / "v21")
    store.update(
        _features(release),
        _schedule(release),
        {"connected": True, "last_tick_age_sec": 1.0},
        now=release + pd.Timedelta(minutes=40),
    )
    episode = store.episodes[0]
    assert episode["lean"] == "LONG"
    assert episode["locked_ts_utc"] == (release + pd.Timedelta(minutes=5)).isoformat()
    assert episode["lock_price"] != episode["release_price"]
    assert episode["phase"] == "FINALIZED"
    assert set(episode["outcomes"]) == {"5m", "15m", "30m"}
    assert episode["outcomes"]["5m"]["gross_ticks"] == 5.0
    assert episode["outcomes"]["5m"]["after_cost_ticks"] == 4.5
    assert episode["outcomes"]["5m"]["adverse_ticks"] >= 0.0


def test_short_flow_maps_to_short(tmp_path: Path) -> None:
    release = pd.Timestamp("2026-07-09T12:30:00Z")
    store = EventPaperStore(tmp_path / "v21")
    store.update(
        _features(release, flow=-100.0),
        _schedule(release),
        {"connected": True, "last_tick_age_sec": 1.0},
        now=release + pd.Timedelta(minutes=6),
    )
    assert store.episodes[0]["lean"] == "SHORT"
    assert store.episodes[0]["direction_sign"] == -1.0


def test_stale_feed_blocks_lock(tmp_path: Path) -> None:
    release = pd.Timestamp("2026-07-09T12:30:00Z")
    store = EventPaperStore(tmp_path / "v21")
    store.update(
        _features(release),
        _schedule(release),
        {"connected": True, "last_tick_age_sec": 90.0},
        now=release + pd.Timedelta(minutes=6),
    )
    episode = store.episodes[0]
    assert episode["locked_ts_utc"] is None
    assert episode["quality"]["feed_stale_at_lock"] is True


def test_incomplete_first5_is_no_signal(tmp_path: Path) -> None:
    release = pd.Timestamp("2026-07-09T12:30:00Z")
    features = _features(release).drop(
        index=[release + pd.Timedelta(minutes=4), release + pd.Timedelta(minutes=5)]
    )
    store = EventPaperStore(tmp_path / "v21")
    store.update(
        features,
        _schedule(release),
        {"connected": True, "last_tick_age_sec": 1.0},
        now=release + pd.Timedelta(minutes=6),
    )
    episode = store.episodes[0]
    assert episode["quality"]["incomplete_first5"] is True
    assert episode["lean"] == "NO_SIGNAL"


def test_episode_dedup_and_append_once(tmp_path: Path) -> None:
    release = pd.Timestamp("2026-07-09T12:30:00Z")
    store = EventPaperStore(tmp_path / "v21")
    args = (
        _features(release),
        _schedule(release),
        {"connected": True, "last_tick_age_sec": 1.0},
    )
    store.update(*args, now=release + pd.Timedelta(minutes=40))
    store.update(*args, now=release + pd.Timedelta(minutes=41))
    assert len(store.episodes) == 1
    log = pd.read_csv(store.log_path)
    assert len(log) == 1


def test_store_is_isolated() -> None:
    text = str(STORE_DIR).replace("\\", "/")
    assert text.endswith("workspace/analysis/aggression_v21_live")
    assert "aggression_v2_live" not in text
    assert "aggression_v3_live" not in text


def test_missing_schedule_payload_is_graceful(tmp_path: Path) -> None:
    store = EventPaperStore(tmp_path / "v21")
    payload = store.payload(
        pd.DataFrame(),
        {"status": "missing"},
        {"connected": False, "last_tick_age_sec": None},
        now=pd.Timestamp("2026-07-09T12:00:00Z"),
    )
    assert payload["events_db"]["status"] == "missing"
    assert payload["upcoming"] == []
    assert payload["active"] is None


def test_event_flow_api_shape(monkeypatch) -> None:
    payload = {
        "success": True,
        "strategy_status": "PROVISIONAL / RESEARCH-ONLY",
        "feed": {"connected": False},
        "events_db": {"status": "ok"},
        "upcoming": [],
        "active": None,
        "recent": [],
        "stats": {},
    }

    class FakeEvents:
        def payload(self, *_args, **_kwargs):
            return payload

    monkeypatch.setattr(app_module, "_ensure_live", lambda: None)
    monkeypatch.setattr(app_module, "_evaluate_once", lambda: (pd.DataFrame(), pd.DataFrame(), {}, {}))
    monkeypatch.setattr(app_module, "EVENTS", FakeEvents())
    monkeypatch.setattr(app_module.SCHEDULE, "snapshot", lambda: (pd.DataFrame(), {"status": "ok"}))
    monkeypatch.setattr(app_module.LIVE_BUFFER, "status", lambda: {"connected": False})
    client = app_module.app.test_client()
    response = client.get("/api/event_flow")
    assert response.status_code == 200
    body = response.get_json()
    assert body["strategy_status"] == "PROVISIONAL / RESEARCH-ONLY"
    assert set(("upcoming", "active", "recent", "stats")).issubset(body)
