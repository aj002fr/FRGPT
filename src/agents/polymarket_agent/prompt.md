# Polymarket Agent

## Purpose

Producer agent that searches Polymarket prediction markets using natural language queries.

## Inputs

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | str | Yes | Natural language search query |
| session_id | str | No | Session identifier (auto-generated if not provided) |
| limit | int | No | Maximum results (default: 10, max: 50) |

## Outputs

### File Bus Output

Location: `workspace/agents/polymarket-agent/out/{id:06d}.json`

```json
{
  "data": [{
    "query": "Will Bitcoin reach $100k?",
    "session_id": "20251113143022_a3f2e9",
    "search_method": "hybrid_llm",
    "llm_scoring_enabled": true,
    "markets": [
      {
        "market_id": "0x123...",
        "title": "Bitcoin to reach $100,000 by end of 2025?",
        "description": "Resolves YES if Bitcoin reaches $100k...",
        "outcomes": ["Yes", "No"],
        "prices": {"Yes": 0.65, "No": 0.35},
        "volume": 1250000,
        "liquidity": 85000,
        "status": "active",
        "url": "https://polymarket.com/event/...",
        "close_time": "2025-12-31T23:59:59Z",
        "relevance_score": 0.85,
        "relevance_reason": "Directly matches Bitcoin price prediction query"
      }
    ],
    "result_count": 5
  }],
  "metadata": {
    "query": "Polymarket search: Will Bitcoin reach $100k?",
    "timestamp": "2025-11-13T14:30:22Z",
    "row_count": 1,
    "agent": "polymarket-agent",
    "version": "1.0"
  }
}
```

### Run Log

Location: `workspace/agents/polymarket-agent/logs/{run_id}.json`

```json
{
  "run_id": "20251113_143022",
  "query": "Will Bitcoin reach $100k?",
  "session_id": "20251113143022_a3f2e9",
  "output_path": "workspace/agents/polymarket-agent/out/000001.json",
  "status": "success",
  "result_count": 5,
  "timestamp": "2025-11-13T14:30:22Z",
  "duration_ms": 2134.56,
  "agent": "polymarket-agent",
  "version": "1.0"
}
```

## Process Flow

1. **Validate Input**: Check query is non-empty, limit is within bounds
2. **Generate Session ID**: Create unique session identifier if not provided
3. **Call MCP Tool**: Invoke `search_polymarket_markets` tool
   - Tool queries Polymarket API (hybrid: recent + popular markets)
   - Tool performs fast keyword filtering (~50 candidates)
   - Tool uses GPT-4 to re-rank by semantic relevance (if API key available)
   - Tool stores results in database
4. **Get Results**: Receive market data with relevance scores/reasons
5. **Write to File Bus**: Atomic write to `out/{id}.json`
6. **Write Run Log**: Log execution details to `logs/{run_id}.json`
7. **Return**: Output file path

## Session ID Format

Format: `{timestamp}_{random_hash}`

Example: `20251113143022_a3f2e9`

- `timestamp`: YYYYMMDDHHmmss (UTC)
- `random_hash`: 3 bytes of random hex

## Example Usage

```python
from src.agents.polymarket_agent.run import PolymarketAgent

agent = PolymarketAgent()

# Run with custom query
output_path = agent.run(
    query="Will federal shutdown end by November 15?",
    limit=10
)

# Run with existing session
output_path = agent.run(
    query="Bitcoin price prediction 2025",
    session_id="20251113143022_a3f2e9",
    limit=5
)
```

## Key Features

- **LLM-Powered Relevance**: Uses GPT-4 to score semantic relevance (0-1 scale with explanations)
- **Hybrid Search**: Fast keyword filter + accurate LLM re-ranking
- **Graceful Fallback**: Falls back to keyword-only if no OpenAI API key
- **Multi-User Support**: Session IDs enable query tracking across users
- **Historical Data**: All queries stored in `polymarket_markets.db`
- **Atomic Operations**: Crash-safe file writes
- **Full Audit Trail**: Every run logged with metadata

## Database Storage

All queries and results are stored in `polymarket_markets.db` table `prediction_queries`:

- session_id
- user_query
- expanded_keywords (JSON, legacy field - empty array)
- results (JSON, includes relevance_score and relevance_reason if LLM used)
- market_ids (JSON)
- avg_probability
- total_volume
- result_count
- timestamp

## Integration

- **File Bus**: Compatible with existing consumer agents
- **MCP Tools**: Uses `search_polymarket_markets` tool
- **Schema Validation**: Follows standard output schema
- **Manifest System**: Incremental file IDs (000001.json, 000002.json, ...)

