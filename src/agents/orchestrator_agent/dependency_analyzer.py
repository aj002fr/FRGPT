"""Dependency Analysis for Task DAG."""

import logging
from typing import Dict, Any, List, Set, Tuple
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class DependencyAnalyzer:
    """
    Analyzes task dependencies and extracts execution paths.
    
    Builds a DAG (Directed Acyclic Graph) from task dependencies and
    extracts independent execution paths for parallel processing.
    """
    
    def __init__(self):
        """Initialize dependency analyzer."""
        self.tasks = []
        self.dependency_graph = {}
        self.reverse_graph = {}
        logger.info("DependencyAnalyzer initialized")
    
    def analyze(self, subtasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze task dependencies and extract paths.
        
        Args:
            subtasks: List of task dictionaries with 'id' and 'dependencies'
            
        Returns:
            Dictionary with dependency_paths and analysis metadata
            
        Example:
            Input: [
                {"id": "task_1", "dependencies": []},
                {"id": "task_2", "dependencies": []},
                {"id": "task_3", "dependencies": ["task_1", "task_2"]}
            ]
            Output: {
                "dependency_paths": [
                    ["task_1"],
                    ["task_2"],
                    ["task_1", "task_2", "task_3"]
                ],
                "has_cycles": False,
                "max_depth": 2,
                "parallel_groups": [[task_1, task_2], [task_3]]
            }
        """
        self.tasks = subtasks
        self._build_graphs()
        
        # Check for cycles
        has_cycles = self._detect_cycles()
        if has_cycles:
            logger.error("Cycle detected in task dependencies!")
            raise ValueError("Task dependencies contain a cycle")
        
        # Extract paths
        dependency_paths = self._extract_paths()
        
        # Map each task to one canonical dependency path (root → task)
        task_dependency_paths = self._build_task_dependency_paths(dependency_paths)
        
        # Calculate parallel groups
        parallel_groups = self._compute_parallel_groups()
        
        # Calculate max depth
        max_depth = self._calculate_max_depth()
        
        analysis = {
            "dependency_paths": dependency_paths,
            "task_dependency_paths": task_dependency_paths,
            "has_cycles": has_cycles,
            "max_depth": max_depth,
            "parallel_groups": parallel_groups,
            "total_tasks": len(subtasks),
            "independent_tasks": len([t for t in subtasks if not t.get('dependencies')])
        }
        
        logger.info(f"Analysis complete: {len(dependency_paths)} paths, "
                   f"max depth {max_depth}, {len(parallel_groups)} parallel groups")
        
        return analysis
    
    def _build_graphs(self):
        """Build forward and reverse dependency graphs."""
        self.dependency_graph = {}
        self.reverse_graph = defaultdict(list)
        
        for task in self.tasks:
            task_id = task['id']
            deps = task.get('dependencies', [])
            
            self.dependency_graph[task_id] = deps
            
            # Build reverse graph (who depends on this task)
            for dep in deps:
                self.reverse_graph[dep].append(task_id)
        
        logger.debug(f"Built graphs for {len(self.tasks)} tasks")
    
    def _detect_cycles(self) -> bool:
        """
        Detect cycles using DFS.
        
        Returns:
            True if cycle detected, False otherwise
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {task['id']: WHITE for task in self.tasks}
        
        def dfs(node):
            if color[node] == GRAY:
                return True  # Back edge found = cycle
            if color[node] == BLACK:
                return False  # Already processed
            
            color[node] = GRAY
            
            for neighbor in self.reverse_graph.get(node, []):
                if dfs(neighbor):
                    return True
            
            color[node] = BLACK
            return False
        
        for task in self.tasks:
            if color[task['id']] == WHITE:
                if dfs(task['id']):
                    return True
        
        return False
    
    def _extract_paths(self) -> List[List[str]]:
        """
        Extract all dependency paths from the DAG.
        
        A path is a sequence of tasks where each task depends on previous ones.
        Independent tasks get their own single-task paths.
        
        Returns:
            List of paths (each path is a list of task IDs)
        """
        paths = []
        
        # Find all leaf tasks (tasks with no dependents)
        leaf_tasks = [
            task['id'] for task in self.tasks
            if task['id'] not in self.reverse_graph or not self.reverse_graph[task['id']]
        ]
        
        logger.debug(f"Found {len(leaf_tasks)} leaf tasks: {leaf_tasks}")
        
        # For each leaf, trace back to root(s)
        for leaf in leaf_tasks:
            leaf_paths = self._trace_paths_to_roots(leaf)
            paths.extend(leaf_paths)
        
        # Deduplicate paths (same task sequence)
        unique_paths = []
        seen = set()
        for path in paths:
            path_tuple = tuple(path)
            if path_tuple not in seen:
                seen.add(path_tuple)
                unique_paths.append(path)
        
        return unique_paths
    
    def _build_task_dependency_paths(
        self,
        dependency_paths: List[List[str]]
    ) -> Dict[str, List[str]]:
        """
        Build mapping from task_id to its dependency path.
        
        Strategy:
        - For tasks in a single path: use that path
        - For tasks with fan-in (multiple incoming paths): merge all predecessor tasks
        
        This ensures tasks with multiple dependencies get the full context of ALL
        their predecessors, not just one arbitrary path.
        
        Args:
            dependency_paths: List of paths (root → leaf)
            
        Returns:
            Dict mapping task_id -> dependency path (list of task IDs in execution order)
            
        Example:
            Input: [['task_1', 'task_3'], ['task_2', 'task_3']]
            
            Output: {
                'task_1': ['task_1', 'task_3'],           # Single path
                'task_2': ['task_2', 'task_3'],           # Single path
                'task_3': ['task_1', 'task_2', 'task_3'] # Merged: all predecessors
            }
        """
        # First pass: collect all paths each task appears in
        task_to_paths: Dict[str, List[List[str]]] = {}
        
        for path in dependency_paths:
            if not path:
                continue
            
            for task_id in path:
                if task_id not in task_to_paths:
                    task_to_paths[task_id] = []
                task_to_paths[task_id].append(path)
        
        # Second pass: build canonical path for each task
        task_paths: Dict[str, List[str]] = {}
        
        for task_id, paths in task_to_paths.items():
            if len(paths) == 1:
                # Single path: use it as-is
                task_paths[task_id] = paths[0]
            else:
                # Multiple paths (fan-in): merge all unique predecessors
                all_predecessors = set()
                for path in paths:
                    # Get all tasks before this task in the path
                    task_index = path.index(task_id)
                    all_predecessors.update(path[:task_index])
                
                # Build merged path: predecessors (topologically sorted) + this task
                # Use the order from the first path as a reasonable topological sort
                merged_path = []
                for path in paths:
                    for tid in path:
                        if tid in all_predecessors and tid not in merged_path:
                            merged_path.append(tid)
                
                # Add the task itself at the end
                merged_path.append(task_id)
                task_paths[task_id] = merged_path
        
        return task_paths
    
    def _trace_paths_to_roots(self, task_id: str) -> List[List[str]]:
        """
        Trace all paths from a task back to root tasks (no dependencies).
        
        Args:
            task_id: Starting task ID
            
        Returns:
            List of paths from roots to this task
        """
        dependencies = self.dependency_graph.get(task_id, [])
        
        if not dependencies:
            # Root task - return single-element path
            return [[task_id]]
        
        # Recursively trace paths for each dependency
        all_paths = []
        
        for dep in dependencies:
            dep_paths = self._trace_paths_to_roots(dep)
            for dep_path in dep_paths:
                # Extend path with current task
                all_paths.append(dep_path + [task_id])
        
        return all_paths
    
    def _compute_parallel_groups(self) -> List[List[str]]:
        """
        Compute groups of tasks that can run in parallel.
        
        Uses topological sorting to identify tasks at each level.
        
        Returns:
            List of parallel groups (tasks that can run together)
        """
        # Calculate in-degree for each task
        in_degree = {}
        for task in self.tasks:
            task_id = task['id']
            in_degree[task_id] = len(self.dependency_graph.get(task_id, []))
        
        parallel_groups = []
        remaining = set(task['id'] for task in self.tasks)
        
        while remaining:
            # Find all tasks with in-degree 0 (no pending dependencies)
            ready = [tid for tid in remaining if in_degree[tid] == 0]
            
            if not ready:
                # Should not happen if no cycles
                logger.error("No ready tasks but tasks remaining - possible cycle")
                break
            
            parallel_groups.append(ready)
            
            # Remove ready tasks and update in-degrees
            for task_id in ready:
                remaining.remove(task_id)
                
                # Decrease in-degree for dependent tasks
                for dependent in self.reverse_graph.get(task_id, []):
                    if dependent in remaining:
                        in_degree[dependent] -= 1
        
        return parallel_groups
    
    def _calculate_max_depth(self) -> int:
        """
        Calculate maximum depth of the dependency tree.
        
        Returns:
            Maximum depth (0 for single task, 1 for task with dependency, etc.)
        """
        if not self.tasks:
            return 0
        
        # Calculate depth for each task using BFS
        depth = {}
        
        # Start with root tasks (no dependencies)
        for task in self.tasks:
            task_id = task['id']
            if not self.dependency_graph.get(task_id):
                depth[task_id] = 0
        
        # Process tasks in topological order
        queue = deque([tid for tid, d in depth.items() if d == 0])
        
        while queue:
            task_id = queue.popleft()
            
            # Update depth for dependent tasks
            for dependent in self.reverse_graph.get(task_id, []):
                # Depth is max of all dependency depths + 1
                dep_depths = [
                    depth.get(dep, -1) 
                    for dep in self.dependency_graph.get(dependent, [])
                ]
                
                if all(d >= 0 for d in dep_depths):
                    depth[dependent] = max(dep_depths) + 1
                    queue.append(dependent)
        
        return max(depth.values()) if depth else 0
    
    def get_task_order(self) -> List[str]:
        """
        Get topological sort of tasks.
        
        Returns:
            List of task IDs in valid execution order
        """
        in_degree = {}
        for task in self.tasks:
            task_id = task['id']
            in_degree[task_id] = len(self.dependency_graph.get(task_id, []))
        
        queue = deque([tid for tid, deg in in_degree.items() if deg == 0])
        order = []
        
        while queue:
            task_id = queue.popleft()
            order.append(task_id)
            
            for dependent in self.reverse_graph.get(task_id, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)
        
        return order
    
    def get_immediate_dependencies(self, task_id: str) -> List[str]:
        """Get immediate dependencies for a task."""
        return self.dependency_graph.get(task_id, [])
    
    def get_all_dependencies(self, task_id: str) -> Set[str]:
        """
        Get all transitive dependencies for a task.
        
        Args:
            task_id: Task ID
            
        Returns:
            Set of all task IDs that this task depends on
        """
        all_deps = set()
        to_process = deque(self.dependency_graph.get(task_id, []))
        
        while to_process:
            dep = to_process.popleft()
            if dep not in all_deps:
                all_deps.add(dep)
                to_process.extend(self.dependency_graph.get(dep, []))
        
        return all_deps

