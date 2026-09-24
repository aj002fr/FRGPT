"""Synthetic tests for the X1 price-only baseline runner."""

import json

import pandas as pd
import pytest

from scripts.move_exhaustion_v2.episodes.builder import CONSUMED_COLUMNS
from scripts.move_exhaustion_v2.x1_run import X1RunError, run_x1

from conftest import _extend_coverage, _mk_frame


def _x0_pass_tree(tmp_path, discovery, validation, holdout):
    analysis = tmp_path / "analysis"
    store = tmp_path / "store"
    experiments = analysis / "experiments" / "X0_test"
    experiments.mkdir(parents=True)
    store.mkdir()
    (experiments / "conclusion.json").write_text(
        json.dumps(
            {
                "experiment": "X0",
                "verdict": "PASS",
                "holdout_opened": False,
            }
        ),
        encoding="utf-8",
    )
    (analysis / "split_manifest.json").write_text(
        json.dumps(
            {
                "holdout_opened": False,
                "splits": {
                    "discovery": {"dates": discovery},
                    "validation": {"dates": validation},
                    "final_holdout": {"dates": holdout, "locked": True},
                },
            }
        ),
        encoding="utf-8",
    )
    return analysis, store


def _write_day(store, trading_date: str, mids):
    frame = _mk_frame(mids, trading_date=trading_date, step_s=10)
    t_star = frame["ts_event"].iloc[-1]
    frame = pd.concat(
        [frame, _extend_coverage(frame, t_star + pd.Timedelta(minutes=15), mids[-1])],
        ignore_index=True,
    )
    frame = frame[list(CONSUMED_COLUMNS)]
    frame.to_parquet(store / f"{trading_date}.parquet", index=False)


def test_x1_runs_discovery_validation_and_skips_holdout(tmp_path):
    analysis, store = _x0_pass_tree(
        tmp_path,
        discovery=["2024-01-02"],
        validation=["2025-01-02"],
        holdout=["2026-01-02"],
    )
    mids = [100.0, 100.0625, 100.125]
    _write_day(store, "2024-01-02", mids)
    _write_day(store, "2025-01-02", mids)
    (store / "2026-01-02.parquet").write_bytes(b"not-a-parquet")
    result = run_x1(
        analysis_root=analysis,
        store_root=store,
        run_id="synth",
        primary_only=True,
    )
    assert result["holdout_opened"] is False
    assert result["n_dates_used"] == 2
    conclusion = json.loads(
        (analysis / "experiments" / "X1_synth" / "conclusion.json").read_text(
            encoding="utf-8"
        )
    )
    assert conclusion["holdout_opened"] is False
    assert conclusion["verdict"] is None
    assert "key_number" in conclusion


def test_x1_refuses_without_x0_pass(tmp_path):
    analysis, store = _x0_pass_tree(
        tmp_path, discovery=["2024-01-02"], validation=[], holdout=[]
    )
    payload = json.loads(
        (analysis / "experiments" / "X0_test" / "conclusion.json").read_text(
            encoding="utf-8"
        )
    )
    payload["verdict"] = "FAIL"
    (analysis / "experiments" / "X0_test" / "conclusion.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    with pytest.raises(X1RunError, match="X0 PASS"):
        run_x1(analysis_root=analysis, store_root=store, run_id="x", primary_only=True)


def test_x1_resume_keeps_checkpointed_days(tmp_path):
    analysis, store = _x0_pass_tree(
        tmp_path,
        discovery=["2024-01-02"],
        validation=["2025-01-02"],
        holdout=["2026-01-02"],
    )
    mids = [100.0, 100.0625, 100.125]
    _write_day(store, "2024-01-02", mids)
    _write_day(store, "2025-01-02", mids)
    run_x1(
        analysis_root=analysis,
        store_root=store,
        run_id="synth",
        primary_only=True,
    )
    splits = json.loads(
        (analysis / "split_manifest.json").read_text(encoding="utf-8")
    )
    splits["splits"]["discovery"]["dates"] = ["2024-01-02", "2024-01-03"]
    (analysis / "split_manifest.json").write_text(
        json.dumps(splits), encoding="utf-8"
    )
    _write_day(store, "2024-01-03", mids)
    run_x1(
        analysis_root=analysis,
        store_root=store,
        run_id="synth",
        primary_only=True,
    )
    labeled = pd.read_parquet(
        analysis / "experiments" / "X1_synth" / "labeled_B8_x2.parquet",
        columns=["trading_date"],
    )
    assert set(labeled["trading_date"].astype(str)) == {
        "2024-01-02",
        "2024-01-03",
        "2025-01-02",
    }


def test_x1_stop_when_sufficient_skips_validation(tmp_path, monkeypatch):
    import scripts.move_exhaustion_v2.x1_run as x1_mod

    monkeypatch.setattr(x1_mod, "MIN_RESOLVED_DISCOVERY", 1)
    analysis, store = _x0_pass_tree(
        tmp_path,
        discovery=["2024-01-02", "2024-01-03", "2024-01-04"],
        validation=["2025-01-02"],
        holdout=["2026-01-02"],
    )
    mids = [100.0, 100.0625, 100.125]
    for day in ["2024-01-02", "2024-01-03", "2024-01-04", "2025-01-02"]:
        _write_day(store, day, mids)
    (store / "2026-01-02.parquet").write_bytes(b"not-a-parquet")
    result = run_x1(
        analysis_root=analysis,
        store_root=store,
        run_id="synth",
        primary_only=True,
        stop_when_sufficient=True,
    )
    assert result["holdout_opened"] is False
    metrics = json.loads(
        (analysis / "experiments" / "X1_synth" / "metrics.json").read_text(
            encoding="utf-8"
        )
    )
    assert metrics["stop_when_sufficient"] is True
    assert metrics["n_validation_dates"] == 0
    labeled_path = analysis / "experiments" / "X1_synth" / "labeled_B8_x2.parquet"
    if labeled_path.is_file():
        labeled = pd.read_parquet(labeled_path, columns=["trading_date"])
        dates = set(labeled["trading_date"].astype(str))
        assert "2025-01-02" not in dates
        assert "2026-01-02" not in dates
        assert metrics["n_dates_used"] <= 3
