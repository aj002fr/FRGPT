# Orchestrator Agent - Quick Start Guide

Get started with the Orchestrator Agent in 5 minutes.

## Prerequisites

1. **Python 3.11+** installed
2. **API Keys** (recommended for intelligent planning):
   - OpenAI API key (GPT-4) **OR** Anthropic API key (Claude)
   - System works without keys (falls back to rule-based planning)

## Installation

### 1. Set Up API Keys

Create `config/keys.env`:

```bash
# For Orchestrator AI Planning (choose one or both)
OPENAI_API_KEY=sk-your-openai-key-here        # Option 1: Uses GPT-4
# OR
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key   # Option 2: Uses Claude

# Orchestrator tries OpenAI first, then Anthropic, then falls back to rules
# Having either key enables intelligent task decomposition and validation
```

**Note**: The orchestrator uses **direct AI API calls** (not the Task Master npm tool, which is for development workflow management).

### 2. Verify Installation

```bash
# Test that orchestrator can be imported
python -c "from src.agents.orchestrator_agent import OrchestratorAgent; print('✓ Orchestrator ready')"
```

## Basic Usage

### Command Line

```bash
# List sample queries
python scripts/test_orchestrator.py --list

# Run a simple query
python scripts/test_orchestrator.py --query 1

# Run a custom query
python scripts/test_orchestrator.py --custom "What are Bitcoin predictions?"
```

### Python

```python
from src.agents.orchestrator_agent import OrchestratorAgent

# Create agent
agent = OrchestratorAgent()

# Run query
result = agent.run("What are Bitcoin predictions?")

# Print answer
print(result['answer'])
```

## Example Queries

### Simple (Single Agent)

**Query 1**: Simple Polymarket search
```bash
python scripts/test_orchestrator.py --query 1
```
Query: "What are current Bitcoin predictions?"

**Query 2**: Simple SQL query
```bash
python scripts/test_orchestrator.py --query 2
```
Query: "Show me market data for Bitcoin symbols"

**Query 3**: Historical analysis
```bash
python scripts/test_orchestrator.py --query 3
```
Query: "What were Bitcoin predictions on January 1st 2025?"

### Complex (Multiple Agents)

**Query 4**: Multi-agent parallel execution ⭐ **RECOMMENDED**
```bash
python scripts/test_orchestrator.py --query 4
```
Query: "What were Bitcoin predictions on Jan 1st and how do market data prices compare?"

This will:
1. Decompose into 2 tasks
2. Execute both agents in parallel
3. Consolidate results
4. Validate answer completeness

## Understanding the Output

```
=======================================================================
ORCHESTRATION RESULTS
=======================================================================

Answer:
-----------------------------------------------------------------------
Query: What were Bitcoin predictions on Jan 1st and how do market data prices compare?
Results from 2 worker agent(s):

reasoning_agent:
  - Performed 1 analysis/analyses
  - Topics: Bitcoin
  - Dates analyzed: 2025-01-01

market_data_agent:
  - Found 42 market data records
  - Symbols: BTC.USD, BTC.EUR, ...

---
Detailed results are available in the worker_outputs section.

Metadata:
-----------------------------------------------------------------------
Run ID: 20251114_120500
Duration: 8234.56 ms
Total Tasks: 2
Successful: 2
Failed: 0
Agents Used: reasoning_agent, market_data_agent
Unmappable Tasks: 0

Validation:
-----------------------------------------------------------------------
Valid: True
Completeness Score: 92.5%
Issues: None

Output File: workspace/agents/orchestrator-agent/out/000001.json
Script: workspace/agents/orchestrator-agent/generated_scripts/orchestration_20251114_120500.py

=======================================================================
✓ ORCHESTRATION COMPLETED SUCCESSFULLY
=======================================================================
```

## Advanced Options

### Skip Validation (Faster)

```bash
python scripts/test_orchestrator.py --query 4 --skip-validation
```

Saves ~2-4 seconds by skipping AI validation step.

### Custom Subtask Count

```bash
python scripts/test_orchestrator.py --query 4 --num-subtasks 3
```

Limits decomposition to maximum 3 subtasks.

### Verbose Logging

```bash
python scripts/test_orchestrator.py --query 4 --verbose
```

Shows detailed DEBUG-level logs.

## Accessing Detailed Results

### In Python

