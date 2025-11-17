"""MCP client and tool discovery (code-mode)."""

from .client import MCPClient
from .discovery import discover_tools, register_tool

__all__ = ['MCPClient', 'discover_tools', 'register_tool']


