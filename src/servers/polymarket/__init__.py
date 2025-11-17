"""
Polymarket server tools.

Imports all tool modules to trigger @register_tool decorators.
"""

# Import tool modules to register them with MCP discovery
from . import search_markets
from . import get_history
from . import get_price_history

__all__ = ['search_markets', 'get_history', 'get_price_history']
