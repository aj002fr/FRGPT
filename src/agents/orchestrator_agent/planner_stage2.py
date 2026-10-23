"""Stage 2 Planner - Tool Discovery Per Dependency Path."""

import logging
from typing import Dict, Any, List, Optional

from src.mcp.taskmaster_client import TaskPlannerClient
from .tool_loader import ToolLoader
from .task_mapper import TaskMapper
from .workers_db import WorkersDB
from .config import get_db_path

# TEMPORARY: Force reload dependency_resolver to pick up latest changes
import importlib
import sys
# Always try to reload if it exists
if 'src.agents.orchestrator_agent.dependency_resolver' in sys.modules:
    del sys.modules['src.agents.orchestrator_agent.dependency_resolver']

from .dependency_resolver import DependencyResolver, build_context_from_plan

logger = logging.getLogger(__name__)
logger.info("LOADED dependency_resolver (forced fresh import)")


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
        self.ai_planner = TaskPlannerClient()
        
        logger.info(f"PlannerStage2 initialized for path '{path_id}' with {len(task_ids)} tasks")
    
    def discover_tools_and_params(
        self,
        all_subtasks: List[Dict[str, Any]],
        user_query: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Discover tools and parameters for this path using AI reasoning.
        
        Args:
            all_subtasks: All subtasks from Stage 1 plan
            user_query: Original user query (required for runner_agent tasks)
            
        Returns:
            Path execution plan with tools and parameters
        """
        logger.info(f"Discovering tools for path '{self.path_id}'")
        
        # Step 1: Load tasks for this path.
        #
        # Preferred path: if a run_id is provided, read the normalized Planner 1
        # tasks for this run directly from WorkersDB so that the entire
        # orchestrator pipeline can operate from a shared SQLite state instead
        # of intermediate JSON artifacts.
        if run_id:
            try:
                db_path = get_db_path()
                with WorkersDB(db_path) as db:
                    all_tasks_from_db = db.get_planner1_tasks(run_id)
                logger.info(
                    "Loaded %d Planner 1 tasks from DB for run %s",
                    len(all_tasks_from_db),
                    run_id,
                )
            except Exception as exc:
                logger.error(
                    "Failed to load Planner 1 tasks from DB for run %s: %s; "
                    "falling back to in-memory subtasks.",
                    run_id,
                    exc,
                )
                all_tasks_from_db = None
        else:
            all_tasks_from_db = None

        source_subtasks = all_tasks_from_db or all_subtasks

        # Debug: log which tasks we're filtering for
        logger.debug(f"Path {self.path_id}: Looking for task_ids: {self.task_ids}")
        logger.debug(f"Path {self.path_id}: Source subtasks: {[t['id'] for t in source_subtasks]}")
        
        path_tasks = [
            task
            for task in source_subtasks
            if task["id"] in self.task_ids and task.get("mappable", False)
        ]
        
        # Debug: log which tasks we found
        found_task_ids = [t['id'] for t in path_tasks]
        missing_task_ids = [tid for tid in self.task_ids if tid not in found_task_ids]
        if missing_task_ids:
            logger.warning(f"Path {self.path_id}: Missing tasks in path_tasks: {missing_task_ids}")
        logger.debug(f"Path {self.path_id}: Found path_tasks: {found_task_ids}")
        
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
        
        # Step 3: Get available tools per agent
        available_tools = {
            agent: self.tool_loader.get_tools_for_agent(agent)
            for agent in agent_names
        }
        
        logger.info(f"Available tools per agent: {available_tools}")
        
        # Step 4: Prepare tasks for AI tool selection
        ai_input_tasks = []
        for task in path_tasks:
            ai_input_tasks.append({
                'task_id': task['id'],
                'description': task.get('description', ''),
                'agent': task.get('assigned_agent'),
                'agent_params': task.get('agent_params', {}),
                'dependencies': task.get('dependencies', [])
            })
        
        # Step 5: AI-powered tool selection and parameter mapping
        try:
            ai_selection = self.ai_planner.select_tools_for_path(
                self.path_id,
                ai_input_tasks,
                available_tools,
                user_query=user_query
            )
            logger.info(f"AI tool selection complete: {ai_selection.get('method', 'unknown')}")
        except Exception as e:
            logger.error(f"AI tool selection failed, using fallback: {e}")
            # Fallback: use all available tools
            ai_selection = self.ai_planner._fallback_tool_selection(
                self.path_id,
                ai_input_tasks,
                available_tools
            )
        
        # Step 6: Extract selected tools and load them
        all_selected_tools = set()
        for task_selection in ai_selection.get('tool_selections', {}).values():
            all_selected_tools.update(task_selection.get('selected_tools', []))
        
        # Load only the selected tools
        tools_loaded = {}
        for tool_name in all_selected_tools:
            tool_info = self.tool_loader.load_tool(tool_name)
            if tool_info:
                tools_loaded[tool_name] = tool_info
        
        tool_names = list(tools_loaded.keys())
        logger.info(f"Loaded {len(tool_names)} AI-selected tools: {tool_names}")
        
        # Step 7: Build execution plan using AI selections
        execution_plan = []
        tool_selections = ai_selection.get('tool_selections', {})
        
        for task in path_tasks:
            task_id = task['id']
            task_selection = tool_selections.get(task_id, {})
            agent_name = task.get('assigned_agent')
            
            # Get tool params from AI selection
            tool_params = task_selection.get('tool_params', {})
            
            # IMPORTANT: For runner_agent tasks, always inject the user query
            if agent_name == 'runner_agent' and user_query:
                # Ensure all runner_agent tools get the query parameter
                for tool_name in task_selection.get('selected_tools', []):
                    if tool_name in tool_params:
                        # Add query to existing params
                        tool_params[tool_name]['query'] = user_query
                    else:
                        # Create params with query
                        tool_params[tool_name] = {'query': user_query}
                
                logger.debug(f"Injected user query into runner_agent task {task_id}")
            
            # Extract dependencies from tool parameters (__task_ref__ directives)
            explicit_deps = set(task.get('dependencies', []))
            implicit_deps = self._extract_task_references(tool_params)
            all_deps = sorted(explicit_deps | implicit_deps)
            
            if implicit_deps:
                logger.info(f"Task {task_id}: Added implicit dependencies from __task_ref__: {implicit_deps}")
            
            task_plan = {
                'task_id': task_id,
                'agent': agent_name,
                'description': task.get('description', ''),
                'tools': task_selection.get('selected_tools', []),
                'tool_params': tool_params,
                'dependencies': all_deps,
                'agent_params': task.get('agent_params', {}),
                'ai_reasoning': task_selection.get('reasoning', '')
            }
            execution_plan.append(task_plan)
        
        # Step 8: Resolve soft dependencies
        # Scan for tools with soft_dependencies and inject prerequisite tasks
        resolver = DependencyResolver(self.tool_loader)
        
        # DEBUG: Log what we're passing to context builder
        logger.info(f"Building context from {len(execution_plan)} tasks")
        for t in execution_plan:
            logger.debug(f"  Task {t.get('task_id')}: tools={t.get('tools')}, tool_params keys={list(t.get('tool_params', {}).keys())}")
        
        context = build_context_from_plan(execution_plan, user_query)
        
        resolved_plan, resolution_notes = resolver.resolve_dependencies(execution_plan, context)
        
        if resolution_notes:
            injected_count = sum(1 for n in resolution_notes if n.get('type') == 'injected')
            skipped_count = sum(1 for n in resolution_notes if n.get('type') == 'skip')
            if injected_count:
                logger.info(f"Dependency resolution: injected {injected_count} tasks")
            if skipped_count:
                logger.info(f"Dependency resolution: skipped {skipped_count} (missing context)")
        
        # Use resolved plan (may have injected tasks)
        execution_plan = resolved_plan
        
        # Update tools_loaded to include any newly required tools from injected tasks
        for task_plan in execution_plan:
            if task_plan.get('_is_soft_dependency'):
                for tool_name in task_plan.get('tools', []):
                    if tool_name not in tool_names:
                        tool_info = self.tool_loader.load_tool(tool_name)
                        if tool_info:
                            tools_loaded[tool_name] = tool_info
                            tool_names.append(tool_name)
        
        # Step 9: Build path plan
        path_plan = {
            'path_id': self.path_id,
            'tasks': self.task_ids,
            'mappable_tasks': [t['id'] for t in path_tasks],
            'agents': agent_names,
            'tools_loaded': tool_names,
            'execution_plan': execution_plan,
            'ai_method': ai_selection.get('method', 'unknown'),
            'metadata': {
                'total_tasks': len(self.task_ids),
                'mappable_tasks': len(path_tasks),
                'num_agents': len(agent_names),
                'num_tools': len(tool_names),
                'ai_powered': True
            }
        }
        
        logger.info(f"Path plan created for '{self.path_id}': "
                   f"{len(execution_plan)} tasks, {len(tool_names)} tools (AI-selected)")
        
        return path_plan
    
    def _extract_task_references(self, params: Any) -> set:
        """
        Recursively extract all task IDs referenced via __task_ref__ in parameters.
        
        Args:
            params: Tool parameters (can be dict, list, or primitive)
            
        Returns:
            Set of task IDs found in __task_ref__ directives
        """
        task_refs = set()
        
        if isinstance(params, dict):
            if "__task_ref__" in params:
                task_id = params.get("__task_ref__")
                if task_id:
                    task_refs.add(task_id)
            # Recursively check nested dicts
            for value in params.values():
                task_refs |= self._extract_task_references(value)
        elif isinstance(params, list):
            # Recursively check list items
            for item in params:
                task_refs |= self._extract_task_references(item)
        
        return task_refs
    
    def _build_task_plan(
        self,
        task: Dict[str, Any],
        available_tools: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        DEPRECATED: Build execution plan for a single task (fallback only).
        
        This method is kept for backward compatibility but is no longer used
        in the main flow. AI-powered tool selection is now used instead.
        
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
        
        logger.debug(f"Task plan for {task_id}: {len(task_tools)} tools (fallback)")
        
        return task_plan
    
    def _extract_tool_params(
        self,
        task: Dict[str, Any],
        tool_names: List[str],
        available_tools: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        DEPRECATED: Extract parameters for each tool from task description (fallback only).
        
        This method is kept for backward compatibility but is no longer used
        in the main flow. AI-powered parameter mapping is now used instead.
        
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
        DEPRECATED: Extract parameters for a specific tool (fallback only).
        
        This method is kept for backward compatibility but is no longer used
        in the main flow. AI-powered parameter mapping is now used instead.
        
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
                # Polymarket search (Gamma /public-search + history)
                return {
                    "query": agent_params.get("query", ""),
                    "session_id": agent_params.get("session_id"),
                    "limit": agent_params.get("limit", 10),
                }
            if tool_name == "get_polymarket_history":
                # Polymarket query history
                return {
                    "session_id": agent_params.get("session_id"),
                    "limit": agent_params.get("limit", 10),
                }
            if tool_name == "get_market_price_history":
                # Single-date price history (Polymarket CLOB)
                return {
                    "market_id": agent_params.get("market_id"),
                    "date": agent_params.get("date"),
                    "date_range_hours": agent_params.get("date_range_hours", 12),
                }
            if tool_name == "get_market_price_range":
                # Price range / trend over dates
                return {
                    "market_id": agent_params.get("market_id"),
                    "start_date": agent_params.get("start_date"),
                    "end_date": agent_params.get("end_date"),
                    "interval_days": agent_params.get("interval_days", 1),
                }
        
        elif agent_name == "eventdata_puller_agent":
            # EventData Puller tools
            if tool_name == "query_event_history":
                return {
                    "event_name": agent_params.get("event_name"),
                    "country": agent_params.get("country"),
                    "lookback_days": agent_params.get("lookback_days"),
                    "limit": agent_params.get("limit", 100)
                }
            elif tool_name == "search_events":
                return {
                    "keyword": agent_params.get("event_name"),
                    "country": agent_params.get("country"),
                    "importance": agent_params.get("importance"),
                    "limit": agent_params.get("limit", 50)
                }
            elif tool_name == "fetch_economic_calendar":
                return {
                    "country": agent_params.get("country"),
                    "event_name": agent_params.get("event_name"),
                    "importance": agent_params.get("importance"),
                    "full_refresh": False
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

    def _is_reasoning_only_agent(self, agent_name: Optional[str]) -> bool:
        """
        DEPRECATED: Use _is_orchestrator_direct() instead.
        
        Return True if the given agent is called directly by orchestrator.
        """
        if not agent_name:
            return False
        from .config import AGENT_CAPABILITIES

        capabilities = AGENT_CAPABILITIES.get(agent_name, {})
        # Check new execution_mode field, fallback to old reasoning_only for compatibility
        return (
            capabilities.get("execution_mode") == "orchestrator_direct" or
            bool(capabilities.get("reasoning_only"))
        )
    
    def _is_orchestrator_direct(self, agent_name: Optional[str]) -> bool:
        """Return True if the given agent is called directly by orchestrator."""
        if not agent_name:
            return False
        from .config import AGENT_CAPABILITIES

        capabilities = AGENT_CAPABILITIES.get(agent_name, {})
        return capabilities.get("execution_mode") == "orchestrator_direct"
    
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

