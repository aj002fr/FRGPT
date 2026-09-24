from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from scripts.autoresearch.fixed_env.execution import (
    ExecutionController,
    declared_d001_cells,
    declared_grid,
    evaluate_d001_map,
    evaluate_predictive_cells,
    select_e001,
    select_e002,
)
from scripts.autoresearch.fixed_env.discovery import (
    APPROVED_MANIFEST_NAME,
    _float_keyed_audit,
    authorized_context,
)
from scripts.autoresearch.fixed_env.evaluator import fit_logit, transform_logit
from scripts.autoresearch.fixed_env.results import (
    build_result_bundle,
    publish_result_bundle,
)
from scripts.autoresearch.fixed_env.preregister import canonical_bytes
from scripts.autoresearch.fixed_env.state import StateStore
from scripts.autoresearch.fixed_env.validation import (
    FrozenDiscovery,
    QUARANTINE_SCOPE,
    VALIDATION_SCOPE,
    authorized_execution_context,
    build_execution_payload,
    circular_shift_p_values,
    validation_decision,
)
from scripts.autoresearch.schemas import PreprocessingIdentity, ResultBundleV1, RunIdentity

H = "a" * 64


def test_experiment_specific_discovery_selection_is_deterministic() -> None:
    grid = declared_grid({
        "window_minutes": [15, 30, 60],
        "deadband_ticks": [0.5, 1.0, 2.0],
        "dwell_minutes": [1, 2],
    })
    assert len(grid) == 18
    configs = {str(index): config for index, config in enumerate(grid)}
    effects = {key: [0.01, 0.01, 0.01, -0.001] for key in configs}
    selected = select_e001(effects, configs)
    assert selected.config == {
        "window_minutes": 15, "deadband_ticks": 2.0, "dwell_minutes": 1,
    }
    pooled = {key: 0.0 for key in configs}
    pooled["0"] = 1.0
    assert select_e001(effects, configs, pooled).config == configs["0"]
    e002 = select_e002(
        {
            "fold1": {0.1: 2.0, 1.0: 1.0, 10.0: 3.0},
            "fold2": {0.1: 2.0, 1.0: 0.5, 10.0: 3.0},
        },
        {
            5: [0.01, 0.01, -0.01, 0.01],
            15: [0.02, 0.02, 0.02, -0.01],
            30: [0.01, -0.01, 0.01, 0.01],
        },
    )
    assert e002.config == {
        "ridge_lambda": 1.0, "formation_lookback_minutes": 15,
        "entry_gap_ticks": 2,
    }
    canonical_bytes(asdict(e002))
    assert _float_keyed_audit({0.1: 2.0, 1.0: 1.0}) == {
        "0.1": 2.0, "1": 1.0,
    }
    final = select_e002(
        {"fold1": {0.1: 1.0, 1.0: 2.0}},
        {5: [1, 1, 1, -1], 15: [2, 2, 2, -1], 30: [1, 1, 1, -1]},
        final_mse={0.1: 3.0, 1.0: 1.0},
    )
    assert final.config["ridge_lambda"] == 1.0


def test_discovery_authorization_is_fail_closed(tmp_path: Path) -> None:
    manifest = (
        tmp_path / "workspace" / "analysis" / "aggression_autoresearch_campaign"
        / "manifests" / APPROVED_MANIFEST_NAME
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '{"authorized_scope":"discovery_only","real_outcomes_authorized":false,'
        '"validation_authorized":false}',
        encoding="utf-8",
    )
    with pytest.raises(PermissionError, match="discovery-only"):
        authorized_context(tmp_path, manifest)


def test_logit_freezes_discovery_median_imputation() -> None:
    rows = [{"x": 1.0}, {"x": float("nan")}, {"x": 3.0}, {"x": 4.0}]
    state = fit_logit(rows, [0, 0, 1, 1], ["x"], split="discovery")
    probabilities = transform_logit([{"x": float("nan")}], state)
    assert np.isfinite(probabilities).all()
    assert state.medians == (3.0,)
    normalized = json.loads(json.dumps(asdict(state), allow_nan=False))
    assert isinstance(normalized["medians"], list)


def test_d001_has_exactly_243_noninteractive_cells() -> None:
    cells = declared_d001_cells()
    assert len(cells) == len(set(cells)) == 243
    assert "buysell_ratio|LOW|ASIA|LOW|15" in cells
    assert all(cell.count("|") == 4 for cell in cells)


