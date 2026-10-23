from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.autoresearch.fixed_env.approval import create_approval, verify_approval
from scripts.autoresearch.fixed_env.candidate_runner import run_candidate, target_free_rows
from scripts.autoresearch.fixed_env.data_access import contained_path
from scripts.autoresearch.fixed_env.evaluator import fit_logit, transform_logit
from scripts.autoresearch.fixed_env.preregister import (
    canonical_spec_hash,
    effective_spec_hash,
    freeze_json,
    verify_file,
)
from scripts.autoresearch.schemas import (
    DescriptiveSpecV2,
    ExecutionContractV1,
    PredictiveExperimentSpecV2,
    RunIdentity,
    SchemaError,
    load_registry,
    safe_load_yaml,
    validate_candidate_rows,
    validate_status_transition,
)

ROOT = Path(__file__).parents[1]
REGISTRY = ROOT / "scripts" / "autoresearch" / "registry" / "hypotheses.yaml"
EXPERIMENTS = ROOT / "scripts" / "autoresearch" / "registry" / "experiments"
EXECUTION_CONTRACTS = (
    ROOT / "scripts" / "autoresearch" / "registry" / "execution_contracts"
)
H = "a" * 64


def identity(split: str = "confirmation") -> RunIdentity:
    return RunIdentity("E999", "FAM_TEST", split, H, H, H, H)


def test_hypothesis_registry_remains_strict_and_separate_from_specs() -> None:
    hypotheses = load_registry(REGISTRY)
    assert [item.hypothesis_id for item in hypotheses] == ["H001", "H002", "H003", "H004"]
    assert [item.status for item in hypotheses] == [
        "exploratory", "proposed", "proposed", "blocked",
    ]
    registry_text = REGISTRY.read_text(encoding="utf-8")
    assert "experiment_id:" not in registry_text
    assert sorted(path.name for path in EXPERIMENTS.glob("*.yaml")) == [
        "D001.yaml", "E001.yaml", "E002.yaml",
    ]


def test_candidate_contract_target_isolation_and_frozen_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_SECRET", "must-not-leak")
    candidate = tmp_path / "candidate.py"
    candidate.write_text(
        "import os\n"
        "def fit(rows):\n"
        "    return {'mean': sum(r['x'] for r in rows) / len(rows)}\n"
        "def transform(rows, frozen_state):\n"
        "    clean = 1.0 if os.getenv('AUTORESEARCH_SECRET') is None else 0.0\n"
        "    return [{'formation_ts': r['formation_ts'], 'cell_id': 'CELL_A', "
        "'feature': (r['x'] - frozen_state['mean']) * clean} for r in rows]\n",
        encoding="utf-8",
    )
    rows = [
        {
            "formation_ts": "2026-01-02T14:00:00Z", "x": 1.0,
            "price_return_1m": 0.1, "target": 1,
        },
        {
            "formation_ts": "2026-01-02T14:01:00Z", "x": 3.0,
            "price_return_1m": -0.1, "target": 0,
        },
    ]
    parsed, state, state_hash = run_candidate(
        candidate, rows, split="discovery",
        source_roots=[tmp_path],
        allowed_input_columns={"formation_ts", "x", "price_return_1m"},
        registered_cells={"CELL_A"},
    )
    assert state == {"mean": 2.0}
    assert len(state_hash) == 64
    assert [row.feature for row in parsed] == [-1.0, 1.0]

    validation, same_state, _ = run_candidate(
        candidate, [rows[0]], split="validation", frozen_state=state,
        source_roots=[tmp_path],
        allowed_input_columns={"formation_ts", "x", "price_return_1m"},
        registered_cells={"CELL_A"},
    )
    assert validation[0].feature == -1.0
    assert same_state == state
    with pytest.raises(SchemaError):
        run_candidate(
            candidate, rows, split="validation", frozen_state=None,
            source_roots=[tmp_path],
            allowed_input_columns={"formation_ts", "x", "price_return_1m"},
            registered_cells={"CELL_A"},
        )
    causal_rows = [
        {**item, "recent_return": item["price_return_1m"]} for item in rows
    ]
    projected = target_free_rows(
        causal_rows, {"formation_ts", "price_return_1m", "recent_return"},
    )
    assert projected[0]["price_return_1m"] == projected[0]["recent_return"] == 0.1
    with pytest.raises(SchemaError):
        target_free_rows(rows, {"formation_ts", "future_return"})
    for evaluator_column in ("target", "forward_return_5m", "realized_pnl", "outcome_range"):
        with pytest.raises(SchemaError):
            target_free_rows(rows, {"formation_ts", evaluator_column})


