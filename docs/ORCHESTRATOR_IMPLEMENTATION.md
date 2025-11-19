# Orchestrator Agent Implementation

## Overview

The Orchestrator Agent is a meta-agent that coordinates multiple worker agents to answer complex queries requiring data from different sources.  
The current implementation uses a **Two-Stage Planner Architecture**:

- **Planner 1**: AI-powered task decomposition + dependency analysis (via `TaskPlannerClient`)
- **Planner 2**: Tool discovery and parameter extraction per dependency path (lazy loading)
- **Coder**: Script generation that executes worker agents and writes to SQLite + file bus
- **Workers**: Existing agents (`market_data_agent`, `polymarket_agent`)
- **Runner**: DB-based consolidation and AI validation

Legacy single-stage components (`code_generator.py`, `consolidator.py`) are retained only for tests and backwards compatibility; the production path uses the two-stage system.

## Architecture

### Components

**Primary two-stage components**

```
src/agents/orchestrator_agent/
├── __init__.py              # Package initialization
├── config.py                # Configuration and agent registry
├── run.py                   # Main OrchestratorAgent (two-stage planner)
├── task_mapper.py           # Task-to-agent mapping logic
├── dependency_analyzer.py   # DAG analysis + dependency paths
├── planner_stage1.py        # Planner 1: task decomposition + dependencies
├── planner_stage2.py        # Planner 2: tool discovery per path
├── coder.py                 # Script generation (async + DB/file bus)
├── worker_executor.py       # Executes generated scripts
├── workers_db.py            # SQLite database (worker_runs, task_outputs, task_plan)
├── runner.py                # DB-based result consolidation
├── validator.py             # Answer validation layer
└── prompt.md                # Orchestrator instructions and examples

src/mcp/
└── taskmaster_client.py     # TaskPlannerClient (AI planning/validation client)

scripts/
└── test_orchestrator.py     # CLI testing tool

tests/e2e/
└── test_orchestrator_e2e.py # End-to-end tests
```

**Legacy single-stage components (used only in tests)**

- `src/agents/orchestrator_agent/code_generator.py` – Original dynamic script generator (replaced by `coder.py`)
- `src/agents/orchestrator_agent/consolidator.py` – Original result consolidator (replaced by `runner.py`)

### Workflow (Two-Stage Planner)

```
┌───────────────────────────────────────────────────────────────┐
│                    Natural Language Query                     │
└────────────────────────┬──────────────────────────────────────┘
                         │
                         ▼
┌───────────────────────────────────────────────────────────────┐
│  STAGE 1: TASK PLANNING (Planner 1 + TaskMapper + DAG)       │
│  • TaskPlannerClient: AI-powered decomposition               │
│  • TaskMapper: agent assignment + parameter extraction       │
│  • DependencyAnalyzer: DAG + dependency paths                │
│  • WorkersDB.task_plan: initial planning table               │
└────────────────────────┬──────────────────────────────────────┘
                         │
                         ▼
┌───────────────────────────────────────────────────────────────┐
│  STAGE 2: TOOL DISCOVERY (Planner 2, per dependency path)    │
│  • ToolLoader: lazy tool loading per path                    │
│  • PlannerStage2: tools + tool_params per task               │
│  • WorkersDB.task_plan: enrich with tools + parameters       │
└────────────────────────┬──────────────────────────────────────┘
                         │
                         ▼
┌───────────────────────────────────────────────────────────────┐
│  CODE GENERATION (Coder)                                     │
│  • Generate async Python scripts per path                    │
│  • Embed DB writes (worker_runs + task_outputs)             │
│  • Respect dependencies (topological ordering)               │
└────────────────────────┬──────────────────────────────────────┘
                         │
                         ▼
┌───────────────────────────────────────────────────────────────┐
│  EXECUTION (WorkerExecutor + Agents)                         │
│  • Execute scripts (asyncio)                                 │
│  • Run worker agents (market_data, polymarket, reasoning)    │
│  • Write outputs to file bus + SQLite                        │
└────────────────────────┬──────────────────────────────────────┘
                         │
                         ▼
┌───────────────────────────────────────────────────────────────┐
│  CONSOLIDATION (Runner)                                      │
│  • Read all task_outputs from SQLite                         │
│  • Merge results by agent type                               │
│  • Generate summary statistics + natural language answer     │
└────────────────────────┬──────────────────────────────────────┘
                         │
                         ▼
┌───────────────────────────────────────────────────────────────┐
│  VALIDATION (AnswerValidator)                                │
│  • AI validation (via TaskPlannerClient)                     │
│  • Local completeness checks                                 │
│  • Validation report                                         │
└────────────────────────┬──────────────────────────────────────┘
                         │
                         ▼
┌───────────────────────────────────────────────────────────────┐
│  OUTPUT (File Bus + Return)                                  │
│  • Write consolidated result to file bus                     │
│  • Return result dict (includes planning_table + metadata)   │
└───────────────────────────────────────────────────────────────┘
```

