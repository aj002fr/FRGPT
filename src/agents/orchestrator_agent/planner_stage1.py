"""Stage 1 Planner - Task Decomposition and Agent Assignment."""

import logging
from typing import Dict, Any, List, Optional

from src.mcp.taskmaster_client import TaskPlannerClient
from .task_mapper import TaskMapper
from .dependency_analyzer import DependencyAnalyzer
from .config import AGENT_CAPABILITIES

logger = logging.getLogger(__name__)


class PlannerStage1:
    """
    Stage 1 Planner - Task decomposition with dependencies.
    
    Responsibilities:
    1. Accept user query + agent descriptions
    2. Create subtasks with unique IDs
    3. Identify dependency structure (DAG)
    4. Generate task prompts
    5. Assign tasks to agents
    """
    
    def __init__(self):
        """Initialize Stage 1 Planner."""
        self.task_planner = TaskPlannerClient()
        self.task_mapper = TaskMapper()
        self.dependency_analyzer = DependencyAnalyzer()
        
        logger.info("PlannerStage1 initialized")
    
    def plan(
        self,
        query: str,
        num_subtasks: Optional[int] = None,
        agent_capabilities: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create task decomposition plan.
        
        Args:
            query: User query in natural language
            num_subtasks: Number of subtasks (optional, AI decides if None)
            agent_capabilities: Agent registry (uses AGENT_CAPABILITIES if None)
            
        Returns:
            Task plan dictionary with:
            - query: Original query
            - subtasks: List of task dictionaries with id, description, agent, dependencies
            - dependency_paths: List of execution paths
            - metadata: Analysis metadata
        """
        logger.info(f"Planning for query: '{query[:100]}...'")
        
        # Use default capabilities if not provided
        if agent_capabilities is None:
            agent_capabilities = AGENT_CAPABILITIES
        
        # Step 1: AI-powered task decomposition
        available_agents = list(agent_capabilities.keys())
        logger.info(f"Available agents: {available_agents}")
        
        task_plan = self.task_planner.plan_task(
            query=query,
            available_agents=available_agents,
            num_subtasks=num_subtasks
        )
        
        raw_subtasks = task_plan.get('subtasks', [])
        logger.info(f"AI generated {len(raw_subtasks)} subtasks")
        
        # Step 2: Normalize task IDs and format
        subtasks = self._normalize_subtasks(raw_subtasks)
        
        # Step 3: Agent assignment (task-to-agent mapping)
        mapped_subtasks = self.task_mapper.map_all_tasks(subtasks)
        
        # Step 4: Extract final subtasks with proper format
        final_subtasks = []
        for task in mapped_subtasks:
            final_task = {
                'id': task['id'],
                'description': task.get('description', ''),
                'assigned_agent': task.get('mapped_agent'),
                'dependencies': task.get('dependencies', []),
                'priority': task.get('priority', 1),
                'mappable': task.get('mappable', False),
                'agent_params': task.get('agent_params', {})
            }
            final_subtasks.append(final_task)
        
        # Step 5: Dependency analysis
        mappable_tasks = [t for t in final_subtasks if t['mappable']]
        
        if not mappable_tasks:
            logger.warning("No mappable tasks found!")
            analysis = {
                'dependency_paths': [],
                'task_dependency_paths': {},
                'has_cycles': False,
                'max_depth': 0,
                'parallel_groups': [],
                'total_tasks': 0,
                'independent_tasks': 0
            }
        else:
            analysis = self.dependency_analyzer.analyze(mappable_tasks)
        
        task_dependency_paths = analysis.get('task_dependency_paths', {})
        
        # Step 6: Build final plan
        plan = {
            'query': query,
            'subtasks': final_subtasks,
            'dependency_paths': analysis['dependency_paths'],
            'metadata': {
                'total_tasks': len(final_subtasks),
                'mappable_tasks': len(mappable_tasks),
                'unmappable_tasks': len(final_subtasks) - len(mappable_tasks),
                'has_cycles': analysis['has_cycles'],
                'max_depth': analysis['max_depth'],
                'parallel_groups': analysis['parallel_groups'],
                'independent_tasks': analysis['independent_tasks'],
                'available_agents': available_agents,
                # For each task (typically leaves), a canonical dependency path
                'task_dependency_paths': task_dependency_paths
            }
        }
        
        logger.info(f"Plan created: {len(mappable_tasks)} mappable tasks, "
                   f"{len(analysis['dependency_paths'])} paths, "
                   f"max depth {analysis['max_depth']}")
        
        return plan
    
    def _normalize_subtasks(
        self,
        raw_subtasks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Normalize subtask format.
        
        Ensures:
        - Task IDs are strings (task_1, task_2, etc.)
        - Dependencies are lists of task IDs
        - Priority is set
        
        Args:
            raw_subtasks: Raw subtasks from AI planner
            
        Returns:
            Normalized subtasks
        """
        normalized = []
        
        for i, task in enumerate(raw_subtasks, 1):
            # Get or generate task ID
            task_id = task.get('id')
            if not task_id:
                task_id = f"task_{i}"
            elif isinstance(task_id, int):
                task_id = f"task_{task_id}"
            
            # Normalize dependencies
            dependencies = task.get('dependencies', [])
            if not isinstance(dependencies, list):
                dependencies = [dependencies] if dependencies else []
            
            # Ensure dependency IDs are strings
            normalized_deps = []
            for dep in dependencies:
                if isinstance(dep, int):
                    normalized_deps.append(f"task_{dep}")
                else:
                    normalized_deps.append(str(dep))
            
            # Get priority (default to 1)
            priority = task.get('priority', 1)
            
            # Build normalized task
            normalized_task = {
                'id': task_id,
                'description': task.get('description', ''),
                'dependencies': normalized_deps,
                'priority': priority,
                'agent': task.get('agent', '')  # Suggested agent (may be empty)
            }
            
            normalized.append(normalized_task)
        
        logger.debug(f"Normalized {len(normalized)} subtasks")
        return normalized
    
    def validate_plan(
        self,
        plan: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """
        Validate task plan.
        
        Args:
            plan: Task plan dictionary
            
        Returns:
            (is_valid, error_message)
        """
        # Check required fields
        if 'subtasks' not in plan:
            return False, "Missing 'subtasks' field"
        
        if 'dependency_paths' not in plan:
            return False, "Missing 'dependency_paths' field"
        
        subtasks = plan['subtasks']
        
        # Check task IDs are unique
        task_ids = [t['id'] for t in subtasks]
        if len(task_ids) != len(set(task_ids)):
            return False, "Duplicate task IDs found"
        
        # Check dependencies reference valid tasks
        for task in subtasks:
            for dep in task.get('dependencies', []):
                if dep not in task_ids:
                    return False, f"Invalid dependency: {dep} (task not found)"
        
        # Check for cycles (should be caught by analyzer, but double-check)
        if plan.get('metadata', {}).get('has_cycles', False):
            return False, "Dependency cycle detected"
        
        return True, None
    
    def get_task_by_id(
        self,
        plan: Dict[str, Any],
        task_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get task by ID from plan.
        
        Args:
            plan: Task plan
            task_id: Task ID
            
        Returns:
            Task dictionary or None if not found
        """
        for task in plan.get('subtasks', []):
            if task['id'] == task_id:
                return task
        
        return None
    
    def get_tasks_by_agent(
        self,
        plan: Dict[str, Any],
        agent_name: str
    ) -> List[Dict[str, Any]]:
        """
        Get all tasks assigned to an agent.
        
        Args:
            plan: Task plan
            agent_name: Agent name
            
        Returns:
            List of task dictionaries
        """
        tasks = []
        
        for task in plan.get('subtasks', []):
            if task.get('assigned_agent') == agent_name:
                tasks.append(task)
        
        return tasks
    
    def get_root_tasks(
        self,
        plan: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Get root tasks (no dependencies).
        
        Args:
            plan: Task plan
            
        Returns:
            List of root task dictionaries
        """
        root_tasks = []
        
        for task in plan.get('subtasks', []):
            if not task.get('dependencies'):
                root_tasks.append(task)
        
        return root_tasks

