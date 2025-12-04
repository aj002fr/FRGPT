# Orchestrator Agent

## Purpose

The Orchestrator Agent coordinates multiple worker agents to answer complex queries that require data from different sources or agents, and then calls a dedicated GPT-5-powered `runner_agent` to produce the final answer.

## Workflow

1. **Task Planning**: Uses taskmaster MCP server to decompose natural language queries into subtasks
2. **Task Mapping**: Maps each subtask to the appropriate worker agent (market_data_agent, polymarket_agent, runner_agent)
3. **Code Generation**: Generates executable Python script with async task execution for worker agents
4. **Parallel Execution**: Executes independent worker tasks in parallel using asyncio and persists outputs to SQLite + file bus
5. **Final Reasoning**: Calls the `runner_agent` once with worker outputs and planning context to generate the final answer
6. **Output**: Writes the runner agent's final answer to the file bus and returns it

## Available Agents

### market_data_agent
- **Purpose**: Execute SQL queries on market_data table
- **Keywords**: sql, market data, database, price, bid, ask, symbol
- **Inputs**: template, params, columns, limit
- **Outputs**: Market data records from database

### polymarket_agent
- **Purpose**: AI-powered Polymarket analysis (current state + historical comparison)
- **Keywords**: polymarket, prediction, forecast, probability, odds, historical, opinion, comparison, trend, analysis
- **Inputs**: query, session_id, limit
- **Outputs**: Prediction market data with prices, volumes, and reasoning-style historical comparison

### runner_agent
- **Purpose**: Final consolidation and answer generation using GPT-5-class models
- **Keywords**: explain, summarize, compare, interpret, overall answer, narrative
- **Inputs**: query, worker_outputs, planning_table, run_id
- **Outputs**: Final natural-language answer plus structured reasoning metadata

## Input

```python
{
    "query": str,              # Natural language query
    "num_subtasks": int,       # Optional: number of subtasks (default: 5)
    "skip_validation": bool    # Optional: skip validation step (default: False)
}
```

## Output

```python
{
    "query": str,              # Original query
    "answer": str,             # Natural language answer
    "data": {                  # Consolidated data
        "by_agent": {},        # Results grouped by agent
        "summary": {}          # Summary metrics
    },
    "validation": {            # Validation result
        "valid": bool,
        "completeness_score": float,
        "issues": [],
        "suggestions": []
    },
    "metadata": {
        "run_id": str,
        "duration_ms": float,
        "total_tasks": int,
        "successful_tasks": int,
        "failed_tasks": int,
        "agents_used": [],
        "validation_passed": bool,
        "script_path": str,
        "unmappable_tasks": int
    },
    "worker_outputs": [],      # Full outputs from each worker
    "output_path": str         # Path to file bus output
}
```

## Example Usage

### Example 1: Simple Query

```python
from src.agents.orchestrator_agent import OrchestratorAgent

agent = OrchestratorAgent()
result = agent.run("What are current Bitcoin predictions?")

print(result['answer'])
# Query: What are current Bitcoin predictions?
# Results from 1 worker agent(s):
#
# polymarket_agent:
#   - Found 5 prediction markets
#   - Average probability: 65.3%
#   - Total volume: $1,250,000
```

### Example 2: Complex Multi-Agent Query

```python
result = agent.run(
    "What were Bitcoin predictions on Jan 1st and how do market data prices compare?"
)

# This will:
# 1. Decompose into 2 tasks:
#    - Get historical Bitcoin predictions (polymarket_agent)
#    - Get SQL market data for Bitcoin (market_data_agent)
# 2. Execute both in parallel
# 3. Consolidate results
# 4. Validate completeness
```

### Example 3: Custom Subtask Count

```python
result = agent.run(
    "Analyze Bitcoin, Ethereum, and AI prediction markets",
    num_subtasks=3  # Create up to 3 subtasks
)
```

## Task Mapping Logic

The orchestrator uses keyword matching to map tasks to agents:

1. **Check suggested agent**: If taskmaster suggests an agent, use it
2. **Keyword matching**: Score each agent based on keyword overlap
3. **Best match**: Select agent with highest score
4. **Parameter extraction**: Extract relevant parameters from task description

## Code Generation

Generated scripts include:

- **Imports**: Relevant agent classes
- **Task functions**: One async function per task
- **Main function**: Orchestrates execution with proper dependencies
- **Error handling**: Try-catch blocks for each task
- **Result collection**: Structured output format

## Parallel Execution

Tasks are executed in parallel when:
- No dependencies between tasks
- Independent data sources
- Different worker agents

Tasks are executed sequentially when:
- Dependencies exist (e.g., Task 2 depends on Task 1)
- Resource constraints

## Validation

The validation layer checks:

1. **Taskmaster validation**: Uses AI to verify answer completeness
2. **Local checks**:
   - Answer length (minimum 50 characters)
   - Task failure count
   - Worker data availability
   - Query keyword coverage

Validation fails if:
- All tasks failed
- Answer too short
- Low keyword overlap with query
- No worker outputs available

## File Bus Integration

### Output Files
```
workspace/agents/orchestrator-agent/
├── out/
│   └── 000001.json         # Consolidated results
├── logs/
│   └── 20251114_120000.json  # Run logs
└── generated_scripts/
    └── orchestration_20251114_120000.py  # Generated execution script
```

### Output Format
Follows standard file bus schema:
- `data`: List with single orchestration result
- `metadata`: Standard fields + orchestration metadata
- Atomic writes using temp file + rename

## Error Handling

### Unmappable Tasks
If a task cannot be mapped to any agent:
- Task is marked as `mappable: false`
- Warning is logged
- Task is skipped during execution
- Reported in metadata

### Task Failures
If a worker agent fails:
- Error is captured in task result
- Other tasks continue execution
- Failure is reported in validation
- Included in consolidated output metadata

### Script Execution Errors
If generated script fails:
- Error is logged
- Run log records failure
- Exception is raised to caller

## Configuration

See `config.py` for:
- Agent capabilities registry
- Execution timeouts
- Directory structure
- Default settings

## Dependencies

- **taskmaster-ai**: npm package for task planning/validation
- **asyncio**: Python stdlib for parallel execution
- **subprocess**: For script execution (optional)
- Existing worker agents and their dependencies

## Performance

Typical execution times:
- Task planning: 1-3 seconds (taskmaster API call)
- Task mapping: < 100ms
- Code generation: < 100ms
- Script execution: Depends on worker agents (2-30 seconds)
- Consolidation: < 100ms
- Validation: 1-3 seconds (taskmaster API call)

**Total**: 5-40 seconds depending on query complexity

## Notes

- First run may be slower (npm package download)
- Requires ANTHROPIC_API_KEY for taskmaster
- Can run without validation (skip_validation=True)
- Generated scripts are saved for debugging
- All operations logged at INFO level

