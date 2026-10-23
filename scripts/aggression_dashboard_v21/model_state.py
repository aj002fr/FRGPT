"""V2.1-local Ridge model instance type.

Only the class definition and read-only feature matrix are reused. V2.1 owns
its own instance and does not share V2 application state.
"""

from scripts.aggression_dashboard_v2.model_state import RidgeModelState

__all__ = ["RidgeModelState"]
