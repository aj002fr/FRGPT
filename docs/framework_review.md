# Market Data Orchestrator — Framework Review & Optimization Plan

**Date**: 2026-02-24  
**Scope**: Full architectural critique, optimization roadmap, and Cursor integration assessment  
**Verified against**: All source files in `src/`, `scripts/`, `config/`, `memory-bank/`

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [What Works Well](#2-what-works-well)
3. [What Needs Fixing](#3-what-needs-fixing)
4. [Optimization Plan — Speed](#4-optimization-plan--speed)
5. [Optimization Plan — Accuracy & Coverage](#5-optimization-plan--accuracy--coverage)
6. [Cursor as Backend — Assessment](#6-cursor-as-backend--assessment)
7. [Recommended Hybrid Architecture](#7-recommended-hybrid-architecture)
8. [Implementation Priority Matrix](#8-implementation-priority-matrix)

---

## 1. Executive Summary

The framework is a **well-structured, domain-rich agentic system** for a macro rates desk. It covers event distributions, live market data, predictive markets, news search, Twitter monitoring, Bloomberg integration, and FOMC sentiment — a rare breadth of coverage for a custom-built system.

**The core issue is latency-efficiency mismatch**: every query — from "what is TY at?" to "compare NFP vs CPI surprise distributions with Polymarket odds" — goes through the same 5-stage pipeline requiring 4+ LLM roundtrips. The recommended fix is a **tiered query router** that short-circuits simple queries while preserving the full pipeline for genuinely complex ones.

Cursor cannot replace the runtime backend (it's an IDE, not a server), but its **MCP protocol support** offers a natural integration path: expose data sources as MCP servers so both your Telegram bot **and** Cursor users on the desk can query the same tools.

---

## 2. What Works Well

### A. Two-Stage Planner Architecture ✅

The separation of Stage 1 (task decomposition + DAG) from Stage 2 (per-path tool discovery) is a strong design:

```
Planner1: "What needs to happen?" → Task graph with dependencies
Planner2: "Which tools for each path?" → Tool selection with lazy loading
```

This keeps LLM context windows small — Planner 1 sees only agent descriptions, Planner 2 sees only the tools for its specific path. Most frameworks dump every tool signature into one prompt, which degrades planning quality.

**Verified**: `src/agents/orchestrator_agent/planner_stage1.py` calls `TaskPlannerClient.plan_task()` with agent descriptions only, then `planner_stage2.py` loads tools per dependency path.

### B. Domain Coverage ✅

The system covers 7+ data source integrations with rich analytical capabilities:

| Data Source | Implementation | Status |
|---|---|---|
| Market data (OHLCV) | PostgreSQL `ohlcv_1m` via `run_query` tool | ✅ Production |
| Economic events | SQLite `economic_events.db` + Trading Economics API | ✅ Production |
| Polymarket odds | Gamma API + CLOB midpoint via `search_polymarket_markets` | ✅ Production |
| Bloomberg data | Excel scraping via xlwings + `bbg_data` table | ✅ Production |
| X/Twitter | Filtered stream via `x_macro_stream_bot.py` | ✅ Production |
| News/web search | Perplexity API via `perplexity_search.py` | ✅ Production |
| Live prices | Databento via `databento_live_state.py` | ✅ Production |

### C. Event-Driven Analytics Ecosystem ✅

The standalone dashboard ecosystem is exceptionally rich for a rates desk:

- **Pre/post-event analysis**: NFP, CPI, PPI, JOLTS, ISM Services, ISM Manufacturing
- **Two-factor PCA**: CPI and NFP factor bucketing with drilldowns
- **FOMC sentiment**: Real-time press conference scoring + historical transcript alignment
- **Polymarket scenarios**: Live odds → surprise bucket → projected price paths
- **Geopolitical events**: 21-event analysis with normalized price paths
- **Auction supply**: 20Y auction field regression + return analysis
- **Holiday patterns**: US holiday candlestick dashboard with Tier-1 event overlays

### D. Plugin System & Soft Dependencies ✅

**Verified**: `@register_tool` in `src/mcp/discovery.py` supports:
- `soft_dependencies`: Auto-injected prerequisite tasks (e.g., candlestick chart auto-fetches economic events)
- `agent` parameter: Explicit agent assignment
- Git submodule plugin support via `src/servers/analytics/plugins/`

This means new tools can be added without modifying orchestrator code.

### E. Operational Maturity ✅

- Run logging to SQLite + file artifacts (`src/core/run_store.py`)
- Telegram feedback loop with reply-to context (`src/core/telegram_feedback_store.py`)
- DSPy/GEPA prompt optimization harnesses (`scripts/helpers/planner1_gepa_opt.py`, `planner2_gepa_opt.py`)
- Scheduled proactive event alerts (`scripts/scheduled_event_runner.py`)
- Redis pre-alert streaming service (`src/services/redis_event_stream/`)

### F. Follow-Up Context ✅ (Partial)

**Verified**: `telegram_bot.py` line 178-209 implements `_build_followup_context()` which detects reply-to messages, loads the previous query and answer via `read_run_final_answer()`, and prepends conversation context to the new query. This is functional but limited — it only carries text, not data artifacts or intermediate results.

---

## 3. What Needs Fixing

### A. ❌ Every Query Goes Through 5-Stage Pipeline (4+ LLM Calls)

**Verified in `src/agents/orchestrator_agent/run.py`**:

```
Stage 1 (line 104): self.planner1.plan()           → LLM call (task decomposition)
Stage 2 (line 185): planner2.discover_tools_and_params() → LLM call (per path)
Stage 3 (line 262): self.coder.generate()           → LLM call (code generation)
Stage 4 (line 311): executor.execute_all()          → subprocess exec
Stage 5 (line 331): runner_agent.run()              → LLM call (consolidation)
```

For a simple query like "when is next NFP?" or "show me TY 5-min candles today", this pipeline pays ~15-30 seconds of LLM latency. There is **no fast path** — confirmed by grep showing zero matches for `classify`, `router`, `route_query`, or `fast_path` in the orchestrator codebase.

**Impact**: Traders asking quick questions during market hours experience unacceptable latency.

### B. ❌ Code Generation Stage Is an Expensive Indirection

**Verified in `src/agents/orchestrator_agent/coder.py`** (line 760):

The generated script contains:
```python
tool_func = get_tool("{tool_name}")
result_{tool_name} = tool_func(**tool_params_{tool_name})
```

The Coder generates Python scripts that call `get_tool()` — **the same function the orchestrator could call directly**. The indirection adds:
1. An LLM call to generate the script
2. File I/O to write the script to disk
3. `exec()` in an isolated namespace (line 155 of `worker_executor.py`)
4. `asyncio.run(main())` inside that namespace

For single-tool tasks (which represent the majority of plans), this is pure overhead. The generated script's value is real only when tasks have complex inter-dependencies or data transformations that need custom code.

### C. ❌ Duplicate Capability Registries

**Verified**: Two separate `AGENT_CAPABILITIES` dicts exist:

1. `config/settings.py` line 127–195: Contains `polymarket_agent`, `market_data_agent`, `eventdata_puller_agent`, `analytics_agent`, `runner_agent`, `orchestrator_agent`
2. `src/agents/orchestrator_agent/config.py` line 40–360: Contains `market_data_agent`, `polymarket_agent`, `runner_agent`, `eventdata_puller_agent`, `analytics_agent`, `anomaly_notes_agent` — **with different schema** (includes `keywords`, `class`, `module`, `execution_mode`, `reasoning_enabled`, `param_descriptions`)

The orchestrator uses only its own config. The `config/settings.py` version is legacy but still imported by other modules, creating drift risk: `anomaly_notes_agent` exists only in orchestrator config, `orchestrator_agent` exists only in settings.

### D. ❌ No Query-Result Caching at Orchestrator Level

**Verified**: The only caching in the orchestrator is tool-loading cache in `tool_loader.py` (line 187). There is no result cache for identical or similar queries. Common patterns like "show me today's events" or "what's TY at?" hit the full pipeline every time.

**Dashboard-level caches exist** (L2 caches in unified dashboard for event details, candlesticks, ad-hoc sessions), but the orchestrator itself has none.

### E. ❌ No Stage-by-Stage Progress Streaming

**Verified in `telegram_bot.py`** (line 753):

```python
processing_msg = await update.message.reply_text(
    f"Processing your query...\n\n"
    f"Query: {query[:200]}{'...' if len(query) > 200 else ''}\n\n"
    f"This may take 1-2 minutes."
)
```

A single processing message is sent at the start, then deleted when complete. The user sees nothing for 15-60 seconds. There are no intermediate updates like "Planning tasks…", "Fetching market data…", "Analyzing…".

### F. ❌ Follow-Up Context Is Text-Only

**Verified in `telegram_bot.py`** (line 178-209):

`_build_followup_context()` builds a text string:
```
=== CONVERSATION CONTEXT ===
Previous Query: ...
Previous Answer: ...
================================
Follow-up Question: ...
```

This carries only the previous text answer (capped at `MAX_FOLLOWUP_PREV_ANSWER_CHARS = 2000`). It does **not** carry:
- The actual data artifacts from the previous run
- The planning table or worker outputs
- Any plots or computed statistics

So "now compare that with CPI" after an NFP query starts from scratch — the follow-up prompt tells the LLM about the previous answer but doesn't give it the underlying data.

### G. ❌ Standalone Dashboards Are Disconnected from Query Pipeline

The ~20+ standalone dashboard generators (NFP post-event, CPI two-factor PCA, FOMC meetings, geo analysis, JOLTS event study, etc.) are separate scripts that cannot be invoked by the orchestrator. If a user asks "show me the NFP two-factor dashboard for today's release" via Telegram, the orchestrator has no tool to dispatch to `nfp_twofactor_pca_dashboard.py`.

The `/pre_event` and `/post_event` Telegram commands are hardcoded shortcuts — they bypass the orchestrator entirely by directly calling `build_preevent_dashboard()` or running specific scripts. This works but means natural language queries cannot trigger these capabilities.

### H. ❌ No Temporal Context Injection

**Verified**: Neither `PlannerStage1` nor `PLANNER1_PROMPT_CORE` inject current market context (time, upcoming events, recent events, current price, session status) into the planning prompt. The planner prompt is rich in structural patterns but has no awareness of "right now."

For a trading desk, this means the planner cannot distinguish between "what just happened?" (check last 30 minutes + news) and "what should I expect?" (upcoming events + historical distributions) without the user being very explicit.

### I. ⚠️ `exec()` Security Surface

**Verified in `worker_executor.py`** (line 155):

```python
namespace = {}
exec(script_code, namespace)
```

Generated scripts are executed via `exec()` in an isolated namespace. While the scripts are LLM-generated (not user-input), this creates a surface for prompt injection if user queries can influence the generated code in unintended ways. The Coder does filter parameters against tool signatures (line 769 of `coder.py`), which mitigates but doesn't eliminate the risk.

---

## 4. Optimization Plan — Speed

### Priority 1: Query Router (Fast Path Bypass)

**Expected impact**: 5-15x faster for 60-70% of queries  
**Effort**: Medium (2-3 days)

Add a lightweight query classifier **before** the orchestrator pipeline:

```
┌─────────────────────────────────────────────────┐
│                   QUERY ROUTER                   │
│                                                  │
│  Tier 1: DIRECT (no LLM needed)                │
│    "what is TY at?"       → live price API      │
│    "when is next NFP?"    → DB lookup           │
│    "show today's events"  → DB query + format   │
│    → Response in <1 second                      │
│                                                  │
│  Tier 2: SIMPLE (1 LLM call, direct tool call) │
│    "show me NFP history"  → tool call + format  │
│    "TY candles today"     → tool call + chart   │
│    → Response in 3-5 seconds                    │
│                                                  │
│  Tier 3: COMPLEX (full pipeline)                │
│    Multi-source, multi-step analytical queries   │
│    → Current pipeline: 15-60 seconds            │
└─────────────────────────────────────────────────┘
```

**Implementation approach**: The router itself can be a small LLM call (~0.5s with GPT-4o-mini) or a rule-based classifier using keyword patterns. Given your existing `AGENT_CAPABILITIES` keyword lists, a hybrid approach works: pattern-match first, LLM-classify only if uncertain.

### Priority 2: Direct Tool Dispatch (Kill Codegen for Simple Tasks)

**Expected impact**: -3-5 seconds per query for Tier 2+  
**Effort**: Medium (2-3 days)

After Planner 2 identifies `{tool_name, tool_params}`, call the tool function directly instead of generating a script:

```python
# Current flow (slow):
#   Planner2 → {tool: "run_query", params: {symbol: "TY%"}}
#   Coder.generate() → writes script.py (LLM call)
#   WorkerExecutor → exec(script.py) → calls get_tool("run_query")(**params)

# Proposed flow (fast):
#   Planner2 → {tool: "run_query", params: {symbol: "TY%"}}
#   DirectExecutor → get_tool("run_query")(**params)  # No codegen
```

**Keep Coder for**: Tasks with inter-task data dependencies, custom transformations, or `"none"` agent processing steps. The Coder's value is real for complex DAGs where intermediate data flows between tasks.

**Decision rule**: If a path has ≤2 tasks and no `"none"` agent tasks, use direct dispatch. Otherwise, use Coder.

### Priority 3: Response Streaming to Telegram

**Expected impact**: Perceived latency → near-zero  
**Effort**: Easy (1 day)

Update `_process_query()` to send stage-by-stage status updates:

```python
async def _process_query(update: Update, query: str) -> None:
    status_msg = await update.message.reply_text("🔍 Classifying query...")
    
    tier = classify_query(query)
    
    if tier == 1:
        result = await direct_lookup(query)
        await status_msg.edit_text(result)
        return
    
    await status_msg.edit_text("🧠 Planning analysis...")
    plan = planner1.plan(query)
    
    await status_msg.edit_text(
        f"📊 Executing {plan['metadata']['total_tasks']} tasks "
        f"across {len(plan['dependency_paths'])} paths..."
    )
    # ... execute ...
    
    await status_msg.edit_text("✍️ Generating answer...")
    answer = runner.run(...)
    
    await status_msg.delete()
    await update.message.reply_text(answer)
```

### Priority 4: Query-Result Cache

**Expected impact**: Instant for repeated queries  
**Effort**: Easy (0.5 day)

Add an LRU cache keyed by `(normalized_query_hash, time_bucket)`:

```python
from functools import lru_cache
import hashlib

CACHE_TTL = {
    "live":       30,    # "what's TY at?" — 30 seconds
    "today":      300,   # "today's events" — 5 minutes
    "historical": 3600,  # "NFP history" — 1 hour
}
```

For "what just happened?" queries, TTL is short (30s). For historical analysis, TTL can be long (1 hour+).

### Priority 5: Parallel Planner 2 Execution

**Expected impact**: -1-3 seconds for multi-path queries  
**Effort**: Easy (0.5 day)

**Currently**: Planner 2 instances run sequentially in a `for` loop (line 184 of `run.py`).  
**Fix**: Run them concurrently since they're independent per path:

```python
# Current (sequential):
for planner2 in planner2_instances:
    path_plan = planner2.discover_tools_and_params(...)

# Proposed (parallel):
import asyncio
path_plans = await asyncio.gather(*[
    loop.run_in_executor(None, p2.discover_tools_and_params, ...)
    for p2 in planner2_instances
])
```

### Combined Speed Projection

| Query Type | Current Latency | With Optimizations | Speedup |
|---|---|---|---|
| "What is TY at?" | 15-25s | <1s | ~20x |
| "When is next NFP?" | 15-25s | <1s | ~20x |
| "Show me NFP history" | 15-25s | 3-5s | ~5x |
| "TY candles on Dec 6" | 20-30s | 5-8s | ~4x |
| Complex multi-source | 30-60s | 20-40s | ~1.5x |

---

## 5. Optimization Plan — Accuracy & Coverage

### A. Temporal Context Injection

**Impact**: Better source prioritization, smarter planning  
**Effort**: Medium (1-2 days)

Inject a real-time market context snapshot into the Planner 1 prompt:

```python
def build_temporal_context() -> str:
    """Build current market context for planner."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    
    context = {
        "current_time_et": now_et.strftime("%Y-%m-%d %H:%M ET"),
        "market_session": get_current_session(now_et),  # "pre-market" / "US open" / "closed"
        "upcoming_events_24h": get_upcoming_events(hours=24),
        "recent_events_2h": get_recent_events(hours=2),
        "last_ty_price": get_live_ty_price(),
        "today_range_bps": get_today_range_bps(),
    }
    return json.dumps(context, indent=2)
```

Append this to the Planner 1 system prompt so it can distinguish:
- "what just happened?" → prioritize news/events in last 2 hours
- "what should I expect?" → prioritize upcoming events + historical distributions
- "show me [historical]" → standard historical query

### B. Register Dashboards as Orchestrator Tools

**Impact**: Dramatically increases query coverage  
**Effort**: Medium (2-3 days)

Register the key standalone dashboards as tools so the orchestrator can invoke them from natural language:

```python
# In a new file: src/servers/dashboards/__init__.py

@register_tool(
    name="generate_nfp_postevent_dashboard",
    description="Generate NFP post-event HTML dashboard with surprise bucketing",
    agent="analytics_agent",
    input_schema={
        "actual": {"type": "number", "description": "Actual NFP value (thousands)"},
        "consensus": {"type": "number", "description": "Consensus (thousands)"},
        "consensus_std_dev": {"type": "number", "description": "Consensus std dev"},
    }
)
def generate_nfp_postevent_dashboard(actual, consensus, consensus_std_dev):
    from scripts.non_farm_payrolls_postevent_dashboard import build_postevent_dashboard
    return build_postevent_dashboard(actual=actual, consensus=consensus, ...)

@register_tool(
    name="generate_preevent_dashboard",
    description="Generate pre-event analysis dashboard for any supported event",
    agent="analytics_agent",
    input_schema={
        "event_name": {"type": "string", "enum": ["NFP", "CPI", "PPI"]},
        "consensus": {"type": "number"},
        "cons_std": {"type": "number"},
    }
)
def generate_preevent_dashboard(event_name, consensus=None, cons_std=None):
    from scripts.preevent_boxplot_dashboard import build_preevent_dashboard
    return build_preevent_dashboard(event_name=event_name, ...)
```

**Priority candidates to register**:
1. `build_preevent_dashboard` (NFP/CPI/PPI)
2. `build_postevent_dashboard` variants (NFP, ISM, JOLTS)
3. `nfp_twofactor_pca_dashboard.py` / `cpi_twofactor_pca_dashboard.py`
4. `geo_analysis.py` (geopolitical events)
5. `fomc_meetings_dashboard.py`

### C. Unify Capability Registries

**Impact**: Eliminates maintenance drift  
**Effort**: Easy (0.5 day)

Make `src/agents/orchestrator_agent/config.py` the single source of truth. In `config/settings.py`, replace the duplicate dict with an import:

```python
# config/settings.py — REPLACE duplicate AGENT_CAPABILITIES with:
from src.agents.orchestrator_agent.config import AGENT_CAPABILITIES
```

Or if circular imports are an issue, extract the registry to a standalone `src/agents/registry.py` that both files import.

### D. Enrich Follow-Up Context

**Impact**: Better multi-turn accuracy  
**Effort**: Medium (1-2 days)

Extend `_build_followup_context()` to include structured data from the previous run:

```python
def _build_followup_context(run_id: str, new_query: str) -> str:
    prev_query = read_run_query_text(run_id)
    prev_answer = read_run_final_answer(run_id)
    
    # NEW: Load previous worker outputs summary
    prev_outputs = load_worker_outputs_summary(run_id)  # Compact: tool names + key stats
    
    # NEW: Load previous planning table
    prev_plan = load_planning_table_summary(run_id)  # Tasks + agents used
    
    context = f"""=== CONVERSATION CONTEXT ===
Previous Query: {prev_query}
Previous Answer: {prev_answer[:2000]}
Previous Data Sources: {prev_outputs}
Previous Plan: {prev_plan}
================================

Follow-up Question: {new_query}

INSTRUCTION: If the follow-up references data from the previous query 
(e.g., "compare that with...", "now show..."), reuse the same data sources 
and parameters where applicable.
"""
    return context
```

### E. Tool Output Validation Layer

**Impact**: Catches bad data before it reaches the Runner  
**Effort**: Easy (1 day)

Add a validation step between worker execution and Runner consolidation:

```python
def validate_worker_output(task_id: str, output: dict) -> dict:
    """Validate and clean worker output before consolidation."""
    
    # Check for empty data
    if not output.get("data"):
        output["_validation"] = "WARNING: Empty data returned"
    
    # Check for TY price anomalies (known bad-tick issue)
    if "prices" in output:
        prices = output["prices"]
        valid = [p for p in prices if 80 <= p <= 150]
        if len(valid) < len(prices):
            output["_validation"] = f"Filtered {len(prices)-len(valid)} bad ticks"
            output["prices"] = valid
    
    # Check for stale data
    if "timestamp" in output:
        age = (datetime.now() - parse(output["timestamp"])).total_seconds()
        if age > 86400:
            output["_validation"] = f"Data is {age/3600:.0f}h old"
    
    return output
```

### F. Confidence Scoring

**Impact**: Users know answer reliability  
**Effort**: Easy (add to Runner prompt)

Modify the Runner agent's system prompt to include confidence scoring:

```
Rate your confidence in this answer on a scale of 1-5:
- 5: All data sources returned clean, complete data; answer is definitive
- 4: Most data available; minor gaps acknowledged
- 3: Partial data; answer includes caveats
- 2: Significant data gaps; answer is speculative
- 1: Insufficient data; answer should be treated as a rough estimate

Include a "Confidence: X/5" line and a brief explanation of any limitations.
```

---

## 6. Cursor as Backend — Assessment

### Can Cursor Replace the Framework Runtime?

**Answer: No.** Cursor is a code editor with AI assistance, not a server platform.

| Requirement | Your Framework | Cursor |
|---|---|---|
| Always-on Telegram webhook listener | ✅ Python process (`telegram_bot.py`) | ❌ Desktop IDE, must be open |
| Persistent database state | ✅ PostgreSQL + SQLite + Redis | ❌ No persistent runtime |
| Real-time data streams | ✅ Databento, WebSocket, X filtered stream | ❌ Not designed for streams |
| Multi-user concurrent access | ✅ Async Python | ❌ Single-user IDE session |
| Headless / server deployment | ✅ Any Linux box, Docker, etc. | ❌ Requires desktop GUI |
| Sub-second data lookups | ✅ Direct DB queries | ❌ Routes through LLM |
| Scheduled proactive alerts | ✅ APScheduler, cron, Redis publisher | ❌ No scheduler |
| Production uptime / monitoring | ✅ Standard ops tooling | ❌ IDE uptime ≠ server uptime |

**Cursor Background Agents** (announced late 2025) run code changes in sandboxed cloud VMs — they're designed for multi-file code refactoring tasks, not for serving real-time financial queries. They have no programmatic API that can be called from Telegram or a dashboard.

### What Cursor IS Good For

1. **Accelerating agent development**: Use Cursor to rapidly write new tools, dashboards, and analysis scripts. Your existing `.cursor/commands/` playbooks (`pre_event.md`, `post_event.md`, `distro_adhoc.md`) are exactly right.

2. **Interactive desk-side queries**: When a PM or analyst has Cursor open, they can ask Claude (via Cursor chat) to query your data sources if you expose them as MCP servers. This is a natural complement to Telegram — not a replacement.

3. **Prompt optimization**: Use Cursor's AI to iterate on planner prompts, test query decompositions, and validate tool parameter extraction.

### MCP Server Integration — The Right Hybrid

The **Model Context Protocol (MCP)** is the key bridge. Your framework already uses MCP-like patterns (`@register_tool`, tool discovery, schema-driven dispatch). The opportunity:

**Build thin MCP server wrappers** around your existing tool functions:

```python
# mcp_servers/market_data_server.py
# Standard MCP server that wraps your existing tools

from mcp.server import Server
from src.mcp.discovery import get_tool

server = Server("market-data")

@server.tool("get_ohlcv")
async def get_ohlcv(symbol: str, date: str, limit: int = 1000):
    """Get OHLCV data for a symbol on a date."""
    tool_fn = get_tool("run_query")
    return tool_fn(
        template="by_symbol",
        params={"symbol_pattern": f"{symbol}%", "date": date},
        limit=limit
    )

@server.tool("get_events")
async def get_events(event_name: str, lookback_days: int = 365):
    """Get economic event history."""
    tool_fn = get_tool("query_event_history")
    return tool_fn(event_name=event_name, lookback_days=lookback_days)
```

Then configure Cursor to discover these MCP servers:

```json
// .cursor/mcp.json
{
  "servers": {
    "market-data": {
      "command": "python",
      "args": ["mcp_servers/market_data_server.py"]
    },
    "events": {
      "command": "python",
      "args": ["mcp_servers/events_server.py"]
    },
    "polymarket": {
      "command": "python",
      "args": ["mcp_servers/polymarket_server.py"]
    }
  }
}
```

**Result**: Analysts with Cursor can ask Claude "what was TY's range around last NFP?" and Claude calls the MCP server → your existing tool → PostgreSQL → formatted answer. Same data, same tools, different UI.

---

## 7. Recommended Hybrid Architecture

```
┌───────────────────────────────────────────────────────────┐
│                      USER INTERFACES                       │
│                                                           │
│  Telegram Bot        Unified Dashboard        Cursor IDE  │
│  (mobile/desk)       (browser, charts)        (analysts)  │
└──────┬────────────────────┬───────────────────────┬───────┘
       │                    │                       │
       ▼                    ▼                       ▼
┌──────────────┐    ┌──────────────┐     ┌─────────────────┐
│ QUERY ROUTER │    │  FastAPI     │     │  MCP Protocol   │
│              │    │  /api/*      │     │  (Cursor calls   │
│ Tier 1: Direct    │  Dashboard   │     │   these servers) │
│ Tier 2: Simple    │  APIs        │     │                 │
│ Tier 3: Full  │   │              │     │                 │
└──────┬────────┘   └──────┬───────┘     └────────┬────────┘
       │                   │                      │
       ▼                   ▼                      ▼
┌───────────────────────────────────────────────────────────┐
│              ORCHESTRATOR (Tier 3 only)                    │
│                                                           │
│  Planner1 → Planner2 → Coder (complex only) → Runner     │
│                          ↕                                │
│               Direct Executor (simple tasks)              │
└──────────────────────────┬────────────────────────────────┘
                           │
                           ▼
┌───────────────────────────────────────────────────────────┐
│              SHARED TOOL LAYER                             │
│                                                           │
│  run_query        │ query_event_history │ search_markets  │
│  run_multi_date   │ search_events      │ get_price_hist  │
│  annotate_range   │ get_next_event     │ search_news     │
│  compute_returns  │ compute_volatility │ compute_probmat │
│  generate_chart   │ gen_multi_charts   │ plot_distro     │
│  build_preevent_dashboard    │ build_postevent_dashboard  │
│  nfp_twofactor_dashboard     │ cpi_twofactor_dashboard    │
└──────────────────────────────┬────────────────────────────┘
                               │
                               ▼
┌───────────────────────────────────────────────────────────┐
│                      DATA LAYER                            │
│                                                           │
│  PostgreSQL       │ SQLite              │ Redis           │
│  (ohlcv_1m)       │ (economic_events.db)│ (alerts stream) │
│                   │ (polymarket_markets)│                 │
│                   │ (metadata.db)       │                 │
│                                                           │
│  Databento API    │ Trading Economics   │ Perplexity API  │
│  (live prices)    │ (event calendar)    │ (news search)   │
│                                                           │
│  X API v2         │ Bloomberg Excel     │ PricingMonkey   │
│  (filtered stream)│ (xlwings scraping)  │ (breakevens)    │
└───────────────────────────────────────────────────────────┘
```

---

## 8. Implementation Priority Matrix

| # | Change | Speed Impact | Accuracy Impact | Effort | Priority |
|---|---|---|---|---|---|
| 1 | **Query Router** (Tier 1/2/3 classification) | ★★★★★ | ★★☆☆☆ | 2-3 days | **P0** |
| 2 | **Direct Tool Dispatch** (bypass codegen for simple) | ★★★★☆ | ★☆☆☆☆ | 2-3 days | **P0** |
| 3 | **Response Streaming** (stage updates in Telegram) | ★★★★☆ (perceived) | ★☆☆☆☆ | 1 day | **P1** |
| 4 | **Temporal Context Injection** | ★☆☆☆☆ | ★★★★☆ | 1-2 days | **P1** |
| 5 | **Register Dashboards as Tools** | ★★☆☆☆ | ★★★★★ | 2-3 days | **P1** |
| 6 | **Query-Result Cache** | ★★★☆☆ | ★☆☆☆☆ | 0.5 day | **P2** |
| 7 | **Unify Capability Registries** | ★☆☆☆☆ | ★★☆☆☆ | 0.5 day | **P2** |
| 8 | **Parallel Planner 2** | ★★☆☆☆ | ★☆☆☆☆ | 0.5 day | **P2** |
| 9 | **Enriched Follow-Up Context** | ★☆☆☆☆ | ★★★☆☆ | 1-2 days | **P2** |
| 10 | **Tool Output Validation** | ★☆☆☆☆ | ★★★☆☆ | 1 day | **P2** |
| 11 | **Confidence Scoring** | ★☆☆☆☆ | ★★☆☆☆ | 0.5 day | **P3** |
| 12 | **MCP Server Wrappers** (Cursor integration) | ★☆☆☆☆ | ★★☆☆☆ | 2-3 days | **P3** |

### Recommended Execution Order

**Sprint 1 (Week 1)**: Items 1 + 2 → Query router + direct dispatch  
**Sprint 2 (Week 2)**: Items 3 + 4 + 6 → Streaming + temporal context + cache  
**Sprint 3 (Week 3)**: Items 5 + 7 + 8 → Dashboard tools + registry unify + parallel P2  
**Sprint 4 (Week 4)**: Items 9 + 10 + 11 + 12 → Follow-up context + validation + MCP

---

## Appendix: Key File References

| Component | File | Lines of Interest |
|---|---|---|
| Orchestrator pipeline | `src/agents/orchestrator_agent/run.py` | 74-405 (full `run()` method) |
| Planner 1 prompt | `src/mcp/taskmaster_client.py` | 232-430 (`PLANNER1_PROMPT_CORE`) |
| Code generation | `src/agents/orchestrator_agent/coder.py` | 26-787 (script generation) |
| Worker execution | `src/agents/orchestrator_agent/worker_executor.py` | 43-164 (exec-based execution) |
| Runner consolidation | `src/agents/runner_agent/run.py` | 34-80 (`RunnerAgent`) |
| Telegram query handler | `scripts/telegram_bot.py` | 746-920 (`_process_query`) |
| Follow-up context | `scripts/telegram_bot.py` | 178-209 (`_build_followup_context`) |
| Tool registry | `src/mcp/discovery.py` | 21-79 (`@register_tool`) |
| Capability registry (orchestrator) | `src/agents/orchestrator_agent/config.py` | 40-360 |
| Capability registry (settings) | `config/settings.py` | 127-195 |
| Tool loader | `src/agents/orchestrator_agent/tool_loader.py` | 187-331 |
