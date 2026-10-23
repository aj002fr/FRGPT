"""V2.4-local LIFO flag surface.

Reuses the frozen V2.2/V1.2 definition so the paper book's LIFO gate is
identical to the one the offline harness scored.
"""

from scripts.aggression_dashboard_v22.lifo_context import (
    LARGE_ABS_Z,
    LIFO_FRIENDLY_LABEL,
    annotate_lifo_flags,
)

__all__ = ["LARGE_ABS_Z", "LIFO_FRIENDLY_LABEL", "annotate_lifo_flags"]