def test_d001_fixed_four_tick_boundary_and_nomination_cap() -> None:
    records = []
    for month, start_day in ((1, 1), (2, 3)):
        for offset in range(25):
            date = f"2025-{month:02d}-{start_day + offset:02d}"
            for proxy_value, move in ((-1.0, 3 / 64), (2.0, 4 / 64)):
                for _ in range(2):
                    records.append({
                        "date": date, "price": 100.0, "session": "ASIA",
                        "event_blackout": False, "buysell_ratio": proxy_value,
                        "large_trade_share": 0.5, "trade_intensity": 0.5,
                        "realized_vol_context": 0.5,
                        "paths": {
                            "15": [100 + move], "30": [100 + move],
                            "60": [100 + move],
                        },
                    })
    result = evaluate_d001_map(
        records,
        {
            "buysell_ratio": [0.0, 1.0], "large_trade_share": [0.3, 0.7],
            "trade_intensity": [0.3, 0.7], "realized_vol": [0.3, 0.7],
        },
        first_half_end="2025-01-31",
    )
    assert len(result["cells"]) == 243
    assert len(result["nominations"]) <= 2
    assert result["nominations"][0]["cell_id"].startswith("buysell_ratio|LOW")
    canonical_bytes(result)


def test_predictive_evaluation_planted_effect_and_null() -> None:
    labels = [
        f"2025-{month:02d}-{day:02d}"
        for month in range(7, 12) for day in range(1, 7)
    ]
    dates = np.repeat(labels, 4)
    target = np.tile([0, 0, 1, 1], 30)
    baseline = np.full(len(target), 0.5)
    augmented = np.where(target == 1, 0.75, 0.25)
    mask = np.ones(len(target), dtype=bool)
    planted = evaluate_predictive_cells(
        dates, target, baseline, augmented, {"ALL": mask},
        denominator=3, repetitions=199, seed_key="planted",
        shift_p_values={"ALL": 0.01}, utility_threshold=0.2,
    )
    assert planted["denominator"] == 3
    assert planted["cells"][0]["decision"] == "screen_pass"
    canonical_bytes(planted)
    null = evaluate_predictive_cells(
        dates, target, baseline, baseline, {"ALL": mask},
        denominator=3, repetitions=199, seed_key="null",
        shift_p_values={"ALL": 1.0}, utility_threshold=0.2,
    )
    assert null["cells"][0]["decision"] != "screen_pass"
    with pytest.raises(PermissionError):
        ExecutionController(actual_run=False).evaluate()


def _publish_once(root: Path) -> tuple[str, str, str]:
    store = StateStore(root / "state.sqlite", output_roots=[root])
    identity = RunIdentity("E999", "FAM_TEST", "validation", H, H, H, H, H)
    reservation = store.reserve(identity)
    bundle, payload_hash, envelope_hash = build_result_bundle(
        {"cells": [{"cell_id": "ALL", "effect": 0.01}]},
        identity=identity, base_spec_hash=H, effective_spec_hash=H,
        code_manifest_hash=H, decision="screen_pass", headline_cell="ALL",
    )
    receipt_hash = publish_result_bundle(
        store, reservation.run_id, root / "result.json", bundle,
        payload_hash=payload_hash, envelope_hash=envelope_hash,
    )
    assert store.get_run(reservation.run_id)["output_hash"] == envelope_hash
    return payload_hash, envelope_hash, receipt_hash


def test_result_bundle_hashes_are_deterministic_and_one_shot(tmp_path: Path) -> None:
    first = _publish_once(tmp_path / "one")
    second = _publish_once(tmp_path / "two")
    assert first == second
    store = StateStore(tmp_path / "one" / "state.sqlite", output_roots=[tmp_path / "one"])
    identity = RunIdentity("E999", "FAM_TEST", "validation", H, H, H, H, H)
    with pytest.raises(RuntimeError, match="already COMPLETED"):
        store.reserve(identity)


