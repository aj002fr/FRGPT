"""Dry protocol controller; it validates and describes work but never executes it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ProtocolStep:
    name: str
    split: str
    experiment_id: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class CampaignPlan:
    campaign_id: str
    dry_run: bool
    steps: tuple[ProtocolStep, ...]
    experiment_ids: tuple[str, ...]


class DryProtocolController:
    """Schema-loose orchestration for protocol inspection and later adaptation."""

    _SPLITS = ("discovery", "validation", "confirmation")

    def __init__(self, campaign: Mapping[str, Any]) -> None:
        self._campaign = dict(campaign)

    def plan(self) -> CampaignPlan:
        campaign_id = str(self._campaign.get("campaign_id", "")).strip()
        if not campaign_id:
            raise ValueError("campaign_id is required")
        experiments = self._campaign.get("experiments", ())
        if not isinstance(experiments, Sequence) or isinstance(experiments, (str, bytes)):
            raise ValueError("experiments must be a sequence of loose mappings")
        steps: list[ProtocolStep] = []
        experiment_ids: list[str] = []
        for experiment in experiments:
            if not isinstance(experiment, Mapping):
                raise ValueError("each experiment must be a mapping")
            experiment_id = str(experiment.get("experiment_id", "")).strip()
            if not experiment_id or experiment_id in experiment_ids:
                raise ValueError("experiment IDs must be present and unique")
            experiment_ids.append(experiment_id)
            splits = tuple(experiment.get("splits", self._SPLITS))
            if not splits or any(split not in self._SPLITS for split in splits):
                raise ValueError("unknown protocol split")
            for split in splits:
                steps.extend(self._steps_for(experiment_id, split))
        return CampaignPlan(campaign_id, True, tuple(steps), tuple(experiment_ids))

    @staticmethod
    def _steps_for(experiment_id: str, split: str) -> tuple[ProtocolStep, ...]:
        prefix = f"{experiment_id}:{split}"
        return (
            ProtocolStep(
                "construct_common_anchors", split, experiment_id,
                ("date_manifest", "source_coverage", "session_bounds"),
                (f"{prefix}:common_anchors",),
            ),
            ProtocolStep(
                "candidate_transform", split, experiment_id,
                (f"{prefix}:common_anchors", "frozen_candidate_state"),
                (f"{prefix}:candidate_cells",),
            ),
            ProtocolStep(
                "evaluator_outcomes", split, experiment_id,
                (f"{prefix}:common_anchors", "evaluator_prices"),
                (f"{prefix}:outcomes",),
            ),
            ProtocolStep(
                "paired_statistics", split, experiment_id,
                (f"{prefix}:candidate_cells", f"{prefix}:outcomes"),
                (f"{prefix}:statistics",),
            ),
        )

    def run(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise PermissionError(
            "DryProtocolController cannot load data or execute market experiments"
        )


def build_campaign_plan(campaign: Mapping[str, Any]) -> CampaignPlan:
    return DryProtocolController(campaign).plan()


ProtocolController = DryProtocolController