## Worker Agent Registry

The orchestrator knows about two worker agents:

### 1. market_data_agent
- **Purpose**: Execute SQL queries on market_data table
- **Keywords**: sql, market data, database, query, price, bid, ask, symbol
- **Capabilities**:
  - SQL queries on market_data table
  - Filter by symbol patterns
  - Filter by date
  - Retrieve bid/ask prices

### 2. polymarket_agent
- **Purpose**: Search Polymarket prediction markets with AI-powered reasoning
- **Keywords**: polymarket, prediction market, prediction, forecast, probability, odds, betting, historical, opinion, comparison, trend, analysis, sentiment, change, evolution
- **Capabilities**:
  - Search Polymarket markets
  - Get market prices and probabilities
  - Retrieve volume and liquidity data
  - LLM-powered relevance scoring
  - Parse natural language queries with date extraction
  - Compare current vs historical market states
  - Sort by relevance and volume
  - Flag low volume markets

## Usage

### Command Line

```bash
# List sample queries
python scripts/test_orchestrator.py --list

# Run a sample query
python scripts/test_orchestrator.py --query 4

# Run a custom query
python scripts/test_orchestrator.py --custom "What were Bitcoin predictions on Jan 1st?"

# Skip validation for faster execution
python scripts/test_orchestrator.py --query 4 --skip-validation

# Specify number of subtasks
python scripts/test_orchestrator.py --query 4 --num-subtasks 3
```

### Python API

```python
from src.agents.orchestrator_agent import OrchestratorAgent

# Initialize agent
agent = OrchestratorAgent()

# Run orchestration
result = agent.run(
    query="What were Bitcoin predictions on Jan 1st and how do market data prices compare?",
    num_subtasks=5,
    skip_validation=False
)

# Access results
print(result['answer'])
print(f"Validation passed: {result['validation']['valid']}")
print(f"Agents used: {result['metadata']['agents_used']}")
```

## Output Format

```python
{
    "query": str,              # Original query
    "answer": str,             # Natural language answer
    "data": {                  # Consolidated data
        "by_agent": {
            "agent_name": [...data...]
        },
        "summary": {
            "market_data": {...},
            "polymarket": {...},
            "reasoning": {...}
        }
    },
    "validation": {            # Validation result
        "valid": bool,
        "completeness_score": float,
        "issues": [...],
        "suggestions": [...],
        "local_checks": {...}
    },
    "metadata": {
        "run_id": str,
        "duration_ms": float,
        "total_tasks": int,
        "successful_tasks": int,
        "failed_tasks": int,
        "agents_used": [...],
        "validation_passed": bool,
        "script_path": str,
        "unmappable_tasks": int
    },
    "worker_outputs": [...],   # Full outputs from each worker
    "output_path": str         # File bus output path
}
```

## Configuration

### Environment Variables

Required in `config/keys.env`:

```bash
# Required for taskmaster
ANTHROPIC_API_KEY=your_anthropic_key

# Optional
OPENAI_API_KEY=your_openai_key
```

### Agent Capabilities

Edit `src/agents/orchestrator_agent/config.py` to:
- Add new worker agents
- Modify keyword mappings
- Adjust execution timeouts
- Change default settings

## Task Mapping Logic

### Keyword-Based Matching

The TaskMapper scores each agent based on keyword overlap:

```python
def _calculate_match_score(task_desc, keywords):
    score = 0
    for keyword in keywords:
        if keyword in task_desc.lower():
            score += 1
    return score
```

