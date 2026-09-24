"""Focused tests for the isolated V2.6 C018 and feedback surface."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v26.alerts import STORE_DIR as ALERT_STORE
from scripts.aggression_dashboard_v26.c018_context import (
    C018_LABEL,
    STRATEGY_STATUS,
    T3_RANGE_CUTOFF_TICKS,
    TICK_SIZE,
    annotate_c018_flags,
)
from scripts.aggression_dashboard_v26.c018_store import C018Store, STORE_DIR as C018_STORE
from scripts.aggression_dashboard_v26.feedback_store import FeedbackStore
from scripts.aggression_dashboard_v26.lifo_store import LifoStore, STORE_DIR as LIFO_STORE


def _event_panel(*, include_forming: bool = True) -> pd.DataFrame:
    prices = [100.0 + 7 * TICK_SIZE, 100.0 + 12 * TICK_SIZE, 100.0]
    prices.extend([100.0 + 7 * TICK_SIZE] * 27)
    prices.append(100.0 + 8 * TICK_SIZE)
    if include_forming:
        prices.append(100.0 + 8 * TICK_SIZE)
    index = pd.date_range("2026-08-17 13:00:00", periods=len(prices), freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "price": prices,
            "residual": np.zeros(len(prices)),
            "residual_z": np.zeros(len(prices)),
            "trade_intensity": np.ones(len(prices)),
        },
        index=index,
    )


def test_v26_paths_and_labels_are_isolated() -> None:
    for store in (ALERT_STORE, C018_STORE, LIFO_STORE):
        normalized = str(store).replace("\\", "/")
        assert normalized.endswith("workspace/analysis/aggression_v26_live")
        assert "aggression_v22_live" not in normalized
        assert "aggression_v2_live" not in normalized.replace("aggression_v26_live", "")
    assert STRATEGY_STATUS == "PROVISIONAL / RESEARCH-ONLY / NON-EXECUTABLE"
    assert C018_LABEL == (
        "PROVISIONAL / C018-FADE-LEAN / NOT OFFLINE C018 / NON-EXECUTABLE"
    )


def test_c018_cutoff_is_frozen_and_fires_fade_down() -> None:
    assert T3_RANGE_CUTOFF_TICKS == 10.5
    annotated = annotate_c018_flags(_event_panel(include_forming=False))
    event = annotated.iloc[-1]
    assert event["c018_range_30m_ticks"] == 12.0
    assert event["c018_move_direction"] == "UP"
    assert event["c018_fade_lean"] == "FADE DOWN"
    assert bool(event["c018_fire"]) is True


def test_c018_down_move_fires_fade_up() -> None:
    panel = _event_panel(include_forming=False)
    panel["price"] = 200.0 - panel["price"]
    event = annotate_c018_flags(panel).iloc[-1]
    assert event["c018_range_30m_ticks"] == 12.0
    assert event["c018_move_direction"] == "DOWN"
    assert event["c018_fade_lean"] == "FADE UP"
    assert bool(event["c018_fire"]) is True


def test_c018_store_excludes_forming_bar_and_deduplicates(tmp_path: Path) -> None:
    store = C018Store(store_dir=tmp_path)
    store.update(_event_panel(include_forming=False))
    assert store.episodes == []

    payload = store.update(_event_panel(include_forming=True))
    assert len(store.episodes) == 1
    assert store.episodes[0]["fade_lean"] == "FADE DOWN"
    assert store.episodes[0]["phase"] == "LOCKED"
    assert payload["context"]["active"] is True

    store.update(_event_panel(include_forming=True))
    assert len(store.episodes) == 1


def test_v26_lifo_store_keeps_rule_and_adds_common_status(tmp_path: Path) -> None:
    index = pd.date_range("2026-08-17 13:00:00", periods=90, freq="1min", tz="UTC")
    z_value = np.concatenate([[0.2] * 30, [1.5] * 60])
    panel = pd.DataFrame(
        {
            "price": 110.0 + np.cumsum(np.where(np.arange(90) % 2 == 0, 0.005, -0.005)),
            "residual": z_value * 0.01,
            "residual_z": z_value,
            "trade_intensity": np.concatenate([[8.0] * 30, [1.0] * 60]),
        },
        index=index,
    )
    store = LifoStore(store_dir=tmp_path)
    payload = store.update(panel)
    assert store.episodes
    assert all(ep["strategy_status"] == STRATEGY_STATUS for ep in store.episodes)
    assert all("human_correct" in ep for ep in store.episodes)
    assert payload["strategy_status"] == STRATEGY_STATUS
    assert payload["lifo_friendly_label"].startswith("PROVISIONAL / LIFO-FRIENDLY")


def test_feedback_audit_and_last_grade_wins(tmp_path: Path) -> None:
    c018 = C018Store(store_dir=tmp_path / "episodes")
    c018.update(_event_panel(include_forming=True))
    episode_id = c018.episodes[0]["episode_id"]
    audit = FeedbackStore(path=tmp_path / "feedback.jsonl")

    first_ts = "2026-08-17T14:00:00+00:00"
    audit.record(
        episode_id=episode_id,
        alert_kind="c018",
        correct=True,
        note="first",
        previous_correct=None,
        previous_status=None,
        feedback_ts=first_ts,
    )
    c018.set_feedback(episode_id, True, note="first", feedback_ts=first_ts)

    second_ts = "2026-08-17T14:01:00+00:00"
    previous = c018.get_episode(episode_id)
    audit.record(
        episode_id=episode_id,
        alert_kind="c018",
        correct=False,
        note="regraded",
        previous_correct=previous["human_correct"],
        previous_status=previous["human_feedback_status"],
        feedback_ts=second_ts,
    )
    latest = c018.set_feedback(episode_id, False, note="regraded", feedback_ts=second_ts)

    lines = (tmp_path / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["correct"] is True
    assert json.loads(lines[1])["previous_correct"] is True
    assert latest["human_correct"] is False
    assert latest["human_feedback_note"] == "regraded"


def test_daily_feedback_stats_use_et_and_skip_stays_pending(tmp_path: Path) -> None:
    lifo = LifoStore(store_dir=tmp_path / "lifo")
    lifo.episodes = [
        {
            "episode_id": "lifo_a",
            "alert_kind": "lifo",
            "et_date": "2026-08-17",
            "lock_ts_utc": "2026-08-17T13:00:00+00:00",
            "human_correct": True,
            "human_feedback_status": "graded",
        },
        {
            "episode_id": "lifo_b",
            "alert_kind": "lifo",
            "et_date": "2026-08-17",
            "lock_ts_utc": "2026-08-17T13:10:00+00:00",
            "human_correct": None,
            "human_feedback_status": "skipped",
        },
    ]
    c018_rows = [
        {
            "episode_id": "c018_a",
            "alert_kind": "c018",
            "et_date": "2026-08-17",
            "lock_ts_utc": "2026-08-17T13:20:00+00:00",
            "human_correct": False,
            "human_feedback_status": "graded",
        }
    ]
    store = FeedbackStore(path=tmp_path / "feedback.jsonl")
    payload = store.daily_payload(
        lifo.snapshot_episodes(),
        c018_rows,
        now=datetime(2026, 8, 17, 15, tzinfo=timezone.utc),
    )
    assert payload["stats"]["lifo"] == {
        "issued": 2,
        "pending_grade": 1,
        "graded": 1,
        "marked_correct": 1,
        "marked_wrong": 0,
        "hit_rate_graded": 1.0,
    }
    assert payload["stats"]["c018"]["marked_wrong"] == 1
    assert payload["stats"]["c018"]["hit_rate_graded"] == 0.0
    assert payload["pending_prompts"] == []


def test_feedback_api_regrades_existing_episode(tmp_path: Path, monkeypatch) -> None:
    from scripts.aggression_dashboard_v26 import app as app_module

    assert app_module.PORT == 5062
    c018 = C018Store(store_dir=tmp_path / "c018")
    c018.update(_event_panel(include_forming=True))
    episode_id = c018.episodes[0]["episode_id"]
    monkeypatch.setattr(app_module, "C018", c018)
    monkeypatch.setattr(app_module, "LIFO", LifoStore(store_dir=tmp_path / "lifo"))
    monkeypatch.setattr(
        app_module,
        "FEEDBACK",
        FeedbackStore(path=tmp_path / "feedback.jsonl"),
    )

    client = app_module.app.test_client()
    first = client.post(
        "/api/feedback",
        json={"episode_id": episode_id, "alert_kind": "c018", "correct": True},
    )
    assert first.status_code == 200
    assert first.get_json()["episode"]["human_correct"] is True

    second = client.post(
        "/api/feedback",
        json={"episode_id": episode_id, "alert_kind": "c018", "correct": False},
    )
    body = second.get_json()
    assert second.status_code == 200
    assert body["episode"]["human_correct"] is False
    assert body["feedback"]["stats"]["c018"]["marked_wrong"] == 1
    assert len((tmp_path / "feedback.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_frontend_element_references_exist() -> None:
    package = Path(__file__).resolve().parents[1] / "scripts" / "aggression_dashboard_v26"
    html = (package / "templates" / "index.html").read_text(encoding="utf-8")
    js = (package / "static" / "dashboard.js").read_text(encoding="utf-8")
    referenced = set(re.findall(r'byId\("([\w-]+)"\)', js))
    missing = sorted(element_id for element_id in referenced if f'id="{element_id}"' not in html)
    assert missing == []
    assert 'GRADE_DIALOG.setAttribute("open", "");' in js
    assert "GRADE_DIALOG.show();" not in js
    assert "showModal" not in js
