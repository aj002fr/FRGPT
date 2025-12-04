# Runner Agent

## Purpose

The `runner_agent` is a standalone GPT-5-powered agent responsible for the
**final consolidation and answer generation** step in the orchestration flow.

It:

- Takes the original user query.
- Reads structured outputs from worker agents (market data, Polymarket, etc.).
- Optionally uses the orchestrator planning table for additional context.
- Produces the final user-facing answer in a clear, concise, and well-structured format.

## Inputs

The orchestrator calls the agent with:

- `query`: original natural language query.
- `worker_outputs`: list of worker output records from `WorkersDB`.
- `planning_table` (optional): task planning metadata (dependencies, agents).
- `run_id` (optional): orchestration run identifier for logging/traceability.

## Output

The agent writes a standard file-bus artifact to:

- `workspace/agents/runner-agent/out/{id:06d}.json`

Structure:

```json
{
  "data": [{
    "final_answer": "<string>",
    "reasoning_metadata": {
      "provider": "openai",
      "model": "gpt-5",
      "temperature": 0.2,
      "max_tokens": 1500
    },
    "worker_outputs": [...],
    "planning_table": [...]
  }],
  "metadata": {
    "query": "RunnerAgent final answer for: <user query>",
    "timestamp": "2025-11-25T12:00:00Z",
    "row_count": 1,
    "agent": "runner-agent",
    "version": "1.0",
    "run_id": "<run id>"
  }
}
```

## Behavior

- Uses the same API key loading pattern as the orchestrator planners
  (`config/keys.env` + environment variables).
- Prefers OpenAI with a GPT-5-class model and falls back to a structured
  non-LLM summary if no client is available.
- Avoids dumping raw JSON; instead, it surfaces key statistics and qualitative
  conclusions while pointing back to the underlying worker data when useful.


