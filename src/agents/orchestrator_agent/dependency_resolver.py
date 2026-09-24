"""Soft Dependency Resolver for Tool Dependencies.

Resolves soft dependencies declared in tool metadata and injects
prerequisite tasks into the execution plan.

Example: generate_candlestick_chart declares a soft dependency on events,
so the resolver automatically injects an eventdata_puller_agent task
before the chart task and wires the output.
"""

import logging
import re
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class DependencyResolver:
    """
    Resolves soft dependencies for tools in execution plans.
    
    Scans execution plan for tools with soft_dependencies metadata,
    generates prerequisite tasks, and wires outputs to dependent tools.
    """
    
    def __init__(self, tool_loader):
        """
        Initialize resolver with tool loader for metadata access.
        
        Args:
            tool_loader: ToolLoader instance for accessing tool metadata
        """
        self.tool_loader = tool_loader
        self._injected_task_counter = 0
    
    def resolve_dependencies(
        self,
        execution_plan: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
        """
        Resolve soft dependencies and inject prerequisite tasks.
        
        Args:
            execution_plan: Original execution plan from planner
            context: Context dict with time_range, symbol, country, etc.
            
        Returns:
            Tuple of:
            - Modified execution plan with injected tasks
            - List of dependency resolution notes (for reporting)
        """
        logger.info(f"DependencyResolver: Scanning {len(execution_plan)} tasks for soft dependencies")
        logger.info(f"DependencyResolver: Context = {context}")
        
        resolved_plan = []
        resolution_notes = []
        
        for task in execution_plan:
            # Get tools for this task
            tools = task.get('tools', [])
            task_id = task.get('task_id', 'unknown')
            
            for tool_name in tools:
                # Check if tool has soft dependencies
                soft_deps = self._get_soft_dependencies(tool_name)
                
                logger.debug(f"Task {task_id}: Tool '{tool_name}' soft_deps check: {soft_deps}")
                
                if not soft_deps:
                    continue
                
                logger.info(f"Task {task_id}: Tool '{tool_name}' has {len(soft_deps)} soft dependencies")
                
                for dep in soft_deps:
                    # Generate dependency task
                    dep_task, note = self._create_dependency_task(
                        dep, context, task, tool_name
                    )
                    
                    if dep_task:
                        # Insert dependency task before current task
                        resolved_plan.append(dep_task)
                        
                        # Wire dependency output to tool parameter
                        self._wire_dependency(task, tool_name, dep, dep_task['task_id'])
                        
                        logger.info(f"Injected dependency task '{dep_task['task_id']}' for {tool_name}.{dep['param_name']}")
                    
                    if note:
                        resolution_notes.append(note)
            
            # Add the original task (possibly modified with dependency refs)
            resolved_plan.append(task)
        
        return resolved_plan, resolution_notes
    
    def _get_soft_dependencies(self, tool_name: str) -> List[Dict[str, Any]]:
        """Get soft dependencies for a tool from registry."""
        tool_info = self.tool_loader.load_tool(tool_name)
        if not tool_info:
            logger.warning(f"Tool '{tool_name}' not found in registry")
            return []
        
        soft_deps = tool_info.get('soft_dependencies', [])
        if soft_deps:
            logger.info(f"Found soft_dependencies for '{tool_name}': {len(soft_deps)} dependencies")
        return soft_deps
    
    def _create_dependency_task(
        self,
        dep: Dict[str, Any],
        context: Dict[str, Any],
        parent_task: Dict[str, Any],
        tool_name: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
        """
        Create a dependency task based on soft dependency spec.
        
        Args:
            dep: Soft dependency specification
            context: Execution context
            parent_task: The task that needs this dependency
            tool_name: The tool that declared the dependency
            
        Returns:
            Tuple of (task_dict or None, resolution_note or None)
        """
        param_name = dep.get('param_name')
        source_agent = dep.get('source_agent')
        source_tool = dep.get('source_tool')
        param_mapping = dep.get('param_mapping', {})
        
        if not all([param_name, source_agent, source_tool]):
            note = {
                "type": "error",
                "tool": tool_name,
                "param": param_name,
                "message": "Invalid soft dependency spec: missing required fields"
            }
            return None, note
        
        # Check if we have enough context to resolve params
        resolved_params, missing = self._resolve_param_mapping(param_mapping, context)
        
        if missing:
            note = {
                "type": "skip",
                "tool": tool_name,
                "param": param_name,
                "message": f"Cannot resolve dependency: missing context values: {missing}"
            }
            logger.warning(f"Skipping soft dependency for {tool_name}.{param_name}: missing {missing}")
            return None, note
        
        # Generate unique task ID
        self._injected_task_counter += 1
        dep_task_id = f"dep_{parent_task.get('task_id', 'unknown')}_{param_name}_{self._injected_task_counter}"
        
        # Build dependency task
        dep_task = {
            'task_id': dep_task_id,
            'agent': source_agent,
            'description': f"[Auto-injected] Fetch {param_name} for {tool_name}",
            'tools': [source_tool],
            'tool_params': {
                source_tool: resolved_params
            },
            'dependencies': parent_task.get('dependencies', []).copy(),  # Inherit parent deps
            'agent_params': {},
            '_injected_for': {
                'parent_task': parent_task.get('task_id'),
                'tool': tool_name,
                'param': param_name,
                'output_path': dep.get('output_path', '')
            },
            '_is_soft_dependency': True
        }
        
        note = {
            "type": "injected",
            "tool": tool_name,
            "param": param_name,
            "dep_task_id": dep_task_id,
            "message": f"Injected {source_agent}.{source_tool} to fetch {param_name}"
        }
        
        return dep_task, note
    
    def _resolve_param_mapping(
        self,
        param_mapping: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Resolve parameter mapping using context values.
        
        Supports:
        - Literal values: "high" → "high"
        - Context refs: "$context.time_range.start" → context['time_range']['start']
        - Defaults: "$context.country|US" → context['country'] or 'US'
        
        Args:
            param_mapping: Mapping from tool param to value spec
            context: Execution context
            
        Returns:
            Tuple of (resolved_params, list_of_missing_required_fields)
        """
        resolved = {}
        missing = []
        
        for param, value_spec in param_mapping.items():
            if isinstance(value_spec, str) and value_spec.startswith('$context.'):
                # Parse context reference
                resolved_value, is_missing = self._resolve_context_ref(value_spec, context)
                
                if is_missing:
                    missing.append(value_spec)
                else:
                    resolved[param] = resolved_value
            else:
                # Literal value
                resolved[param] = value_spec
        
        return resolved, missing
    
    def _resolve_context_ref(
        self,
        ref: str,
        context: Dict[str, Any]
    ) -> Tuple[Any, bool]:
        """
        Resolve a $context.path.to.value|default reference.
        
        Supports chained defaults: $context.a|$context.b|literal
        
        Args:
            ref: Reference string like "$context.time_range.start|2025-01-01"
                 or "$context.time_range.start_timestamp|$context.time_range.start"
            context: Context dictionary
            
        Returns:
            Tuple of (resolved_value, is_missing)
        """
        # Remove $context. prefix
        path_with_default = ref.replace('$context.', '')
        
        # Check for default value (may have multiple with | separator)
        if '|' in path_with_default:
            path, default = path_with_default.split('|', 1)
        else:
            path = path_with_default
            default = None
        
        # Navigate path
        parts = path.split('.')
        value = context
        
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                # Path doesn't exist - try default
                if default is not None:
                    # Check if default is also a context reference
                    if default.startswith('$context.') or default.startswith('time_range.') or default.startswith('symbol'):
                        # Recursively resolve the default
                        if not default.startswith('$context.'):
                            default = '$context.' + default
                        return self._resolve_context_ref(default, context)
                    else:
                        # Literal default
                        return default, False
                return None, True
        
        return value, False
    
    def _wire_dependency(
        self,
        task: Dict[str, Any],
        tool_name: str,
        dep: Dict[str, Any],
        dep_task_id: str
    ):
        """
        Wire dependency task output to tool parameter.
        
        Adds a __task_ref__ directive to the tool params so the executor
        knows to substitute the dependency output.
        
        Args:
            task: Parent task to modify
            tool_name: Tool that needs the dependency
            dep: Soft dependency spec
            dep_task_id: ID of injected dependency task
        """
        param_name = dep.get('param_name')
        output_path = dep.get('output_path', '')
        source_tool = dep.get('source_tool', '')
        
        # Build field path: source_tool.output_path or just source_tool
        if output_path:
            field_path = f"{source_tool}.{output_path}"
        else:
            field_path = source_tool
        
        # Ensure tool_params exists
        if 'tool_params' not in task:
            task['tool_params'] = {}
        if tool_name not in task['tool_params']:
            task['tool_params'][tool_name] = {}
        
        # Add reference to dependency output using __task_ref__ + __field__ format
        # This is compatible with the coder's _resolve_task_references method
        task['tool_params'][tool_name][param_name] = {
            '__task_ref__': dep_task_id,
            '__field__': field_path,
            '__soft_dependency__': True,  # Mark as soft for graceful degradation
            '__fallback__': [],  # Empty list fallback if fetch fails
            '__required__': dep.get('required', False)
        }
        
        # Add implicit dependency
        if 'dependencies' not in task:
            task['dependencies'] = []
        if dep_task_id not in task['dependencies']:
            task['dependencies'].append(dep_task_id)


def build_context_from_plan(
    execution_plan: List[Dict[str, Any]],
    user_query: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build execution context from plan and query.
    
    Extracts time ranges, symbols, countries from task parameters
    to provide context for dependency resolution.
    
    Args:
        execution_plan: List of tasks with their params
        user_query: Original user query (optional)
        
    Returns:
        Context dictionary
    """
    context = {
        'time_range': {},
        'symbol': None,
        'country': 'US',  # Default
        'user_query': user_query
    }
    
    # Scan tasks for relevant parameters
    logger.info(f"build_context_from_plan: Scanning {len(execution_plan)} tasks for context values...")
    
    for task in execution_plan:
        task_id = task.get('task_id', 'unknown')
        agent_params = task.get('agent_params', {})
        tool_params = task.get('tool_params', {})
        
        logger.info(f"  Task {task_id}: agent_params keys: {list(agent_params.keys())}")
        logger.info(f"  Task {task_id}: tool_params keys: {list(tool_params.keys())}")
        
        # Extract from agent_params
        _extract_context_values(agent_params, context)
        
        # Extract from tool_params (nested by tool name)
        for tool_name, params in tool_params.items():
            logger.info(f"    Checking tool {tool_name}: type={type(params).__name__}")
            if isinstance(params, dict):
                logger.info(f"    Tool {tool_name}: params keys: {list(params.keys())}")
                if 'conditions' in params:
                    logger.info(f"      HAS CONDITIONS PARAM!")
                _extract_context_values(params, context)
            else:
                logger.info(f"    Tool {tool_name}: params is not dict, skipping")
    
    logger.info(f"Final built context: time_range={context.get('time_range')}, symbol={context.get('symbol')}")
    return context


def _extract_context_values(params: Dict[str, Any], context: Dict[str, Any], depth: int = 0):
    """
    Extract context values from a params dict (recursively scans nested dicts).
    
    Args:
        params: Parameters dict (may contain nested dicts)
        context: Context dict to populate
        depth: Recursion depth (for logging)
    """
    indent = "  " * depth
    
    # Recursively scan all nested dicts FIRST
    for key, value in params.items():
        if isinstance(value, dict) and key not in ['__task_ref__']:  # Skip task references
            logger.debug(f"{indent}Recursing into nested dict: {key}")
            _extract_context_values(value, context, depth + 1)
    
    # Time range
    if 'start_date' in params:
        context['time_range']['start'] = params['start_date']
    if 'end_date' in params:
        context['time_range']['end'] = params['end_date']
    if 'date' in params:
        # Single date → use as both start and end
        context['time_range']['start'] = params['date']
        context['time_range']['end'] = params['date']
    
    # Extract dates/timestamps from SQL conditions
    if 'conditions' in params and isinstance(params['conditions'], str):
        import re
        from datetime import datetime, timedelta
        sql_conditions = params['conditions']
        
        logger.info(f"_extract_context_values: Found SQL conditions parameter: {sql_conditions[:150]}...")
        
        # Special handling for time window predicates so we don't accidentally include an
        # exclusive upper bound date as an "end date" (e.g., timestamp < '2024-12-12'
        # should map to end='2024-12-11' for date-based event fetching).
        #
        # We support:
        #   timestamp >= 'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM:SS'
        #   timestamp >  'YYYY-MM-DD'
        #   timestamp <= 'YYYY-MM-DD'
        #   timestamp <  'YYYY-MM-DD'
        #
        # If we can infer bounds, we prefer them over naive "all quoted dates" extraction.
        bound_pattern = r"\b(?:timestamp|ts_event)\s*(>=|<=|<|>)\s*['\"](\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)['\"]"
        bounds = re.findall(bound_pattern, sql_conditions)
        logger.info(f"_extract_context_values: SQL bound matches: {bounds}")

        inferred_start_date = None
        inferred_end_date = None
        inferred_start_ts = None
        inferred_end_ts = None

        def _parse_dt(s: str) -> datetime:
            if ' ' in s:
                return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            return datetime.strptime(s, "%Y-%m-%d")

        for op, literal in bounds:
            # Date-only literal vs timestamp literal
            is_date_only = (' ' not in literal)
            dt = _parse_dt(literal)

            if op in (">=", ">"):
                # For strictly greater-than on date-only, the effective start is next day
                if op == ">" and is_date_only:
                    dt_eff = dt + timedelta(days=1)
                else:
                    dt_eff = dt

                # Track latest lower bound
                if inferred_start_ts is None or dt_eff > inferred_start_ts:
                    inferred_start_ts = dt_eff
                # Also track date-level start
                if inferred_start_date is None or dt_eff.date() > inferred_start_date:
                    inferred_start_date = dt_eff.date()

            elif op in ("<=", "<"):
                # For strictly less-than on date-only, the effective end is previous day
                if op == "<" and is_date_only:
                    dt_eff = dt - timedelta(days=1)
                else:
                    dt_eff = dt

                # Track earliest upper bound
                if inferred_end_ts is None or dt_eff < inferred_end_ts:
                    inferred_end_ts = dt_eff
                # Also track date-level end
                if inferred_end_date is None or dt_eff.date() < inferred_end_date:
                    inferred_end_date = dt_eff.date()

        if inferred_start_date or inferred_end_date:
            # Apply inferred date bounds if context doesn't already have explicit values
            if inferred_start_date and not context['time_range'].get('start'):
                context['time_range']['start'] = inferred_start_date.strftime("%Y-%m-%d")
            if inferred_end_date and not context['time_range'].get('end'):
                context['time_range']['end'] = inferred_end_date.strftime("%Y-%m-%d")

            # Apply inferred timestamps only if we actually saw time components
            # (avoid manufacturing midnight timestamps from date-only)
            if inferred_start_ts and (' ' in str(inferred_start_ts)) and not context['time_range'].get('start_timestamp'):
                context['time_range']['start_timestamp'] = inferred_start_ts.strftime("%Y-%m-%d %H:%M:%S")
            if inferred_end_ts and (' ' in str(inferred_end_ts)) and not context['time_range'].get('end_timestamp'):
                context['time_range']['end_timestamp'] = inferred_end_ts.strftime("%Y-%m-%d %H:%M:%S")
        else:
            # Fallback: Capture full timestamps OR just dates from any quoted literals
            # Matches: '2024-12-06 13:00:00' or '2024-12-06'
            timestamp_pattern = r"['\"](\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)['\"]"
            matches = re.findall(timestamp_pattern, sql_conditions)

            logger.info(f"_extract_context_values: SQL regex matches: {matches}")

            if matches:
                sorted_matches = sorted(matches)

                # Check if we have time components
                has_time = any(' ' in m for m in matches)

                if has_time:
                    # Store full timestamps
                    if not context['time_range'].get('start_timestamp'):
                        context['time_range']['start_timestamp'] = sorted_matches[0]
                    if not context['time_range'].get('end_timestamp'):
                        context['time_range']['end_timestamp'] = sorted_matches[-1]
                    logger.info(f"_extract_context_values: Extracted timestamps from SQL: {sorted_matches[0]} to {sorted_matches[-1]}")

                # Always store date-only versions too
                dates_only = [m[:10] for m in sorted_matches]
                if not context['time_range'].get('start'):
                    context['time_range']['start'] = dates_only[0]
                if not context['time_range'].get('end'):
                    context['time_range']['end'] = dates_only[-1]
    
    # Handle 'dates' parameter from run_multi_date_query (list of dates/timestamps)
    if 'dates' in params and params['dates']:
        dates_list = params['dates']
        if isinstance(dates_list, list) and len(dates_list) > 0:
            # Skip task references - only process actual date strings
            actual_dates = [d for d in dates_list if isinstance(d, str) and not isinstance(d, dict)]
            if actual_dates:
                try:
                    sorted_dates = sorted(actual_dates)
                    
                    # Check if dates have time components (timestamps)
                    has_time = any('T' in d for d in actual_dates)
                    
                    if has_time:
                        # Extract full timestamps AND dates
                        if not context['time_range'].get('start_timestamp'):
                            context['time_range']['start_timestamp'] = sorted_dates[0]
                        if not context['time_range'].get('end_timestamp'):
                            context['time_range']['end_timestamp'] = sorted_dates[-1]
                        logger.info(f"Extracted timestamp range from dates: {sorted_dates[0]} to {sorted_dates[-1]}")
                    
                    # Also extract date-only versions
                    sorted_dates_only = sorted([d[:10] if len(d) >= 10 else d for d in actual_dates])
                    if not context['time_range'].get('start'):
                        context['time_range']['start'] = sorted_dates_only[0]
                    if not context['time_range'].get('end'):
                        context['time_range']['end'] = sorted_dates_only[-1]
                        
                except (TypeError, ValueError, IndexError) as e:
                    logger.warning(f"Failed to extract dates from dates list: {e}")
    
    # Symbol
    if 'symbol' in params and not context['symbol']:
        context['symbol'] = params['symbol']
    if 'symbol_pattern' in params and not context['symbol']:
        context['symbol'] = params['symbol_pattern']
    
    # Country
    if 'country' in params:
        context['country'] = params['country']
    if 'event_country' in params:
        context['country'] = params['event_country']
    
    # Timestamps from data (if present) - for precise time range
    if 'timestamps' in params and params['timestamps']:
        timestamps = params['timestamps']
        # Handle task references - skip them
        if isinstance(timestamps, dict) and '__task_ref__' in timestamps:
            return  # Can't extract from task reference
        
        if isinstance(timestamps, list) and len(timestamps) > 0:
            # Extract date AND timestamp range
            try:
                start_ts = min(timestamps)
                end_ts = max(timestamps)
                
                # Store full timestamps for intraday precision
                if not context['time_range'].get('start_timestamp'):
                    context['time_range']['start_timestamp'] = start_ts
                if not context['time_range'].get('end_timestamp'):
                    context['time_range']['end_timestamp'] = end_ts
                
                # Also store date-only versions as fallback
                if not context['time_range'].get('start'):
                    context['time_range']['start'] = start_ts[:10] if len(start_ts) >= 10 else start_ts
                if not context['time_range'].get('end'):
                    context['time_range']['end'] = end_ts[:10] if len(end_ts) >= 10 else end_ts
                
                logger.info(f"_extract_context_values: Extracted timestamp range: {start_ts} to {end_ts}")
            except (TypeError, ValueError) as e:
                logger.warning(f"Failed to extract timestamp range: {e}")