### Parameter Extraction

For each agent, specific parameters are extracted:

**market_data_agent**:
- Symbol patterns (e.g., "BTC" → `%BTC%`)
- Dates (e.g., "2025-01-01")
- Template selection

**polymarket_agent**:
- Query text
- Result limit
- Auto-generated session ID

## Code Generation

### Generated Script Structure

```python
"""
Auto-generated orchestration script.
"""

import asyncio
from src.agents.market_data_agent.run import MarketDataAgent
from src.agents.polymarket_agent.run import PolymarketAgent

async def task_1():
    """Task 1: Search polymarket"""
    agent = PolymarketAgent()
    output_path = agent.run(query="Bitcoin")
    # ... read and return results
    
async def task_2():
    """Task 2: Query market data"""
    agent = MarketDataAgent()
    output_path = agent.run(template="all_valid")
    # ... read and return results

async def main():
    """Main orchestration - parallel execution"""
    results = await asyncio.gather(task_1(), task_2())
    return list(results)
```

### Parallel vs Sequential

- **Parallel**: Tasks with no dependencies run simultaneously
- **Sequential**: Tasks with dependencies run in order
- **Mixed**: Groups of parallel tasks run sequentially

## Validation

### Taskmaster Validation

Uses AI to check if answer addresses query:
- Completeness assessment
- Accuracy verification
- Suggestions for improvement

### Local Checks

1. **Answer Length**: Minimum 50 characters
2. **Task Failures**: Count and report failed tasks
3. **Worker Data**: Ensure data is available
4. **Keyword Coverage**: Query terms appear in answer

### Validation Report

```
=============================================================
VALIDATION REPORT
=============================================================
Status: ✓ PASSED
Completeness Score: 85.0%
Method: taskmaster

Issues Found: None

Suggestions (1):
  1. Consider adding more context about methodology

Local Checks Performed: answer_length, task_failures, worker_data_availability, keyword_coverage
=============================================================
```

## File Bus Integration

### Directory Structure

```
workspace/agents/orchestrator-agent/
├── out/
│   ├── 000001.json          # Consolidated results
│   ├── 000002.json
│   └── meta.json            # Manifest
├── logs/
│   ├── 20251114_120000.json # Run log
│   └── 20251114_120500.json
└── generated_scripts/
    ├── orchestration_20251114_120000.py
    └── orchestration_20251114_120500.py
```

### Output Schema

Follows standard file bus format:
- Atomic writes (temp file + rename)
- JSON schema validation
- Incremental IDs via manifest

## Error Handling

### Unmappable Tasks

If a task can't be mapped to any agent:
- Task is marked `mappable: false`
- Warning logged
- Task skipped during execution
- Reported in metadata
- Does not cause failure

### Worker Failures

If a worker agent fails:
- Error captured in task result
- Other tasks continue
- Failure reported in validation
- Included in consolidated metadata
- Validation may fail depending on severity

### Taskmaster Errors

If taskmaster unavailable:
- Falls back to rule-based planning
- Falls back to simple validation
- Logs warning about fallback mode
- Still produces results

## Performance

Typical execution times:

| Step | Time | Notes |
|------|------|-------|
| Task Planning | 1-3s | Taskmaster API call |
| Task Mapping | <100ms | Local keyword matching |
| Code Generation | <100ms | String template generation |
| Script Execution | 2-30s | Depends on worker agents |
| Consolidation | <100ms | Data merging |
| Validation | 1-3s | Taskmaster API call |
| **Total** | **5-40s** | Varies by complexity |

### Optimization Tips

1. **Skip validation** for faster iteration: `skip_validation=True`
2. **Reduce subtasks**: Fewer tasks = faster execution
3. **Cache results**: Workers use file bus (already cached)
4. **Parallel execution**: Automatic when possible

## Testing

### Run E2E Tests

```bash
# All orchestrator tests
python -m pytest tests/e2e/test_orchestrator_e2e.py -v

# Specific test class
python -m pytest tests/e2e/test_orchestrator_e2e.py::TestSimpleOrchestration -v

# Single test
python -m pytest tests/e2e/test_orchestrator_e2e.py::TestSimpleOrchestration::test_polymarket_only_query -v
```

