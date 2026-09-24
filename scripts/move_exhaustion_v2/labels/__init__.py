"""Outcome labels and path metrics for Move Exhaustion V2 (PROGRAM.md §4.2)."""

from .barriers import (
    CONTINUATION,
    OSCILLATORY_UNRESOLVED,
    REVERSAL,
    UNRESOLVED_AMBIGUOUS,
    assign_labels,
)
from .path_metrics import compute_path_metrics

__all__ = [
    "REVERSAL",
    "CONTINUATION",
    "OSCILLATORY_UNRESOLVED",
    "UNRESOLVED_AMBIGUOUS",
    "assign_labels",
    "compute_path_metrics",
]
