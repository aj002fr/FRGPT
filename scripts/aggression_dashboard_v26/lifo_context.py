"""V2.6 LIFO context; the V2.2 H5 rule and labels remain frozen."""

from scripts.aggression_dashboard_v22.lifo_context import (
    DECLUSTER_STRIDE_MIN,
    HORIZON_MIN,
    LIFO_FRIENDLY_LABEL,
    LIFO_FRIENDLY_RESEARCH,
    REGIME_CUTOFFS,
    STRATEGY_STATUS as V22_STRATEGY_STATUS,
    annotate_lifo_flags,
    decluster_fire_mask,
    latest_lifo_context,
    score_forward_regime,
)

STRATEGY_STATUS = "PROVISIONAL / RESEARCH-ONLY / NON-EXECUTABLE"

__all__ = [
    "DECLUSTER_STRIDE_MIN",
    "HORIZON_MIN",
    "LIFO_FRIENDLY_LABEL",
    "LIFO_FRIENDLY_RESEARCH",
    "REGIME_CUTOFFS",
    "STRATEGY_STATUS",
    "V22_STRATEGY_STATUS",
    "annotate_lifo_flags",
    "decluster_fire_mask",
    "latest_lifo_context",
    "score_forward_regime",
]
