from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.autoresearch.fixed_env.candidate_runner import run_candidate
from scripts.autoresearch.fixed_env.matrix import CandidateRows, MatrixSlice
from scripts.autoresearch.fixed_env.outcomes import (
    e001_bounded_4t_30m,
    e002_close_first_passage,
)
from scripts.autoresearch.fixed_env.preregister import digest_file
from scripts.autoresearch_lab.lab_evidence import evidence_payload
from scripts.autoresearch_lab.lab_holdout import holdout_availability
from scripts.autoresearch_lab.lab_integrity import verify_single
from scripts.autoresearch_lab.lab_matrix import DiscoveryOnlyMatrix
from scripts.autoresearch_lab.lab_outcomes import (
    bounded_excursion,
    convergence_first_passage,
)
from scripts.autoresearch_lab.lab_prereg import build_spec, exposure_precheck
from scripts.autoresearch_lab.lab_readers import (
    PROJECT_ROOT,
    STATE_DB,
    load_ledger,
)

H = "a" * 64


class DummyMatrix:
    publication = {"matrix_sha256": H}

    def assigned_dates(self, split: str):
        assert split == "discovery"
        return ("2025-01-02",)

    def load(self, split, allowed_inputs, *, evaluator_inputs=()):
        index = pd.DatetimeIndex(["2025-01-02T12:00:00Z"])
        frame = pd.DataFrame({"price": [100.0], "mom_5m": [0.0]}, index=index)
        evaluator = pd.DataFrame(
            {
                "price": [100.0], "et_date": ["2025-01-02"],
                "minutes_since_open": [60.0], "session_sin": [0.0],
                "session_cos": [1.0], "session": ["RATES_RTH"],
                "event_blackout": [False], "roll_flag": [False],
            },
            index=index,
        )
        return MatrixSlice(split, ("2025-01-02",), CandidateRows(frame, allowed_inputs), evaluator)


def test_discovery_matrix_refuses_every_holdout_split() -> None:
    matrix = DiscoveryOnlyMatrix(DummyMatrix())
    for split in ("validation", "quarantine", "confirmation"):
        with pytest.raises(PermissionError, match="discovery-only"):
            matrix.load(split, ("formation_ts", "mom_5m"))
    loaded = matrix.load("discovery", ("formation_ts", "mom_5m"))
    assert loaded.split == "discovery"


def test_parameterized_outcomes_reproduce_frozen_definitions() -> None:
    rng = np.random.default_rng(17)
    timestamps = np.arange(1, 31, dtype=np.int64) * 60_000_000_000
    for _ in range(100):
        path = 100 + np.cumsum(rng.normal(0, 0.02, len(timestamps)))
        gap = float(rng.choice([-1, 1]) * 0.125)
        assert bounded_excursion(path, 100.0) == e001_bounded_4t_30m(path, 100.0)
        assert convergence_first_passage(
            timestamps, path, 100.0, formation_gap=gap
        ) == e002_close_first_passage(
            timestamps, path, 100.0, formation_gap=gap
        )


def test_hash_bound_outcomes_file_remains_byte_identical() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "workspace/analysis/aggression_autoresearch_campaign/"
         "manifests/execution_manifest_v5.json").read_text(encoding="utf-8")
    )
    relative = "scripts/autoresearch/fixed_env/outcomes.py"
    assert digest_file(PROJECT_ROOT / relative) == manifest["code_files"][relative]


def test_custom_candidate_forbidden_source_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "candidate.py"
    source.write_text(
        "import requests\n"
        "def fit(rows, config): return {}\n"
        "def transform(rows, state): requests.get('http://example.com'); return []\n",
        encoding="utf-8",
    )
    rows = [{"formation_ts": "2025-01-02T12:00:00Z", "mom_5m": 0.0}]
    with pytest.raises(PermissionError, match="forbidden candidate source"):
        run_candidate(
            source,
            rows,
            split="discovery",
            source_roots=[tmp_path],
            allowed_input_columns={"formation_ts", "mom_5m"},
            registered_feature_map={"custom_feature": "test"},
            config={"scale": 1.0},
            contract_hash=H,
            declared_grid={"scale": [1.0]},
        )


def test_integrity_helper_detects_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_text('{"value":1}', encoding="utf-8")
    expected = digest_file(artifact)
    assert verify_single(artifact, expected)["status"] == "pass"
    artifact.write_text('{"value":2}', encoding="utf-8")
    assert verify_single(artifact, expected)["status"] == "fail"


def test_ledger_reader_uses_real_campaign_database() -> None:
    ledger = load_ledger()
    assert Path(ledger["database"]).name == "campaign.sqlite"
    assert len(ledger["runs"]) == 7
    assert len(ledger["exposures"]) == 250
    assert STATE_DB.name != "campaign_state.sqlite"


def test_exposure_precheck_rejects_spent_family_target() -> None:
    spent = exposure_precheck(
        hypothesis_family="FAM_RESIDUAL_OSCILLATION",
        target="BOUNDED_4T_30M",
        holdout_split="validation",
    )
    assert spent["available"] is False
    assert len(spent["exposed_rows"]) == 101
    fresh = exposure_precheck(
        hypothesis_family="FAM_RESIDUAL_OSCILLATION",
        target="NEW_PREREGISTERED_TARGET",
        holdout_split="validation",
    )
    assert fresh["available"] is True
    assert fresh["exposed_rows"] == []


def test_read_paths_never_write_campaign_state() -> None:
    before = digest_file(STATE_DB)
    evidence_payload()
    holdout_availability()
    after = digest_file(STATE_DB)
    assert before == after
    lab_root = PROJECT_ROOT / "scripts" / "autoresearch_lab"
    source = "\n".join(path.read_text(encoding="utf-8") for path in lab_root.glob("*.py"))
    assert "StateStore(" not in source


def test_real_bundle_projection_reproduces_campaign_decisions() -> None:
    payload = evidence_payload()
    assert payload["integrity"]["status"] == "pass"
    assert len(payload["experiments"]["D001"]["cells"]) == 243
    assert len(payload["experiments"]["D001"]["nominations"]) == 2
    assert payload["experiments"]["E001"]["validation"]["decision"] == "not_confirmed"
    assert payload["experiments"]["E002"]["validation"]["decision"] == "not_confirmed"
    for experiment in ("E001", "E002"):
        for cell in payload["experiments"][experiment]["validation"]["evaluation"]["cells"]:
            assert len(cell["gates"]) == 8


def test_preregistration_draft_validates_and_hash_binds() -> None:
    result = {
        "search_digest": "b" * 64,
        "candidate_source_sha256": H,
        "allowed_inputs": ["formation_ts", "mom_5m"],
        "feature_map": {"custom_feature": "Exploratory feature"},
        "config": {"scale": 1.0},
        "treatment_feature": "custom_feature",
        "outcome": {
            "name": "bounded_excursion",
            "horizon_minutes": 30,
            "params": {"threshold_ticks": 4.0},
        },
        "slice": {},
        "baseline_model": {"feature_names": ["realized_vol"]},
    }
    spec, contract = build_spec(
        {
            "experiment_id": "EABC01",
            "hypothesis_id": "H002",
            "claim": "The frozen candidate incrementally predicts a bounded path.",
        },
        result,
    )
    assert contract["base_spec_hash"] == spec["hashes"]["spec_hash"]
    assert contract["candidate"]["source_sha256"] == H


def test_holdout_panel_has_no_execution_path() -> None:
    payload = holdout_availability()
    assert payload["execution_endpoint_present"] is False
    assert payload["all_holdouts_guarded"] is True
    assert all(not row["can_execute"] for row in payload["registered"])
