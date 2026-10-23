"""Lazy tool loading for orchestrator agent."""

import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, Any, List, Set, Optional, Callable

from src.mcp.discovery import discover_tools, get_tool, get_tool_info
from .config import is_agent_enabled

logger = logging.getLogger(__name__)


# Module path prefix to agent name mapping
# Used for auto-detecting agent assignment when not explicitly specified
MODULE_TO_AGENT = {
    "src.servers.analytics": "analytics_agent",
    "src.servers.distro_analytics": "analytics_agent",
    "src.servers.marketdata": "market_data_agent",
    "src.servers.tradingeconomics": "eventdata_puller_agent",
    "src.servers.polymarket": "polymarket_agent",
    "src.servers.runner": "runner_agent",
}


class ToolLoader:
    """
    Lazy tool loader for context-efficient tool discovery.
    
    Loads only the tools needed for specific agents rather than
    all tools upfront, reducing context size for each dependency path.
    
    Architecture:
    - AGENT_TOOL_MAP is dynamically built from tool registry metadata
    - Tools declare their agent via @register_tool(agent="...") or are auto-detected from module path
    - AI reasoning in Planner Stage 2 intelligently selects a subset based on task needs
    - This class handles the actual loading of selected tools from the registry
    
    The AI-powered selection ensures:
    - Only necessary tools are loaded per dependency path
    - Tool parameters are intelligently mapped from agent params
    - Context size is minimized for efficient execution
    - New tools are auto-discovered without manual registration
    """
    
    # Fallback static mapping for core tools (used if auto-discovery fails)
    # New tools should use @register_tool(agent="...") instead of adding here
    _FALLBACK_AGENT_TOOL_MAP = {
        "market_data_agent": ["run_query", "run_multi_date_query"],
        "polymarket_agent": ["search_polymarket_with_history"],
        "runner_agent": ["generate_structured_output", "build_runner_answer"],
        "eventdata_puller_agent": [
            "fetch_economic_calendar", "query_event_history", "search_events",
            "get_events_by_date_range", "start_event_stream", "stop_event_stream",
            "get_live_events", "get_stream_status", "search_event_names",
        ],
        "analytics_agent": [
            "compute_statistics", "compute_percentile_rank", "compare_distributions",
            "compute_correlation", "generate_histogram", "generate_line_chart",
            "generate_scatter_plot", "generate_bar_chart", "generate_candlestick_chart",
            "generate_multi_candlestick_charts", "compute_returns_distribution",
            "compute_volatility_distribution", "compute_probability_matrix",
            "compute_event_distribution", "compute_surprise_price_distribution",
            "compute_surprise_correlation",
        ],
    }
    
    def __init__(self):
        """Initialize tool loader."""
        self._all_tools = None  # Lazy loaded
        self._loaded_tools = {}  # Cache for loaded tools
        self._agent_tool_map = None  # Lazy built from registry
        logger.info("ToolLoader initialized (lazy mode)")
    
    @property
    def AGENT_TOOL_MAP(self) -> Dict[str, List[str]]:
        """
        Dynamic agent-to-tool mapping built from registry.
        
        Tools are assigned to agents via:
        1. Explicit `agent` parameter in @register_tool decorator
        2. Auto-detection from module path using MODULE_TO_AGENT mapping
        3. Fallback to _FALLBACK_AGENT_TOOL_MAP for backward compatibility
        
        Returns:
            Dictionary mapping agent_name -> list of tool names
        """
        if self._agent_tool_map is None:
            self._build_agent_tool_map()
        return self._agent_tool_map
    
    def _build_agent_tool_map(self) -> None:
        """Build agent-tool map from registry metadata."""
        if self._all_tools is None:
            self._discover_all_tools()
        
        agent_tools: Dict[str, List[str]] = defaultdict(list)
        
        for tool_name, info in self._all_tools.items():
            # 1. Check explicit agent assignment
            agent = info.get("agent")
            
            # 2. Auto-detect from module path
            if not agent:
                module = info.get("module", "")
                for prefix, agent_name in MODULE_TO_AGENT.items():
                    if module.startswith(prefix):
                        agent = agent_name
                        break
            
            # 3. Add to agent's tool list
            if agent:
                if tool_name not in agent_tools[agent]:
                    agent_tools[agent].append(tool_name)
        
        # Merge with fallback to ensure core tools are always available
        for agent_name, tools in self._FALLBACK_AGENT_TOOL_MAP.items():
            if agent_name not in agent_tools:
                agent_tools[agent_name] = list(tools)
            else:
                # Add any fallback tools not discovered
                for tool in tools:
                    if tool not in agent_tools[agent_name]:
                        agent_tools[agent_name].append(tool)
        
        self._agent_tool_map = dict(agent_tools)
        logger.info(f"Built agent-tool map: {sum(len(t) for t in self._agent_tool_map.values())} tools across {len(self._agent_tool_map)} agents")
    
    def load_tools_for_agents(
        self,
        agent_names: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Load tools for specific agents.
        
        Note: This method loads ALL tools for the given agents. In the AI-powered
        workflow, Planner 2 uses AI reasoning to select a subset of these tools
        based on actual task requirements, then calls load_tool() for each selected tool.
        
        Only loads tools for enabled agents. Disabled agents are skipped.
        
        Args:
            agent_names: List of agent names
            
        Returns:
            Dictionary of tool_name -> tool_info for relevant tools
        """

        if self._all_tools is None:
            self._discover_all_tools()
        
        tool_names = set()
        for agent_name in agent_names:
            if not is_agent_enabled(agent_name):
                logger.debug(f"Skipping disabled agent: {agent_name}")
                continue
            agent_tools = self.AGENT_TOOL_MAP.get(agent_name, [])
            tool_names.update(agent_tools)
        
        loaded_tools = {}
        for tool_name in tool_names:
            if tool_name in self._all_tools:
                loaded_tools[tool_name] = self._all_tools[tool_name]
            else:
                logger.warning(f"Tool '{tool_name}' not found in registry")
        
        logger.info(f"Loaded {len(loaded_tools)} tools for agents {agent_names}")
        
        return loaded_tools
    
    def load_tool(
        self,
        tool_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Load a specific tool.
        
        This is the primary method used by AI-powered Planner 2 to load
        only the tools that were intelligently selected based on task requirements.
        
        Args:
            tool_name: Tool name
            
        Returns:
            Tool info dictionary or None if not found
        """
        # Check cache first
        if tool_name in self._loaded_tools:
            return self._loaded_tools[tool_name]
        
        # Discover all tools if needed
        if self._all_tools is None:
            self._discover_all_tools()
        
        # Get tool info
        tool_info = self._all_tools.get(tool_name)
        
        if tool_info:
            self._loaded_tools[tool_name] = tool_info
            logger.debug(f"Loaded tool: {tool_name}")
        else:
            logger.warning(f"Tool not found: {tool_name}")
        
        return tool_info
    
    def get_tool_function(
        self,
        tool_name: str
    ) -> Optional[Callable]:
        """
        Get tool function by name.
        
        Args:
            tool_name: Tool name
            
        Returns:
            Tool function or None if not found
        """
        return get_tool(tool_name)
    
    def get_tools_for_agent(
        self,
        agent_name: str
    ) -> List[str]:
        """
        Get list of tool names for an agent.
        
        Returns empty list if agent is disabled.
        
        Args:
            agent_name: Agent name
            
        Returns:
            List of tool names (empty if agent is disabled)
        """
        if not is_agent_enabled(agent_name):
            logger.debug(f"Agent '{agent_name}' is disabled, returning no tools")
            return []
        return self.AGENT_TOOL_MAP.get(agent_name, [])
    
    def get_tool_metadata(
        self,
        tool_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get tool metadata (description, schema, soft_dependencies).
        
        Args:
            tool_name: Tool name
            
        Returns:
            Metadata dictionary or None
        """
        tool_info = self.load_tool(tool_name)
        
        if tool_info:
            return {
                'name': tool_name,
                'description': tool_info.get('description', ''),
                'input_schema': tool_info.get('input_schema', {}),
                'soft_dependencies': tool_info.get('soft_dependencies', []),
                'module': tool_info.get('module', '')
            }
        
        return None
    
    def list_available_tools(self) -> List[str]:
        """
        List all available tools.
        
        Returns:
            List of tool names
        """
        if self._all_tools is None:
            self._discover_all_tools()
        
        return list(self._all_tools.keys())
    
    def _discover_all_tools(self):
        """Discover all tools from servers directory."""
        # Get project root and servers path
        project_root = Path(__file__).parent.parent.parent.parent
        servers_path = project_root / "src" / "servers"
        
        logger.info(f"Discovering tools from {servers_path}")
        
        # Discover all tools
        self._all_tools = discover_tools(servers_path)
        
        logger.info(f"Discovered {len(self._all_tools)} tools: {list(self._all_tools.keys())}")
    
    def get_agent_tool_summary(
        self,
        agent_names: List[str]
    ) -> Dict[str, List[str]]:
        """
        Get summary of tools for each agent.
        
        Only includes enabled agents. Disabled agents are excluded from summary.
        
        Args:
            agent_names: List of agent names
            
        Returns:
            Dictionary mapping agent_name -> list of tool names (enabled agents only)
        """
        summary = {}
        
        for agent_name in agent_names:
            if not is_agent_enabled(agent_name):
                continue
            summary[agent_name] = self.get_tools_for_agent(agent_name)
        
        return summary
    
    def get_enabled_agent_tool_map(self) -> Dict[str, List[str]]:
        """
        Get agent-to-tool mapping for enabled agents only.
        
        Returns:
            Dictionary mapping agent_name -> list of tool names (enabled agents only)
        """
        return {
            agent: tools for agent, tools in self.AGENT_TOOL_MAP.items()
            if is_agent_enabled(agent)
        }
    
    def clear_cache(self):
        """Clear loaded tools cache."""
        self._loaded_tools.clear()
        logger.debug("Tool cache cleared")
    
    def rebuild_agent_tool_map(self):
        """
        Force rebuild of agent-tool map.
        
        Call this after dynamically adding tools at runtime.
        """
        self._agent_tool_map = None
        self._all_tools = None
        self._discover_all_tools()
        self._build_agent_tool_map()
        logger.info("Agent-tool map rebuilt")
    
    def register_agent_tools(
        self,
        agent_name: str,
        tool_names: List[str]
    ):
        """
        Register additional tools for an agent.
        
        Note: Prefer using @register_tool(agent="...") decorator instead.
        This method is for runtime registration.
        
        Args:
            agent_name: Agent name
            tool_names: List of tool names to add
        """
        if self._agent_tool_map is None:
            self._build_agent_tool_map()
        
        if agent_name not in self._agent_tool_map:
            self._agent_tool_map[agent_name] = []
        
        for tool_name in tool_names:
            if tool_name not in self._agent_tool_map[agent_name]:
                self._agent_tool_map[agent_name].append(tool_name)
        
        logger.info(f"Registered {len(tool_names)} tools for agent '{agent_name}'")

