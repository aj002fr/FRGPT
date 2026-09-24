# Planner 2 Metrics

This document describes evaluation metrics for **Planner 2** (tool discovery, selection, and parameter refinement) in the Two-Stage Planner Architecture.

## Overview

### Stage Separation

| Stage | Responsibility |
|-------|----------------|
| **Planner 1** | Decomposes query into tasks, builds the task DAG, assigns each task to an agent, and provides coarse agent-level parameters |
| **Planner 2** | For each task, discovers available tools, selects which tools to call, decides tool execution order, and refines tool-level parameters |

### What Planner 2 Does

Planner 2 operates on the output of Planner 1. For each dependency path, it:

1. **Tool Discovery** — Loads only the tools relevant to agents in the path (lazy loading via `ToolLoader`)
2. **Tool Selection** — Uses AI reasoning to decide which specific tools handle parts of the task
3. **Parameter Refinement** — Maps coarse `agent_params` from Planner 1 to precise `tool_params` per tool
4. **Execution Ordering** — Determines the order in which tools are called within a task

**Example**: Planner 1 creates a task "analyze Bitcoin market sentiment" assigned to `polymarket_agent` with `agent_params: {query: "Bitcoin"}`. Planner 2 then:
- Discovers tools: `search_polymarket_markets`, `get_market_price_history`, `get_polymarket_history`
- Selects `search_polymarket_markets` first, then `get_market_price_history` for historical context
- Refines params: `{search_polymarket_markets: {query: "Bitcoin sentiment", limit: 10}, get_market_price_history: {date: "2025-01-01"}}`

---

## Metrics for Planner 2

### Primary Metrics (Tool-Level)

These metrics directly evaluate Planner 2's tool selection and parameter quality:

| Metric | Type | What It Measures for Planner 2 |
|--------|------|--------------------------------|
| **`agent_params_ok`** | bool | Whether tool-level parameters are well-formed (valid SQL templates, valid query strings, correct types) |
| **`tool_coverage`** | (implicit via `coverage_score`) | Whether the selected tools jointly cover all aspects of the Planner 1 task |
| **`silent_drop_count`** | int | Query/task aspects that Planner 2 failed to map to any tool call |
| **`misallocation_severity`** | str | How badly Planner 2 chose wrong tools: `"none"`, `"minor"`, `"moderate"`, `"severe"` |

### Supporting Metrics (From Planner 1 Context)

These metrics primarily reflect Planner 1 quality but provide context for Planner 2 evaluation:

| Metric | Type | Relevance to Planner 2 |
|--------|------|------------------------|
| `dag_ok` | bool | Upstream constraint — if False, Planner 2 received a broken input |
| `schema_validity_rate` | float | Upstream constraint — malformed tasks from Planner 1 limit what Planner 2 can do |
| `coverage_score` | float | Shared — low values may indicate Planner 1 dropped aspects OR Planner 2 failed to cover them with tools |
| `dependency_recall` / `dependency_precision` | float | For Planner 2: reflects whether tool execution order respects necessary data flow (e.g., search before analysis) |

### Semantic Metrics (LLM-as-Judge)

The LLM evaluator scores how well Planner 2's tool selections serve the original query:

| Metric | What It Tells You About Planner 2 |
|--------|-----------------------------------|
| `task_sufficiency_score` | Do the selected tools + params provide enough information to complete the task? |
| `task_precision_score` | Are tool params tight and relevant, or do they include unnecessary fields? |
| `faithfulness_score` | Do tool selections preserve the user's intent without hallucinating extra requirements? |
| `agent_mapping_accuracy` | Did Planner 2 select appropriate tools within the assigned agent's toolkit? |

---

## Combined Score

The `combine_structural_and_llm_scores()` function produces a single [0, 1] reward:

### Base Score

```
base = 0.20 * coverage_score
     + 0.15 * granularity_score
     + 0.15 * dependency_recall
     + 0.10 * dependency_precision
     + 0.10 * task_sufficiency_score
     + 0.10 * task_precision_score
     + 0.10 * faithfulness_score
     + 0.10 * agent_mapping_accuracy
```

