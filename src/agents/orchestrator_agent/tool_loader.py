"""Lazy tool loading for orchestrator agent."""

import logging
from pathlib import Path
from typing import Dict, Any, List, Set, Optional, Callable

from src.mcp.discovery import discover_tools, get_tool, get_tool_info

logger = logging.getLogger(__name__)


class ToolLoader:
    """
    Lazy tool loader for context-efficient tool discovery.
    
    Loads only the tools needed for specific agents rather than
    all tools upfront, reducing context size for each dependency path.
    
    Architecture:
    - AGENT_TOOL_MAP defines the universe of available tools per agent
    - AI reasoning in Planner Stage 2 intelligently selects a subset based on task needs
    - This class handles the actual loading of selected tools from the registry
    
    The AI-powered selection ensures:
    - Only necessary tools are loaded per dependency path
    - Tool parameters are intelligently mapped from agent params
    - Context size is minimized for efficient execution
    """
    
    # Agent to tool mapping - defines available tools per agent
    # AI in Planner 2 selects from these based on task requirements
    AGENT_TOOL_MAP = {
        "market_data_agent": ["run_query"],
        # polymarket agent uses unified search tool that combines current + historical data
        "polymarket_agent": [
            "search_polymarket_with_history",  # Unified tool (replaces search + get_history)
            # Legacy tools (kept for backward compatibility):
            # "search_polymarket_markets",
            # "get_polymarket_history",
            # "get_market_price_history",
            # "get_market_price_range",
        ],
        # Runner / reasoning agent tools exposed via MCP:
        # - generate_structured_output: non-reasoning consolidation
        # - build_runner_answer: reasoning-enabled final answer builder
        "runner_agent": [
            "generate_structured_output",
            "build_runner_answer",
        ],
        # EventData Puller agent - Trading Economics API tools
        "eventdata_puller_agent": [
            "fetch_economic_calendar",
            "query_event_history",
            "search_events",
            "find_correlated_events",
            "find_events_for_all_instances",
            "start_event_stream",
            "stop_event_stream",
            "get_live_events",
            "get_stream_status",
        ],
        # Analytics agent - Statistics and visualization tools
        "analytics_agent": [
            "compute_statistics",
            "compute_percentile_rank",
            "compare_distributions",
            "compute_correlation",
            "generate_histogram",
            "generate_line_chart",
            "generate_scatter_plot",
            "generate_bar_chart",
        ],
    }
    
    def __init__(self):
        """Initialize tool loader."""
        self._all_tools = None  # Lazy loaded
        self._loaded_tools = {}  # Cache for loaded tools
        logger.info("ToolLoader initialized (lazy mode)")
    
    def load_tools_for_agents(
        self,
        agent_names: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Load tools for specific agents.
        
        Note: This method loads ALL tools for the given agents. In the AI-powered
        workflow, Planner 2 uses AI reasoning to select a subset of these tools
        based on actual task requirements, then calls load_tool() for each selected tool.
        
        Args:
            agent_names: List of agent names
            
        Returns:
            Dictionary of tool_name -> tool_info for relevant tools
        """
        # Discover all tools if not already done
        if self._all_tools is None:
            self._discover_all_tools()
        
        # Collect unique tool names for these agents
        tool_names = set()
        for agent_name in agent_names:
            agent_tools = self.AGENT_TOOL_MAP.get(agent_name, [])
            tool_names.update(agent_tools)
        
        # Load only the needed tools
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
        
        Args:
            agent_name: Agent name
            
        Returns:
            List of tool names
        """
        return self.AGENT_TOOL_MAP.get(agent_name, [])
    
    def get_tool_metadata(
        self,
        tool_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get tool metadata (description, schema).
        
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
        
        Args:
            agent_names: List of agent names
            
        Returns:
            Dictionary mapping agent_name -> list of tool names
        """
        summary = {}
        
        for agent_name in agent_names:
            summary[agent_name] = self.get_tools_for_agent(agent_name)
        
        return summary
    
    def clear_cache(self):
        """Clear loaded tools cache."""
        self._loaded_tools.clear()
        logger.debug("Tool cache cleared")
    
    @classmethod
    def register_agent_tools(
        cls,
        agent_name: str,
        tool_names: List[str]
    ):
        """
        Register tools for a new agent.
        
        Args:
            agent_name: Agent name
            tool_names: List of tool names
        """
        cls.AGENT_TOOL_MAP[agent_name] = tool_names
        logger.info(f"Registered {len(tool_names)} tools for agent '{agent_name}'")

