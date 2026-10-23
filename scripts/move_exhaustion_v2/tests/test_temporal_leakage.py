"""Temporal-leakage scaffold.

Runnable now: mechanical consistency between frozen configs that supports the
causality design (A6). The feature/episode leakage tests are scaffolded and
activate as soon as the corresponding modules exist — they are the enforcement
point for 'max input timestamp <= decision timestamp' and 'episode builder is
flow-free'.
"""

import importlib
from pathlib import Path

import pytest

PKG = "scripts.move_exhaustion_v2"


def _try_import(module: str):
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError:
        return None


# ---------------------------------------------------------------------------
# Runnable now
# ---------------------------------------------------------------------------

def test_decision_time_definition_present_in_program(pkg_root):
    """PROGRAM.md must retain the causality sentence verbatim (frozen surface)."""
    program = (pkg_root / "research" / "PROGRAM.md").read_text(encoding="utf-8")
    assert "measured strictly before and independently of the future outcome" in program
    assert "max input timestamp ≤ decision timestamp" in program


def test_no_random_split_vocabulary_in_frozen_config(splits_cfg):
    text = str(splits_cfg).lower()
    assert "random" not in text, "splits must be chronological; no random splitting"
    assert "shuffle" not in text


# ---------------------------------------------------------------------------
# Scaffold — activates when Phase 1/2 code lands
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    _try_import(f"{PKG}.features.leakage") is None,
    reason="Phase 2 not implemented yet: scripts/move_exhaustion_v2/features/leakage.py must "
    "expose assert_causal(features_df, decision_ts_col) enforcing max input ts <= decision ts",
)
def test_feature_leakage_guard_on_synthetic_frame():
    leakage = importlib.import_module(f"{PKG}.features.leakage")
    import pandas as pd

    good = pd.DataFrame(
        {"decision_ts": pd.to_datetime(["2024-01-02 10:00:00"]),
         "max_input_ts": pd.to_datetime(["2024-01-02 09:59:59"])}
    )
    leakage.assert_causal(good, decision_ts_col="decision_ts", max_input_ts_col="max_input_ts")

    bad = pd.DataFrame(
        {"decision_ts": pd.to_datetime(["2024-01-02 10:00:00"]),
         "max_input_ts": pd.to_datetime(["2024-01-02 10:00:01"])}
    )
    with pytest.raises(Exception):
        leakage.assert_causal(bad, decision_ts_col="decision_ts", max_input_ts_col="max_input_ts")


@pytest.mark.skipif(
    _try_import(f"{PKG}.episodes.builder") is None,
    reason="Phase 1 not implemented yet: scripts/move_exhaustion_v2/episodes/builder.py must "
    "declare CONSUMED_COLUMNS so this test can prove the episode definition is flow-free",
)
def test_episode_builder_is_flow_free():
    builder = importlib.import_module(f"{PKG}.episodes.builder")
    consumed = {c.lower() for c in builder.CONSUMED_COLUMNS}
    flow_markers = {"agg", "flow", "add_", "cxl", "cancel", "size", "volume", "depth"}
    offenders = {c for c in consumed for marker in flow_markers if marker in c}
    assert not offenders, f"episode builder consumes flow/book columns: {sorted(offenders)}"


@pytest.mark.skipif(
    _try_import(f"{PKG}.data.mbp1_store_builder") is None,
    reason="Phase 0 not implemented yet: store builder must expose PRE_EVENT_DEPTH_COLUMNS "
    "derived from the PREVIOUS row's book state (never the current row's post-event state)",
)
def test_pre_event_depth_is_previous_row_state():
    store = importlib.import_module(f"{PKG}.data.mbp1_store_builder")
    assert set(store.PRE_EVENT_DEPTH_COLUMNS) == {"pre_bid_sz", "pre_ask_sz", "pre_bid_px", "pre_ask_px"}
