"""Tool discovery for code-mode MCP."""

import importlib
import inspect
import logging
from pathlib import Path
from typing import Dict, Callable, Any, Optional, List

logger = logging.getLogger(__name__)

# Global tool registry
_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}
_DISCOVERY_DONE: bool = False


def _get_default_servers_path() -> Path:
    """Get the default servers directory path."""
    return Path(__file__).parent.parent / "servers"


def register_tool(
    name: str,
    description: str = "",
    input_schema: Optional[Dict[str, Any]] = None,
    soft_dependencies: Optional[List[Dict[str, Any]]] = None,
    agent: Optional[str] = None
):
    """
    Decorator to register a tool.
    
    Args:
        name: Tool name
        description: Tool description
        input_schema: JSON schema for inputs
        soft_dependencies: Optional list of soft dependencies that enhance the tool.
            Each dependency is a dict with:
            - param_name: Target parameter to fill (e.g., "events")
            - source_agent: Agent to call (e.g., "eventdata_puller_agent")
            - source_tool: Tool to invoke (e.g., "search_events")
            - param_mapping: Dict mapping tool params to context values
              Use "$context.field" for dynamic values, literals for static
            - output_path: Path in result to extract (default: whole result)
            - required: If True, fail if dependency fails (default: False)
        agent: Optional agent name this tool belongs to. If not specified,
            the agent is auto-detected from the module path (e.g., 
            src.servers.analytics.* -> analytics_agent)
    
    Example:
        @register_tool(
            "generate_candlestick_chart",
            "Generate OHLC chart",
            soft_dependencies=[{
                "param_name": "events",
                "source_agent": "eventdata_puller_agent",
                "source_tool": "search_events",
                "param_mapping": {
                    "start_date": "$context.time_range.start",
                    "end_date": "$context.time_range.end",
                    "country": "US",
                    "importance": "high"
                },
                "output_path": "events"
            }],
            agent="analytics_agent"
        )
        def generate_candlestick_chart(...) -> dict:
            ...
    """
    def decorator(func: Callable) -> Callable:
        _TOOL_REGISTRY[name] = {
            "function": func,
            "description": description or func.__doc__ or "",
            "input_schema": input_schema or {},
            "soft_dependencies": soft_dependencies or [],
            "module": func.__module__,
            "agent": agent
        }
        return func
    return decorator


def discover_tools(servers_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """
    Discover all registered tools.
    
    Args:
        servers_path: Path to servers directory (optional, uses default if None)
        
    Returns:
        Dictionary of {tool_name: tool_info}
    """
    global _DISCOVERY_DONE
    
    # Use default path if not provided
    if servers_path is None:
        servers_path = _get_default_servers_path()
    else:
        servers_path = Path(servers_path)
    
    # Dynamically import modules to trigger @register_tool decorators
    if servers_path.exists():
        for server_dir in servers_path.iterdir():
            if server_dir.is_dir() and not server_dir.name.startswith('_'):
                try:
                    module_name = f"src.servers.{server_dir.name}"
                    importlib.import_module(module_name)
                except ImportError:
                    pass  # Skip if can't import
    
    _DISCOVERY_DONE = True
    return _TOOL_REGISTRY.copy()


def get_tool(name: str) -> Optional[Callable]:
    """
    Get tool function by name.
    
    Args:
        name: Tool name
        
    Returns:
        Tool function or None if not found
    """
    tool_info = _TOOL_REGISTRY.get(name)
    if tool_info:
        return tool_info["function"]
    return None


def get_tool_info(name: str) -> Optional[Dict[str, Any]]:
    """
    Get tool information.
    
    Args:
        name: Tool name
        
    Returns:
        Tool info dictionary or None
    """
    return _TOOL_REGISTRY.get(name)


def list_tools() -> list[str]:
    """List all registered tool names."""
    return list(_TOOL_REGISTRY.keys())


def get_all_tools() -> Dict[str, Dict[str, Any]]:
    """
    Get all registered tools with their metadata.
    
    Auto-discovers tools if not already done.
    
    Returns:
        Copy of the tool registry
    """
    global _DISCOVERY_DONE
    if not _DISCOVERY_DONE:
        logger.info("Auto-discovering tools...")
        discover_tools(_get_default_servers_path())
        _DISCOVERY_DONE = True
        logger.info(f"Discovered {len(_TOOL_REGISTRY)} tools")
    return _TOOL_REGISTRY.copy()


def reset_discovery() -> None:
    """
    Reset tool discovery state.
    
    Clears the tool registry and discovery flag to force re-discovery.
    Useful when tool definitions have changed and need to be reloaded.
    """
    global _TOOL_REGISTRY, _DISCOVERY_DONE
    _TOOL_REGISTRY.clear()
    _DISCOVERY_DONE = False
    logger.info("Tool discovery state reset")


