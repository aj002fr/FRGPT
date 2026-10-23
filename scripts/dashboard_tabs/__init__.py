"""
Dashboard Tabs Module
---------------------
Self-contained tab modules for the unified dashboard.
Each tab module generates its own HTML and JavaScript.
"""

from . import preevent_tab, postevent_tab, adhoc_tab, weekmatch_tab, polymarket_scenarios_tab, impulse_tab

__all__ = ['preevent_tab', 'postevent_tab', 'adhoc_tab', 'weekmatch_tab', 'polymarket_scenarios_tab', 'impulse_tab']







