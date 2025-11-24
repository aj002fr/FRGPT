# GEPA Optimization Guide

## Overview

This guide explains how to run GEPA (Generative Exploration and Programmatic Adaptation) optimization for the Orchestrator Planner using DSPy.

## What GEPA Does

GEPA automatically optimizes the planner's prompts and instructions to improve task decomposition quality by:
1. Generating candidate prompt variations
2. Evaluating each against a training set
3. Selecting the best-performing prompt based on structural and semantic metrics

## Prerequisites

```powershell
# Install dependencies
pip install dspy-ai openai

# Set your OpenAI API key
$env:OPENAI_API_KEY = "sk-..."
```

## Current Configuration (Heavy Budget)

- **Model**: `gpt-5` (used for both reflection and evaluation)
- **Max Full Evaluations**: 300 candidate programs
- **Threading**: Multi-threaded evaluation (4 threads by default)
- **Training Set**: 20 diverse planner queries covering:
  - Single-agent tasks (market data, Polymarket, web search, messages, events)
  - Multi-agent tasks with dependencies
  - Complex queries requiring coordination

## How to Run

### From Project Root (PowerShell)

```powershell
cd Z:\Aastha\market_data_puller

# Set Python path so 'src' is visible
$env:PYTHONPATH = (Get-Location)

# Activate venv (if using)
.\.venv\Scripts\Activate.ps1

# Run optimization
py .\scripts\planner_gepa_opt.py
```

### Custom Parameters

You can override defaults programmatically:

```powershell
py -c "from scripts.planner_gepa_opt import run_gepa_optimisation; run_gepa_optimisation(model_name='gpt-5', max_full_evals=300, num_threads=8, log_dir='my_logs')"
```

## What to Expect

### Timing
- Each evaluation includes:
  - Planner LLM call to generate task graph
  - LLM-as-judge call to score the output
  - Structural metrics computation
- With 20 training examples × 300 evaluations = **up to 6,000 LLM calls**
- Estimated time: **2-6 hours** depending on API rate limits and model speed

### Console Output

You'll see:
```
============================================================
GEPA Optimization Configuration:
  Model: gpt-5
  Max Full Evaluations: 300
  Num Threads: 4
  Log Directory: gepa_logs (auto)
============================================================
Loaded 20 training examples
Starting GEPA compilation (this may take a while)...
GEPA will evaluate up to 300 candidate programs
[DSPy progress logs...]
GEPA compilation complete!
Saved best planner prompt to scripts\gepa_logs\best_prompt.txt
Final score for query '...': 0.842
...
```

## Outputs

### 1. Best Prompt Text File

**Location**: `scripts/gepa_logs/best_prompt.txt`

Contains the optimized planner instruction/prompt that achieved the highest score.

### 2. Detailed Logs

**Location**: `scripts/gepa_logs/`

DSPy creates various log files with:
- Evaluation traces
- Candidate program history
- Score progression

## Evaluation Metrics

Each planner output is scored on:

### Structural Metrics (Python)
- Task count appropriateness
- Valid agent mappings
- Dependency structure
- Parameter completeness

### Semantic Metrics (LLM-as-judge)
- Coverage score (0-1): How completely tasks cover the query
- Granularity score (0-1): Appropriate task decomposition
- Dependency recall/precision (0-1): Correct dependencies
- Task sufficiency/precision (0-1): Adequate task detail
- Faithfulness score (0-1): No hallucinated requirements
- Agent mapping accuracy (0-1): Correct agent selection
- Silent drop count: Missing query aspects
- Misallocation severity: Wrong agent assignments

Final score: Weighted combination of all metrics → [0, 1]

## Robustness Features

The script is designed to handle LLM formatting issues gracefully:

1. **JSON Parsing**: Handles various formats (bare JSON, ```json``` wrappers, dict vs list)
2. **Default Scores**: If LLM-as-judge fails to return valid JSON, uses low default scores
3. **Error Recovery**: Evaluation failures return 0.0 instead of crashing
4. **Flexible Output**: Accepts both `{"subtasks": [...]}` and raw `[...]` formats

## Troubleshooting

### "No module named 'dspy'"
```powershell
pip install dspy-ai
```

### "No module named 'src'"
```powershell
$env:PYTHONPATH = (Get-Location)
```

### API Rate Limits
If you hit rate limits, GEPA will show errors. You can:
- Reduce `max_full_evals` (e.g., 100 instead of 300)
- Reduce `num_threads` (e.g., 2 instead of 4)
- Wait and retry

### Model Not Available
If `gpt-5` isn't available on your API key, change to `gpt-4o` or `gpt-4o-mini`:
```python
run_gepa_optimisation(model_name="gpt-4o", ...)
```

## Next Steps

After optimization completes:

1. **Review** `best_prompt.txt` to see the optimized instruction
2. **Integrate** the best prompt into `src/agents/orchestrator_agent/planner_stage1.py`
3. **Test** the orchestrator with the new prompt on real queries
4. **Measure** improvement on your production query set

## Cost Estimation

With `gpt-5` (assuming $10/1M input, $30/1M output tokens):
- ~6,000 LLM calls (planner + evaluator)
- ~2K tokens/call average
- Estimated cost: **$300-600** for full 300-eval run

For cheaper testing:
- Use `gpt-4o-mini` (~10x cheaper)
- Reduce to 50-100 evaluations

