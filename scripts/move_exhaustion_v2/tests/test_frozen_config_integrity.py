"""Mechanical integrity of the frozen scientific surface.

Fails if any frozen config, contract preregistration section, or PROGRAM.md
scientific section changed without a manifest regeneration (which itself
requires a user-approved governance addendum).
"""

from freeze_manifest import collect_entries, read_manifest, verify


def test_manifest_exists_and_matches():
    problems = verify()
    assert not problems, "\n".join(problems)


def test_all_expected_objects_pinned():
    keys = set(read_manifest().keys())
    for required in [
        "config/frozen/episodes.yaml",
        "config/frozen/labels.yaml",
        "config/frozen/splits.yaml",
        "config/frozen/gates.yaml",
        "config/frozen/data_store.yaml",
        "research/PROGRAM.md#sections0-9",
    ]:
        assert required in keys, f"manifest missing pin for {required}"
    contract_pins = [k for k in keys if k.startswith("experiments/X")]
    assert len(contract_pins) == 11, (
        f"expected 11 contract pins (X0-X10), found {len(contract_pins)}: {sorted(contract_pins)}"
    )


def test_episode_config_schema(episodes_cfg):
    ep = episodes_cfg["episode"]
    assert ep["B_ticks"]["primary"] == 8
    assert ep["B_ticks"]["grid"] == [4, 6, 8, 12]
    assert ep["x_ticks"]["primary"] == 2
    assert ep["x_ticks"]["grid"] == [1, 2, 3, 4]
    assert episodes_cfg["instrument"]["tick_size"] == 0.015625
    dd = episodes_cfg["dedupe"]
    assert dd["one_active_episode_per_direction"] is True
    assert dd["block_scope"] == "per_direction"
    assert dd["episode_universe_index"] == ["B_ticks", "x_ticks", "H_minutes"]
    assert dd["label_h_must_match_universe_h"] is True
    assert dd["explicit_user_approval_utc"] == "2026-08-12"
    assert dd["min_anchor_separation_minutes_wall_clock"] == 5


def test_label_config_schema(labels_cfg):
    lab = labels_cfg["labels"]
    assert lab["classes"] == ["REVERSAL", "CONTINUATION", "OSCILLATORY_UNRESOLVED"]
    assert lab["all_three_classes_are_targets"] is True
    assert labels_cfg["primary"] == {"R_ticks": 4, "C_ticks": 4, "H_minutes": 15, "H_clock": "wall"}
    assert labels_cfg["labels"]["censored_label"] == "CENSORED_INCOMPLETE_HORIZON"
    assert lab["universe_h_coupling"].startswith("label H_minutes must exactly match")
    assert lab["internal_no_event_policy"].startswith("on an X0 Q3-passing day")
    assert lab["known_halt_policy"].startswith("if the forward H intersects")
    assert lab["explicit_user_approval_utc"] == "2026-08-12"
    grid = labels_cfg["grid"]
    assert [2, 2] in grid["RC_ticks"] and [3, 5] in grid["RC_ticks"] and [5, 3] in grid["RC_ticks"]
    assert grid["H_minutes"] == [5, 15, 30]
    guard = labels_cfg["cell_guardrails"]
    assert guard["min_resolved_episodes_discovery"] == 100
    assert guard["max_unresolved_share"] == 0.80
    assert guard["max_one_class_share"] == 0.90


def test_review_gate_eligibility_is_explicit(data_store_cfg):
    gates = data_store_cfg["qc_gates"]
    assert gates["review_gate_eligibility"].startswith(
        "Q4/Q5/Q6 failure excludes the day"
    )
    assert gates["review_gate_explicit_user_approval_utc"] == "2026-08-12"


def test_gate_config_schema(gates_cfg):
    assert gates_cfg["bootstrap"]["draws"] == 10000
    assert gates_cfg["bootstrap"]["clustering"] == "date"
    assert gates_cfg["multiplicity"]["method"] == "benjamini_hochberg"
    assert gates_cfg["multiplicity"]["q"] == 0.10
    floors = gates_cfg["sample_floors"]
    assert floors["min_episodes_per_cell"] == 50
    assert floors["min_distinct_dates_per_cell"] == 8
    sesoi = gates_cfg["sesoi"]
    assert sesoi["conditional_probability_lift_pp"] == 8.0
    assert sesoi["model_comparison_brier"] == 0.005
    stab = gates_cfg["stability_gates"]
    assert stab["feature_psi_train_to_eval_max"] == 0.25
    assert stab["max_single_date_share_of_aggregate_effect"] == 0.40
    # Scope decision is binding: no cost gates anywhere in the config.
    assert "cost" not in str(gates_cfg).lower().replace("no cost gates", "")


def test_open_question_references_resolve(pkg_root, episodes_cfg, labels_cfg, gates_cfg, data_store_cfg, splits_cfg):
    """Every OQ-# referenced from frozen config must exist in OPEN_QUESTIONS.md."""
    import re

    oq_doc = (pkg_root / "research" / "OPEN_QUESTIONS.md").read_text(encoding="utf-8")
    defined = set(re.findall(r"## (OQ-\d+)", oq_doc))
    referenced = set()
    for cfg in (episodes_cfg, labels_cfg, gates_cfg, data_store_cfg, splits_cfg):
        referenced |= set(re.findall(r"OQ-\d+", str(cfg)))
    missing = referenced - defined
    assert not missing, f"frozen config references undefined open questions: {sorted(missing)}"