### Test Coverage

- ✅ Agent initialization
- ✅ Workspace structure
- ✅ Simple single-agent queries
- ✅ Task mapping (keyword-based)
- ✅ Code generation (parallel and sequential)
- ✅ Result consolidation
- ✅ Validation layer
- ✅ File bus output
- ✅ Manifest incrementation
- ✅ Run logging
- ✅ Error handling

## Extending the Orchestrator

### Adding a New Worker Agent

1. **Create the agent** in `src/agents/new_agent/`

2. **Register in config.py**:

```python
AGENT_CAPABILITIES = {
    "new_agent": {
        "keywords": ["keyword1", "keyword2"],
        "description": "Agent description",
        "class": "NewAgent",
        "module": "src.agents.new_agent.run",
        "input_params": ["param1", "param2"],
        "capabilities": ["capability 1", "capability 2"]
    }
}
```

3. **Add parameter extraction** in `task_mapper.py`:

```python
def _extract_new_agent_params(self, task_desc: str) -> Dict[str, Any]:
    return {"param1": value1, "param2": value2}
```

4. **Test**: Run orchestrator with queries that should map to new agent

### Customizing Task Planning

Replace taskmaster with custom logic in `run.py`:

```python
def _plan_tasks(self, query: str, num_subtasks: int) -> Dict[str, Any]:
    # Your custom planning logic
    return {
        "query": query,
        "subtasks": [...],
        "execution_order": [[...]]
    }
```

### Custom Validation

Replace/extend validation in `validator.py`:

```python
def _perform_local_checks(self, query, answer, consolidated_result):
    # Your custom validation logic
    return {
        "issues": [...],
        "suggestions": [...],
        "critical_issues": bool
    }
```

## Troubleshooting

### Taskmaster Not Available

**Symptom**: "Taskmaster error" in logs

**Solution**:
- Check `ANTHROPIC_API_KEY` in `config/keys.env`
- Verify `npx` is installed
- Check network connectivity
- Falls back to rule-based planning automatically

### All Tasks Fail

**Symptom**: `successful_tasks: 0` in metadata

**Solution**:
- Check worker agent logs
- Verify database availability (for market_data_agent)
- Verify API keys (for polymarket/reasoning agents)
- Review generated script for errors

### Low Validation Score

**Symptom**: `completeness_score < 0.5`

**Solution**:
- Review validation issues/suggestions
- Check if all relevant agents were used
- Verify worker outputs contain expected data
- Consider adjusting query phrasing

### Script Generation Errors

**Symptom**: "Generated script missing main()" or syntax errors

**Solution**:
- Review `code_generator.py` templates
- Check task mapping correctness
- Verify agent parameters format
- Check generated script in `workspace/agents/orchestrator-agent/generated_scripts/`

## Limitations

1. **First run delay**: Taskmaster npm package download (one-time)
2. **API dependencies**: Requires external API keys
3. **Sequential fallback**: Complex dependencies may not parallelize well
4. **Limited reasoning**: Fallback planning is rule-based
5. **No cross-agent communication**: Workers don't share data directly

## Future Enhancements

1. **Smarter parameter extraction**: Use LLM to parse task descriptions
2. **Dynamic agent discovery**: Auto-detect available agents
3. **Result caching**: Cache worker outputs to avoid re-execution
4. **Streaming results**: Return partial results as tasks complete
5. **Interactive refinement**: Allow user feedback to refine tasks
6. **Cost tracking**: Monitor API usage and costs
7. **Performance metrics**: Detailed timing for each step

## Summary

The Orchestrator Agent provides a powerful meta-agent pattern for coordinating multiple specialized agents. Key benefits:

✅ **Intelligent decomposition**: AI-powered task planning
✅ **Automatic mapping**: Keyword-based agent selection
✅ **Parallel execution**: Independent tasks run concurrently
✅ **Comprehensive validation**: Multi-layer answer checking
✅ **File bus integration**: Follows existing patterns
✅ **Extensible**: Easy to add new worker agents
✅ **Robust**: Fallback mechanisms for failures

The system is production-ready with comprehensive error handling, logging, and testing.