```python
result = agent.run("Your query")

# Get consolidated answer
answer = result['answer']

# Get data by agent
data_by_agent = result['data']['by_agent']
polymarket_data = data_by_agent.get('polymarket_agent', [])
market_data = data_by_agent.get('market_data_agent', [])

# Get full worker outputs
for output in result['worker_outputs']:
    print(f"Agent: {output['agent']}")
    print(f"Status: {output['status']}")
    print(f"Data: {output['data']}")

# Check validation
validation = result['validation']
if validation['valid']:
    print(f"✓ Validation passed ({validation['completeness_score']*100:.1f}%)")
else:
    print(f"✗ Validation failed")
    for issue in validation['issues']:
        print(f"  - {issue}")
```

### From File Bus

```bash
# Read the output file
cat workspace/agents/orchestrator-agent/out/000001.json | python -m json.tool
```

## Troubleshooting

### "AI planning failed" or "No AI client available"

**Problem**: AI API call failed or no API key configured

**Solution**:
1. Check `config/keys.env` has `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`
2. Verify key is valid (starts with `sk-` for OpenAI, `sk-ant-` for Anthropic)
3. Check internet connection
4. Ensure openai or anthropic library is installed: `pip install openai` or `pip install anthropic`

**Fallback**: System automatically falls back to rule-based planning (works without AI, just less intelligent task decomposition)

### "No results obtained"

**Problem**: All worker agents failed

**Solution**:
1. Check worker agent logs in `workspace/agents/<agent-name>/logs/`
2. Verify databases exist (for market_data_agent)
3. Check API keys (for polymarket/reasoning agents)

### "Low validation score"

**Problem**: Answer may be incomplete

**Solution**:
1. Read validation issues: `result['validation']['issues']`
2. Check if all expected agents ran
3. Review worker outputs for missing data
4. Try rephrasing the query

## Testing

### Run E2E Tests

```bash
# All orchestrator tests
python -m pytest tests/e2e/test_orchestrator_e2e.py -v

# Quick smoke test
python -m pytest tests/e2e/test_orchestrator_e2e.py::TestSimpleOrchestration::test_polymarket_only_query -v
```

## Next Steps

1. **Try all sample queries**: `python scripts/test_orchestrator.py --list`
2. **Create custom queries**: Combine different agents
3. **Review generated scripts**: Check `workspace/agents/orchestrator-agent/generated_scripts/`
4. **Read full documentation**: See `docs/ORCHESTRATOR_IMPLEMENTATION.md`
5. **Add new agents**: Extend the orchestrator with custom workers

## Quick Reference

### CLI Commands

```bash
# List queries
python scripts/test_orchestrator.py --list

# Run sample
python scripts/test_orchestrator.py --query N

# Custom query
python scripts/test_orchestrator.py --custom "QUERY"

# Options
--skip-validation     # Skip validation step
--num-subtasks N      # Max subtasks
--verbose             # Debug logging
```

### Python API

```python
from src.agents.orchestrator_agent import OrchestratorAgent

agent = OrchestratorAgent()

result = agent.run(
    query="Your query here",
    num_subtasks=5,           # Optional
    skip_validation=False     # Optional
)
```

### Available Agents

- **market_data_agent**: SQL queries on market_data table
- **polymarket_agent**: Polymarket prediction markets
- **reasoning_agent**: AI-powered historical analysis

### Output Structure

```python
{
    "query": str,
    "answer": str,
    "data": {...},
    "validation": {...},
    "metadata": {...},
    "worker_outputs": [...],
    "output_path": str
}
```

## Support

- Full Documentation: `docs/ORCHESTRATOR_IMPLEMENTATION.md`
- Agent Prompt: `src/agents/orchestrator_agent/prompt.md`
- Example Queries: `scripts/test_orchestrator.py`
- Tests: `tests/e2e/test_orchestrator_e2e.py`

## Tips

💡 **Start simple**: Try query 1 first to verify setup
💡 **API keys optional**: System works without them (rule-based fallback)
💡 **Use skip-validation**: Faster iteration during development
💡 **Check generated scripts**: Great for understanding execution flow in `generated_scripts/`
💡 **Review logs**: Located in `workspace/agents/orchestrator-agent/logs/`
💡 **File bus**: All outputs follow standard schema
💡 **No npm needed**: Uses direct Python API calls (no Node.js required)

**Ready to orchestrate? Run your first query:**

```bash
python scripts/test_orchestrator.py --query 4
```

