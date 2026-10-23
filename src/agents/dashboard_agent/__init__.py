"""
Dashboard Agent - Wraps the orchestrator to produce dashboard configurations.

This agent:
1. Receives a natural language query
2. Runs it through the OrchestratorAgent
3. Classifies the intent and selects appropriate widgets
4. Returns a DashboardConfig JSON that the frontend can render
"""

from .run import DashboardAgent

__all__ = ["DashboardAgent"]
