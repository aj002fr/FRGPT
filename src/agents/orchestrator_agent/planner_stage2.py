"""Stage 2 Planner - Tool Discovery Per Dependency Path."""

import logging
from typing import Dict, Any, List, Optional

from .tool_loader import ToolLoader
from .task_mapper import TaskMapper

logger = logging.getLogger(__name__)


class PlannerStage2:
    """
    Stage 2 Planner - Tool discovery per dependency path.
    
    Responsibilities:
    1. Load only relevant tools for agents in the path
    2. Extract tool parameters from task descriptions
    3. Build execution plan for path
    
    Key Feature: Context isolation - each path only sees its own tools.
    """
    
    def __init__(self, path_id: str, task_ids: List[str]):
        """
        Initialize Stage 2 Planner for a specific path.
        
        Args:
            path_id: Unique path identifier
            task_ids: List of task IDs in this path
        """
        self.path_id = path_id
        self.task_ids = task_ids
        
        self.tool_loader = ToolLoader()
        self.task_mapper = TaskMapper()
        
        logger.info(f"PlannerStage2 initialized for path '{path_id}' with {len(task_ids)} tasks")
    
    def discover_tools_and_params(
        self,
        all_subtasks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Discover tools and parameters for this path.
        
        Args:
            all_subtasks: All subtasks from Stage 1 plan
            
        Returns:
            Path execution plan with tools and parameters
        """
        logger.info(f"Discovering tools for path '{self.path_id}'")
        
        # Step 1: Filter tasks for this path
        path_tasks = [
            task for task in all_subtasks
            if task['id'] in self.task_ids and task.get('mappable', False)
        ]
        
        if not path_tasks:
            logger.warning(f"No mappable tasks in path '{self.path_id}'")
            return self._empty_plan()
        
        # Step 2: Collect unique agents in this path
        agent_names = list(set(
            task['assigned_agent'] 
            for task in path_tasks 
            if task.get('assigned_agent')
        ))
        
        logger.info(f"Path agents: {agent_names}")
        
        # Step 3: Load tools for these agents (lazy loading)
        tools_loaded = self.tool_loader.load_tools_for_agents(agent_names)
        tool_names = list(tools_loaded.keys())
        
        logger.info(f"Loaded {len(tool_names)} tools: {tool_names}")
        
        # Step 4: Build execution plan for each task
        execution_plan = []
        
        for task in path_tasks:
            task_plan = self._build_task_plan(task, tools_loaded)
            execution_plan.append(task_plan)
        
        # Step 5: Build path plan
        path_plan = {
            'path_id': self.path_id,
            'tasks': self.task_ids,
            'mappable_tasks': [t['id'] for t in path_tasks],
            'agents': agent_names,
            'tools_loaded': tool_names,
            'execution_plan': execution_plan,
            'metadata': {
                'total_tasks': len(self.task_ids),
                'mappable_tasks': len(path_tasks),
                'num_agents': len(agent_names),
                'num_tools': len(tool_names)
            }
        }
        
        logger.info(f"Path plan created for '{self.path_id}': "
                   f"{len(execution_plan)} tasks, {len(tool_names)} tools")
        
        return path_plan
    
    def _build_task_plan(
        self,
        task: Dict[str, Any],
        available_tools: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Build execution plan for a single task.
        
        Args:
            task: Task dictionary
            available_tools: Available tools for this path
            
        Returns:
            Task execution plan
        """
        task_id = task['id']
        agent_name = task.get('assigned_agent')
        description = task.get('description', '')
        
        # Get tools for this agent
        agent_tools = self.tool_loader.get_tools_for_agent(agent_name)
        
        # Filter to only available tools
        task_tools = [
            tool_name for tool_name in agent_tools
            if tool_name in available_tools
        ]
        
        # Extract tool parameters
        tool_params = self._extract_tool_params(
            task,
            task_tools,
            available_tools
        )
        
        task_plan = {
            'task_id': task_id,
            'agent': agent_name,
            'description': description,
            'tools': task_tools,
            'tool_params': tool_params,
            'dependencies': task.get('dependencies', []),
            'agent_params': task.get('agent_params', {})
        }
        
        logger.debug(f"Task plan for {task_id}: {len(task_tools)} tools")
        
        return task_plan
    
    def _extract_tool_params(
        self,
        task: Dict[str, Any],
        tool_names: List[str],
        available_tools: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract parameters for each tool from task description.
        
        Args:
            task: Task dictionary
            tool_names: List of tool names for this task
            available_tools: Available tools metadata
            
        Returns:
            Dictionary mapping tool_name -> parameters
        """
        tool_params = {}
        
        agent_name = task.get('assigned_agent')
        agent_params = task.get('agent_params', {})
        
        # For each tool, extract relevant parameters
        for tool_name in tool_names:
            params = self._extract_params_for_tool(
                tool_name,
                agent_name,
                agent_params,
                available_tools.get(tool_name, {})
            )
            
            if params:
                tool_params[tool_name] = params
        
        return tool_params
    
    def _extract_params_for_tool(
        self,
        tool_name: str,
        agent_name: str,
        agent_params: Dict[str, Any],
        tool_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extract parameters for a specific tool.
        
        Args:
            tool_name: Tool name
            agent_name: Agent name
            agent_params: Parameters extracted by TaskMapper
            tool_info: Tool metadata
            
        Returns:
            Tool parameters dictionary
        """
        # Map agent parameters to tool parameters
        # This is agent/tool specific
        
        if agent_name == "market_data_agent" and tool_name == "run_query":
            # Extract SQL query parameters
            return {
                "template": agent_params.get("template", "by_symbol"),
                "params": agent_params.get("params", {}),
                "columns": agent_params.get("columns"),
                "limit": agent_params.get("limit", 1000),
                "order_by_column": agent_params.get("order_by_column"),
                "order_by_direction": agent_params.get("order_by_direction", "ASC"),
            }

        elif agent_name == "polymarket_agent":
            # Unified polymarket agent uses multiple tools
            if tool_name == "search_polymarket_markets":
                return {
                    "query": agent_params.get("query", ""),
                    "session_id": agent_params.get("session_id"),
                    "limit": agent_params.get("limit", 10),
                }
            if tool_name == "get_polymarket_history":
                return {
                    "session_id": agent_params.get("session_id"),
                    "limit": agent_params.get("limit", 10),
                }
            if tool_name == "get_market_price_history":
                return {
                    "market_id": agent_params.get("market_id"),
                    "date": agent_params.get("date"),
                    "date_range_hours": agent_params.get("date_range_hours", 12),
                }
            if tool_name == "get_market_price_range":
                return {
                    "market_id": agent_params.get("market_id"),
                    "start_date": agent_params.get("start_date"),
                    "end_date": agent_params.get("end_date"),
                    "interval_days": agent_params.get("interval_days", 1),
                }
        
        # Default: return agent_params as-is
        return agent_params
    
    def _empty_plan(self) -> Dict[str, Any]:
        """Create empty plan for path with no mappable tasks."""
        return {
            'path_id': self.path_id,
            'tasks': self.task_ids,
            'mappable_tasks': [],
            'agents': [],
            'tools_loaded': [],
            'execution_plan': [],
            'metadata': {
                'total_tasks': len(self.task_ids),
                'mappable_tasks': 0,
                'num_agents': 0,
                'num_tools': 0
            }
        }
    
    def get_tool_metadata(
        self,
        tool_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get metadata for a tool.
        
        Args:
            tool_name: Tool name
            
        Returns:
            Tool metadata or None
        """
        return self.tool_loader.get_tool_metadata(tool_name)
    
    def validate_path_plan(
        self,
        path_plan: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """
        Validate path execution plan.
        
        Args:
            path_plan: Path plan dictionary
            
        Returns:
            (is_valid, error_message)
        """
        # Check required fields
        required_fields = ['path_id', 'tasks', 'execution_plan']
        for field in required_fields:
            if field not in path_plan:
                return False, f"Missing required field: {field}"
        
        # Check execution plan has entries for all mappable tasks
        exec_task_ids = {p['task_id'] for p in path_plan['execution_plan']}
        mappable_task_ids = set(path_plan.get('mappable_tasks', []))
        
        if exec_task_ids != mappable_task_ids:
            return False, "Execution plan doesn't match mappable tasks"
        
        return True, None


def create_planners_for_paths(
    dependency_paths: List[List[str]]
) -> List[PlannerStage2]:
    """
    Create Stage 2 planners for all dependency paths.
    
    Args:
        dependency_paths: List of paths (each path is list of task IDs)
        
    Returns:
        List of PlannerStage2 instances
    """
    planners = []
    
    for i, path in enumerate(dependency_paths, 1):
        path_id = f"path_{i}"
        planner = PlannerStage2(path_id, path)
        planners.append(planner)
    
    logger.info(f"Created {len(planners)} Stage 2 planners for paths")
    
    return planners

