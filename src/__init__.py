"""Code-Mode MCP Market Agents - Source Package."""

__version__ = "1.0.0"

# Main components
from .agents import MarketDataAgent, ConsumerAgent
from .mcp import MCPClient
from .bus import Manifest, write_atomic, read_json

__all__ = ['MarketDataAgent', 'ConsumerAgent', 'MCPClient', 'Manifest', 'write_atomic', 'read_json']

