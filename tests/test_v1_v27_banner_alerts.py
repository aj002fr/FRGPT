"""Tests for the isolated V1 banner / V2.7 alert SQLite log."""

from __future__ import annotations

from pathlib import Path

from scripts.aggression_v1_v27_banner_alerts.store import (
    latest_v1_banner,
    record_v1_banner_change,
    record_v27_alert,
)


def test_banner_change_writes_only_on_state_change(tmp_path: Path) -> None:
    db = tmp_path / "banner_alerts.sqlite"
    first = record_v1_banner_change(
        {"state": "QUIET", "status": "READY", "as_of": "2026-09-03T16:00:00+00:00", "aggress": {"z": 0.1, "trend": "STEADY"}, "implied_passive": {"z": 0.2, "trend": "STEADY"}, "transition": {}},
        db_path=db,
    )
    same = record_v1_banner_change(
        {"state": "QUIET", "status": "READY", "as_of": "2026-09-03T16:01:00+00:00"},
        db_path=db,
    )
    second = record_v1_banner_change(
        {"state": "AGGRESS_DOMINANT", "status": "READY", "as_of": "2026-09-03T16:02:00+00:00", "transition": {"shifted": True, "from_state": "QUIET", "minutes_in_state": 0}},
        db_path=db,
    )
    assert first is not None
    assert same is None
    assert second is not None
    latest = latest_v1_banner(db_path=db)
    assert latest is not None
    assert latest["state"] == "AGGRESS_DOMINANT"
    assert latest["prev_state"] == "QUIET"


def test_v27_alert_stamps_latest_banner(tmp_path: Path) -> None:
    db = tmp_path / "banner_alerts.sqlite"
    record_v1_banner_change(
        {"state": "IMPLIED_PASSIVE_DOMINANT", "status": "READY", "as_of": "2026-09-03T16:00:00+00:00"},
        db_path=db,
    )
    episode = {
        "id": "2026-09-03T16:05:00+00:00_WATCH",
        "opened_ts": "2026-09-03T16:05:00+00:00",
        "direction": "SHORT_RICH",
        "state": "WATCH",
        "open_price": 110.5,
        "open_residual_z": 1.8,
        "session_ticks": 3.0,
    }
    entry_id = record_v27_alert(episode, "ENTRY", db_path=db)
    episode["closed_ts"] = "2026-09-03T16:10:00+00:00"
    episode["exit_reason"] = "Z_RESET"
    episode["close_price"] = 110.48
    episode["dir_pnl_trade_ticks"] = 1.0
    episode["minutes_held"] = 5.0
    exit_id = record_v27_alert(episode, "EXIT", db_path=db)
    again = record_v27_alert(episode, "EXIT", db_path=db)
    assert entry_id is not None
    assert exit_id is not None
    assert again == exit_id
    banner = latest_v1_banner("2026-09-03T16:05:00+00:00", db_path=db)
    assert banner is not None
    assert banner["state"] == "IMPLIED_PASSIVE_DOMINANT"