def test_candidate_output_rejects_leakage_duplicates_and_bad_cells(tmp_path: Path) -> None:
    candidate = tmp_path / "bad.py"
    candidate.write_text(
        "def fit(rows): return {}\n"
        "def transform(rows, state):\n"
        " return [{'formation_ts': rows[0]['formation_ts'], 'cell_id': 'UNKNOWN', "
        "'feature': 1, 'outcome': 1}]\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaError):
        run_candidate(
            candidate, [{"formation_ts": "2026-01-02T14:00:00Z", "x": 1}],
            split="discovery", source_roots=[tmp_path],
            allowed_input_columns={"formation_ts", "x"},
            registered_cells={"CELL_A"},
        )
    with pytest.raises(SchemaError):
        validate_candidate_rows(
            [
                {"formation_ts": "2026-01-02T14:00:00Z", "cell_id": "A", "feature": 1},
                {"formation_ts": "2026-01-02T14:00:00Z", "cell_id": "A", "feature": 2},
            ],
            {"A"},
        )
    with pytest.raises(SchemaError):
        validate_candidate_rows(
            [{"formation_ts": "2026-01-02T14:00:00", "cell_id": "A", "feature": 1}],
            {"A"},
        )
    network_candidate = tmp_path / "network.py"
    network_candidate.write_text(
        "import socket\nsocket.create_connection(('example.com', 80))\n",
        encoding="utf-8",
    )
    with pytest.raises(PermissionError):
        run_candidate(
            network_candidate, [], split="discovery", source_roots=[tmp_path],
            allowed_input_columns=set(), registered_cells={"A"},
        )
    invented = tmp_path / "invented.py"
    invented.write_text(
        "def fit(rows): return {}\n"
        "def transform(rows, state):\n"
        " return [{'formation_ts': '2026-01-02T15:00:00Z', "
        "'cell_id': 'A', 'feature': 1.0}]\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaError, match="absent formation_ts"):
        run_candidate(
            invented, [{"formation_ts": "2026-01-02T14:00:00Z"}],
            split="discovery", source_roots=[tmp_path],
            allowed_input_columns={"formation_ts"}, registered_cells={"A"},
        )
    with pytest.raises(SchemaError, match="must be unique"):
        run_candidate(
            invented,
            [{"formation_ts": "2026-01-02T14:00:00Z"}] * 2,
            split="discovery", source_roots=[tmp_path],
            allowed_input_columns={"formation_ts"}, registered_cells={"A"},
        )


def test_candidate_runner_binds_declared_config_and_contract(tmp_path: Path) -> None:
    candidate = tmp_path / "configured.py"
    candidate.write_text(
        "def fit(rows, config): return {'scale': config['scale']}\n"
        "def transform(rows, state):\n"
        " return [{'formation_ts': r['formation_ts'], "
        "'features': {'value': r['x'] * state['scale']}} for r in rows]\n",
        encoding="utf-8",
    )
    rows = [{"formation_ts": "2026-01-02T14:00:00Z", "x": 2.0}]
    kwargs = {
        "source_roots": [tmp_path],
        "allowed_input_columns": {"formation_ts", "x"},
        "registered_feature_map": {"value": "configured value"},
        "config": {"scale": 3},
        "contract_hash": H,
        "declared_grid": {"scale": [1, 3]},
    }
    parsed, state, state_hash = run_candidate(
        candidate, rows, split="discovery", **kwargs,
    )
    assert parsed[0].features["value"] == 6.0
    assert state["config"] == {"scale": 3}
    assert len(state_hash) == 64
    repeated, same_state, _ = run_candidate(
        candidate, rows, split="validation", frozen_state=state, **kwargs,
    )
    assert repeated == parsed and same_state == state
    with pytest.raises(SchemaError, match="undeclared"):
        run_candidate(candidate, rows, split="discovery", **{**kwargs, "config": {"scale": 4}})


def test_status_transitions_are_explicit_and_terminal() -> None:
    validate_status_transition("hypothesis", "blocked", "proposed")
    validate_status_transition("experiment", "draft", "frozen")
    with pytest.raises(SchemaError):
        validate_status_transition("hypothesis", "superseded", "proposed")
    with pytest.raises(SchemaError):
        validate_status_transition("experiment", "frozen", "draft")


def test_safe_yaml_enforces_file_size_limit(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.yaml"
    oversized.write_text("value: " + "x" * 100, encoding="utf-8")
    with pytest.raises(SchemaError, match="byte limit"):
        safe_load_yaml(oversized, max_bytes=32)


def test_fit_is_discovery_only_and_transform_uses_frozen_state() -> None:
    rows = [{"context": float(index)} for index in range(10)]
    target = [0] * 5 + [1] * 5
    state = fit_logit(rows, target, ["context"], split="discovery")
    assert transform_logit(rows, state)[-1] > transform_logit(rows, state)[0]
    with pytest.raises(PermissionError):
        fit_logit(rows, target, ["context"], split="validation")


def test_hash_freeze_path_containment_and_approval_binding(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    artifact = root / "snapshot.json"
    frozen_hash = freeze_json(artifact, {"b": 2, "a": 1})
    verify_file(artifact, frozen_hash)
    assert contained_path(artifact, [root]) == artifact.resolve()
    with pytest.raises(PermissionError):
        contained_path(tmp_path / "outside.txt", [root], must_exist=False)
    with pytest.raises(FileExistsError):
        freeze_json(artifact, {"a": 9})

    approval = create_approval(
        identity(), primary_claim_hash=H, confirmation_cutoff="2026-07-20",
        date_manifest_hash=H, approver="research-owner",
        approved_at="2026-07-21T16:00:00Z",
    )
    verify_approval(
        approval, identity(), primary_claim_hash=H,
        confirmation_cutoff="2026-07-20", date_manifest_hash=H,
    )
    changed = RunIdentity("E999", "FAM_TEST", "confirmation", "b" * 64, H, H, H)
    with pytest.raises(PermissionError):
        verify_approval(
            approval, changed, primary_claim_hash=H,
            confirmation_cutoff="2026-07-20", date_manifest_hash=H,
        )
    assert json.loads(json.dumps(asdict(approval)))["approver"] == "research-owner"


def test_canonical_spec_hash_excludes_only_its_own_slot() -> None:
    spec = {"hashes": {"spec_hash": "a" * 64}, "outcome": "fixed"}
    first = canonical_spec_hash(spec)
    spec["hashes"]["spec_hash"] = first
    assert canonical_spec_hash(spec) == first
    spec["outcome"] = "changed"
    assert canonical_spec_hash(spec) != first


@pytest.mark.parametrize(
    ("name", "schema"),
    [
        ("E001.yaml", PredictiveExperimentSpecV2),
        ("E002.yaml", PredictiveExperimentSpecV2),
        ("D001.yaml", DescriptiveSpecV2),
    ],
)
def test_frozen_campaign_specs_validate_and_self_bind(name, schema) -> None:
    raw = safe_load_yaml(EXPERIMENTS / name)
    schema.from_dict(raw)
    assert canonical_spec_hash(raw) == raw["hashes"]["spec_hash"]


@pytest.mark.parametrize("name", ["E001.yaml", "E002.yaml", "D001.yaml"])
def test_execution_contracts_are_strict_and_bind_frozen_specs(name: str) -> None:
    raw = safe_load_yaml(EXECUTION_CONTRACTS / name)
    contract = ExecutionContractV1.from_dict(raw)
    spec = safe_load_yaml(EXPERIMENTS / name)
    assert contract.base_spec_hash == spec["hashes"]["spec_hash"]
    first = effective_spec_hash(contract.base_spec_hash, raw)
    assert len(first) == 64
    changed = dict(raw)
    changed["outcome"] = {**raw["outcome"], "tamper": True}
    assert effective_spec_hash(contract.base_spec_hash, changed) != first
    assert isinstance(contract.evaluator, dict)
    assert isinstance(contract.selection, dict)


def test_package_has_no_live_imports_or_process_control() -> None:
    for path in (ROOT / "scripts" / "autoresearch").rglob("*.py"):
        source = path.read_text(encoding="utf-8").lower()
        assert "from scripts.aggression_dashboard" not in source
        assert "import scripts.aggression_dashboard" not in source
        assert "subprocess.popen(" not in source
        assert "os.system(" not in source

