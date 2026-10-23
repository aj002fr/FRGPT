"""Synthetic tests for X0 QC replay (canonical Q1 + Globex Q3 last bound)."""

from datetime import datetime, timezone

from scripts.move_exhaustion_v2.data.x0_qc_replay import replay_checkpoint_qc


def test_replay_marks_canonical_q1_pass_and_globex_q3(tmp_path):
    checkpoint = {
        "holdout_opened": False,
        "builder_sha256": "old",
        "days": {
            "2024-01-02": {
                "status": "built",
                "qc": {
                    "qc_pass": False,
                    "research_eligible": False,
                    "checks": {
                        "Q1": {
                            "passed": False,
                            "reason": "non-monotonic per-file source order: x.csv",
                            "n_violations": 1,
                        },
                        "Q2": {"passed": True},
                        "Q3": {
                            "passed": False,
                            "skipped": False,
                            "first_delta_minutes": 0.0,
                            "last_delta_minutes": -59.9,
                            "last_ts": "2024-01-02T22:59:00+00:00",
                            "n_gap_violations": 0,
                            "reason": "first/last event outside 30-minute session-bound tolerance",
                        },
                        "Q4": {"passed": True},
                        "Q5": {"passed": False},
                        "Q6": {"passed": True},
                    },
                },
            }
        },
    }
    path = tmp_path / "build_state.json"
    import json

    path.write_text(json.dumps(checkpoint), encoding="utf-8")
    result = replay_checkpoint_qc(path)
    updated = json.loads(path.read_text(encoding="utf-8"))
    day = updated["days"]["2024-01-02"]
    assert day["qc"]["checks"]["Q1"]["passed"] is True
    assert day["qc"]["checks"]["Q3"]["passed"] is True
    assert day["research_eligible"] is True
    assert day["qc_pass"] is False  # Q5 review flag still fails all-six
    assert result["n_research_eligible"] == 1
    assert updated["holdout_opened"] is False
    assert datetime.fromisoformat(updated["qc_replay"]["replayed_at_utc"]).tzinfo is timezone.utc
