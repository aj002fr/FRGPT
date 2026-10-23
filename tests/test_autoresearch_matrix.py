from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.autoresearch.fixed_env.matrix import CandidateRows, FrozenMatrix
from scripts.autoresearch.fixed_env.preregister import digest_file


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(tmp_path: Path) -> tuple[FrozenMatrix, Path]:
    root = tmp_path / "data"
    version = root / "versions" / "v1"
    shard = root / "shards" / "features" / "2025-01-02.parquet"
    index = pd.date_range("2025-01-01T23:00:00Z", periods=3, freq="min")
    frame = pd.DataFrame(
        {
            "price": [110.0, 110.01, 110.02],
            "et_date": ["2025-01-02"] * 3,
            "minutes_since_open": [0, 1, 2],
            "session_sin": [0.0, 0.1, 0.2],
            "session_cos": [1.0, 0.9, 0.8],
            "x": [1.0, float("nan"), 3.0],
        },
        index=index,
    )
    shard.parent.mkdir(parents=True)
    frame.to_parquet(shard)
    version.mkdir(parents=True)
    matrix = version / "tbbo_feature_matrix.parquet"
    frame.to_parquet(matrix)
    manifest = root / "manifests" / "dates" / "2025-01-02.json"
    write_json(
        manifest,
        {
            "artifacts": {"features": str(shard)},
            "checksums": {"features": digest_file(shard)},
            "front_selection": {
                "prior_volume_table": [
                    {"root": "TY", "candidates": [{"contract": "TYH5"}]}
                ]
            },
        },
    )
    write_json(
        version / "manifest.json",
        {
            "matrix_sha256": digest_file(matrix),
            "date_manifests": [{
                "trading_date": "2025-01-02", "manifest": str(manifest),
                "checksums": {"features": digest_file(shard)},
            }],
        },
    )
    split = tmp_path / "split.json"
    event = tmp_path / "event.json"
    write_json(split, {"discovery_dates": ["2025-01-02"], "validation_dates": [], "historical_quarantine_dates": []})
    write_json(event, {"blackout_minutes": {"before": 10, "after": 30}, "events": []})
    return FrozenMatrix(
        version, split, event, expected_matrix_hash=digest_file(matrix),
    ), shard


def test_matrix_adapter_separates_candidate_and_evaluator_data(tmp_path: Path) -> None:
    adapter, _ = fixture(tmp_path)
    loaded = adapter.load(
        "discovery", ["formation_ts", "x"], evaluator_inputs=["x"],
    )
    assert isinstance(loaded.candidate_rows, CandidateRows)
    assert loaded.dates == ("2025-01-02",)
    assert loaded.candidate_rows[1]["x"] is None
    assert "price" not in loaded.candidate_rows[0]
    assert "x" in loaded.evaluator_frame
    assert str(loaded.evaluator_frame.index.dtype) == "datetime64[ns, UTC]"
    assert list(loaded.evaluator_frame["session"]) == ["ASIA"] * 3
    with pytest.raises(PermissionError):
        adapter.load("discovery", ["formation_ts", "x"], dates=["2025-01-03"])


def test_matrix_adapter_rejects_changed_shard(tmp_path: Path) -> None:
    adapter, shard = fixture(tmp_path)
    shard.write_bytes(shard.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="checksum"):
        adapter.load("discovery", ["formation_ts", "x"])
