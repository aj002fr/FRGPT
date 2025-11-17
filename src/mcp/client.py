"""Simple MCP client for code-mode tool execution."""

from typing import Any, Dict, Optional
import logging
from .discovery import get_tool, discover_tools

logger = logging.getLogger(__name__)


class MCPClient:
    """
    Simple MCP client for direct Python function calls.
    
    No network protocol - just local tool execution.
    """
    
    def __init__(self, auto_discover: bool = True):
        """
        Initialize MCP client.
        
        Args:
            auto_discover: Automatically discover tools on init
        """
        self.tools: Dict[str, Any] = {}
        
        if auto_discover:
            self.discover_tools()
    
    def discover_tools(self) -> None:
        """Discover all available tools."""
        from pathlib import Path
        
        # Get servers directory
        servers_path = Path(__file__).parent.parent / "servers"
        
        self.tools = discover_tools(servers_path)
        logger.info(f"Discovered {len(self.tools)} tools: {list(self.tools.keys())}")
    
    def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Call a tool by name.
        
        Args:
            name: Tool name
            arguments: Tool arguments
            
        Returns:
            Tool result
            
        Raises:
            ValueError: If tool not found
            Exception: If tool execution fails
        """
        logger.debug(f"Calling tool: {name} with args: {arguments}")
        
        # Get tool function
        tool_func = get_tool(name)
        if not tool_func:
            raise ValueError(f"Tool not found: {name}")
        
        # Call tool
        try:
            if arguments:
                result = tool_func(**arguments)
            else:
                result = tool_func()
            
            logger.debug(f"Tool {name} completed successfully")
            return result
            
        except Exception as e:
            logger.error(f"Tool {name} failed: {e}")
            raise
    
    def list_tools(self) -> list[str]:
        """List available tools."""
        return list(self.tools.keys())
    
    def get_tool_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Get information about a tool."""
        return self.tools.get(name)


