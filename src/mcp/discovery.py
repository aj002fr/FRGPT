"""Tool discovery for code-mode MCP."""

import importlib
import inspect
from pathlib import Path
from typing import Dict, Callable, Any, Optional


# Global tool registry
_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}


def register_tool(
    name: str,
    description: str = "",
    input_schema: Optional[Dict[str, Any]] = None
):
    """
    Decorator to register a tool.
    
    Args:
        name: Tool name
        description: Tool description
        input_schema: JSON schema for inputs
    
    Example:
        @register_tool("run_query", "Execute SQL query")
        def run_query(sql: str) -> dict:
            ...
    """
    def decorator(func: Callable) -> Callable:
        _TOOL_REGISTRY[name] = {
            "function": func,
            "description": description or func.__doc__ or "",
            "input_schema": input_schema or {},
            "module": func.__module__
        }
        return func
    return decorator


def discover_tools(servers_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """
    Discover all registered tools.
    
    Args:
        servers_path: Path to servers directory (optional, for dynamic discovery)
        
    Returns:
        Dictionary of {tool_name: tool_info}
    """
    # If servers_path provided, dynamically import modules
    if servers_path:
        servers_path = Path(servers_path)
        if servers_path.exists():
            # Import all server modules to trigger registration
            for server_dir in servers_path.iterdir():
                if server_dir.is_dir() and not server_dir.name.startswith('_'):
                    try:
                        # Import server package
                        module_name = f"src.servers.{server_dir.name}"
                        importlib.import_module(module_name)
                    except ImportError:
                        pass  # Skip if can't import
    
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