def test_actual_controller_dispatches_outcomes_and_seals_state(tmp_path: Path) -> None:
    controller = ExecutionController(actual_run=True)
    outcomes = controller.construct_outcomes(
        "E001",
        [{"close_path": [100.01, 99.99], "anchor_price": 100.0}],
    )
    np.testing.assert_array_equal(outcomes, [1])
    store = StateStore(tmp_path / "state.sqlite", output_roots=[tmp_path])
    identity = PreprocessingIdentity(
        "E999", "FAM_TEST", "discovery", H, H, H, H,
    )
    reservation = controller.seal_discovery(store, identity, H)
    assert store.get_run(reservation.run_id)["state_hash"] == H


@pytest.mark.parametrize("scope", [VALIDATION_SCOPE, QUARANTINE_SCOPE])
def test_validation_and_quarantine_authorization_refuse_missing_pair(
    tmp_path: Path, scope: str,
) -> None:
    manifests = (
        tmp_path / "workspace" / "analysis" / "aggression_autoresearch_campaign"
        / "manifests"
    )
    manifests.mkdir(parents=True)
    with pytest.raises(PermissionError, match="both required"):
        authorized_execution_context(
            tmp_path, manifests / "future.json", manifests / "future_record.json",
            scope=scope,
        )


def test_date_session_circular_shift_falsification_is_deterministic() -> None:
    dates = np.repeat([f"2025-{month:02d}-{day:02d}"
                       for month in range(9, 12) for day in range(1, 11)], 4)
    sessions = np.repeat("RATES_RTH", len(dates))
    minute = 60 * 1_000_000_000
    timestamps = np.tile(np.arange(4) * 30 * minute, len(dates) // 4)
    target = np.tile([0, 1, 0, 1], len(dates) // 4)
    baseline = np.full(len(dates), 0.5)
    feature = target.astype(float)
    augmented = np.where(target == 1, 0.8, 0.2)
    kwargs = {
        "coefficient": 1.0,
        "scale": 1.0,
        "cell_masks": {"ALL": np.ones(len(dates), dtype=bool)},
        "repetitions": 199,
        "seed_key": "fixed-shift",
    }
    first = circular_shift_p_values(
        dates, sessions, timestamps, target, baseline, augmented, feature, **kwargs,
    )
    second = circular_shift_p_values(
        dates, sessions, timestamps, target, baseline, augmented, feature, **kwargs,
    )
    assert first == second
    assert 0 < first["ALL"] <= 1
    sparse_sessions = sessions.copy()
    sparse_sessions[0] = "ASIA"
    sparse = circular_shift_p_values(
        dates, sparse_sessions, timestamps, target, baseline, augmented, feature,
        **kwargs,
    )
    assert 0 < sparse["ALL"] <= 1


def test_validation_decisions_preserve_predictive_failure_and_non_actionability() -> None:
    passed = {"cells": [{"cell_id": "ALL_ELIGIBLE", "decision": "screen_pass"}]}
    assert validation_decision(
        "E002", passed, {"status": "non_actionable"},
    ) == "non_actionable"
    failed = {"cells": [{"cell_id": "ALL_ELIGIBLE", "decision": "not_confirmed"}]}
    assert validation_decision(
        "E002", failed, {"status": "non_actionable"},
    ) == "not_confirmed"
    e001 = {"cells": [{"cell_id": "ALL", "decision": "screen_pass"}]}
    assert validation_decision("E001", e001) == "screen_pass"
    limited = {"cells": [
        {"cell_id": "ALL", "decision": "positive_but_trivial", "effect": 0.001},
        {"cell_id": "OVERNIGHT", "decision": "screen_pass", "effect": 0.006},
    ]}
    assert validation_decision("E001", limited) == "screen_pass_limited"


def test_quarantine_payload_is_transform_only_and_non_evidentiary() -> None:
    frozen = FrozenDiscovery(
        ResultBundleV1({}, None, H, H, H, H), {}, {"immutable": True}, H, H,
    )
    payload = build_execution_payload(
        experiment_id="E001", split="quarantine", frozen=frozen,
        anchor_count=100, date_count=25, repetitions=9_999,
        evaluation={"cells": []}, tradability=None, decision="screen_pass",
        validation_decision_value="not_confirmed",
    )
    assert payload["transform_only"] is True
    assert payload["status"] == payload["evidentiary_status"] == "non_evidentiary"
    assert payload["validation_decision_unchanged"] == "not_confirmed"
    assert payload["decision_overwrite"] is False
    assert "decision" not in payload
