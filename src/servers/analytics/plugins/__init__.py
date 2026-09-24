"""
Analytics plugins auto-discovery.

This module automatically discovers and imports plugin packages from the plugins directory.
Each plugin is a subdirectory containing an __init__.py file with @register_tool decorated functions.

Plugin Discovery Rules:
- Subdirectories starting with '_' are ignored (e.g., _example is loaded but for reference only)
- Each plugin must have an __init__.py that imports from src.mcp.discovery
- Plugins are imported in alphabetical order
- Import failures are logged but don't crash the system

Adding a Plugin via Git Submodule:
    cd src/servers/analytics/plugins
    git submodule add git@github.com:yourteam/my-plugin.git my_plugin

See plugins/README.md for complete developer documentation.
"""

import importlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PLUGINS_DISCOVERED = False


def discover_plugins() -> list[str]:
    """
    Discover and import all plugins from the plugins directory.
    
    Returns:
        List of successfully loaded plugin names
    """
    global _PLUGINS_DISCOVERED
    
    plugins_dir = Path(__file__).parent
    loaded_plugins = []
    
    # Get all subdirectories (excluding __pycache__ and hidden dirs)
    for plugin_dir in sorted(plugins_dir.iterdir()):
        # Skip non-directories
        if not plugin_dir.is_dir():
            continue
        
        # Skip __pycache__ and hidden directories
        if plugin_dir.name.startswith('__') or plugin_dir.name.startswith('.'):
            continue
        
        # Skip directories starting with _ (reserved for examples/internal)
        # But still try to import _example for reference
        if plugin_dir.name.startswith('_') and plugin_dir.name != '_example':
            continue
        
        # Check for __init__.py
        init_file = plugin_dir / '__init__.py'
        if not init_file.exists():
            logger.debug(f"Skipping {plugin_dir.name}: no __init__.py")
            continue
        
        # Try to import the plugin
        module_name = f"src.servers.analytics.plugins.{plugin_dir.name}"
        try:
            importlib.import_module(module_name)
            loaded_plugins.append(plugin_dir.name)
            logger.info(f"Loaded plugin: {plugin_dir.name}")
        except Exception as e:
            logger.warning(f"Failed to load plugin {plugin_dir.name}: {e}")
    
    _PLUGINS_DISCOVERED = True
    
    if loaded_plugins:
        logger.info(f"Discovered {len(loaded_plugins)} plugins: {loaded_plugins}")
    
    return loaded_plugins


# Auto-discover plugins on module import
_loaded = discover_plugins()

