from __future__ import annotations

import pytest

from scripts.autoresearch.fixed_env.campaign import DryProtocolController, build_campaign_plan


def test_campaign_controller_only_builds_a_dry_protocol_plan() -> None:
    plan = build_campaign_plan(
        {
            "campaign_id": "C001",
            "experiments": [
                {"experiment_id": "E001", "splits": ["discovery", "validation"]},
                {"experiment_id": "E002", "splits": ["discovery"]},
            ],
        }
    )
    assert plan.dry_run is True
    assert plan.experiment_ids == ("E001", "E002")
    assert len(plan.steps) == 12
    assert {step.name for step in plan.steps} == {
        "construct_common_anchors",
        "candidate_transform",
        "evaluator_outcomes",
        "paired_statistics",
    }

    controller = DryProtocolController(
        {"campaign_id": "C001", "experiments": [{"experiment_id": "E001"}]}
    )
    with pytest.raises(PermissionError, match="cannot load data"):
        controller.run()


def test_campaign_controller_rejects_duplicate_experiment_ids() -> None:
    with pytest.raises(ValueError, match="unique"):
        build_campaign_plan(
            {
                "campaign_id": "C001",
                "experiments": [{"experiment_id": "E001"}, {"experiment_id": "E001"}],
            }
        )
