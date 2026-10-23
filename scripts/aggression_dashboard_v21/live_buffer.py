"""V2.1-local import surface for the stateless TBBO buffer class.

The class definition is reused from V2, but V2.1 creates its own instance and
does not import or share V2 application runtime state.
"""

from scripts.aggression_dashboard_v2.live_buffer import LiveTbboBuffer

__all__ = ["LiveTbboBuffer"]