### Planner 2–Specific Penalties

| Condition | Penalty | Why It Matters |
|-----------|---------|----------------|
| `agent_params_ok = False` | -0.15 | Malformed tool parameters will cause runtime failures |
| Per `silent_drop_count` | -0.05 each | Tool selection missed parts of the task |
| `misallocation_severity = "minor"` | -0.05 | Suboptimal tool choice |
| `misallocation_severity = "moderate"` | -0.15 | Wrong tool for the job |
| `misallocation_severity = "severe"` | -0.30 | Completely incorrect tool selection |

Upstream penalties (from Planner 1 issues):
- `dag_ok = False`: -0.30
- `schema_validity_rate < 0.5`: -(1 - rate) * 0.20

---

## Interpreting Planner 2 Failures

| Symptom | Key Metrics | Likely Planner 2 Issue |
|---------|-------------|------------------------|
| Tool call fails at runtime | `agent_params_ok = False` | Parameter refinement produced invalid values (wrong type, missing required field) |
| Task returns partial data | `silent_drop_count > 0`, low `task_sufficiency_score` | Planner 2 didn't select enough tools to cover the full task |
| Wrong tool used | `misallocation_severity != "none"`, low `agent_mapping_accuracy` | AI selected a tool that can't handle this part of the task |
| Tools called in wrong order | Low `dependency_recall` | Planner 2 didn't wire tool dependencies correctly (e.g., tried analysis before fetching data) |
| Unnecessary tool calls | Low `task_precision_score` | Planner 2 over-selected tools or added irrelevant parameters |

### Example: Diagnosing a Tool Selection Failure

**Query**: "Compare Bitcoin prediction markets to treasury futures prices"

Planner 1 creates two tasks:
- Task 1: `polymarket_agent` — "Bitcoin prediction markets"
- Task 2: `market_data_agent` — "treasury futures prices"

If Task 2 returns empty results:

1. Check `agent_params_ok`: If False, Planner 2 generated invalid SQL template params
2. Check `silent_drop_count`: If > 0, "treasury futures" wasn't mapped to a tool call
3. Check tool selections in `WorkersDB.task_plan`: Did Planner 2 select `run_query` with the right template?
4. Check `tool_params`: Is `symbol_pattern` correctly set to `%ZN%` or similar?

---

## When to Use These Metrics

### GEPA Prompt Optimisation

During offline optimisation (`scripts/helpers/planner_gepa_opt.py`):
- The combined score guides evolution of Planner 2 prompts
- Focus on reducing `silent_drop_count` and `misallocation_severity`
- Monitor `agent_params_ok` to catch parameter formatting issues

### Debugging Production Issues

When the orchestrator returns unexpected results:
1. Query `WorkersDB.task_plan` for the `tool_params` and `tools` columns
2. Check if `agent_params_ok` passed validation
3. Look at `misallocation_severity` in GEPA logs if available
4. Enable verbose logging in `test_orchestrator.py` to see AI tool selection reasoning

---

## Planner 1 vs Planner 2: Quick Reference

| Aspect | Planner 1 | Planner 2 |
|--------|-----------|-----------|
| **Decides** | What tasks, which agent | Which tools, what parameters, what order |
| **Input** | User query | Tasks + agent assignments from Planner 1 |
| **Output** | Task DAG with `agent_params` | Execution plan with `tool_params` per tool |
| **Key Metric** | `dag_ok`, `coverage_score` | `agent_params_ok`, `misallocation_severity` |
| **Debug Focus** | Task structure, agent assignment | Tool selection, parameter refinement |

---

## Related Files

- `src/agents/orchestrator_agent/planner_stage2.py` — Planner 2 implementation
- `src/agents/orchestrator_agent/planner2_metrics.py` — Planner 2 metric computation (GEPA optimization)
- `src/agents/orchestrator_agent/planner_metrics.py` — Planner 1 metric computation
- `src/agents/orchestrator_agent/tool_loader.py` — Lazy tool loading and AGENT_TOOL_MAP
- `scripts/helpers/planner_gepa_opt.py` — GEPA optimisation harness
