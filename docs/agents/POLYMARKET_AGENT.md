# Polymarket Agent

## Overview

The Polymarket Agent is a producer agent that searches Polymarket prediction markets using natural language queries with **no LLM reasoning**. It calls the Polymarket Gamma `/markets` API to fetch active markets sorted by volume, then applies local keyword filtering and strong validation to return only high-volume markets that are textually relevant to the query. This keeps the agent fast, deterministic, and free of API token requirements.

## Architecture

```
User Query → PolymarketAgent → Polymarket `/markets` API (volume-sorted)
                    ↓                     ↓
              Session ID Gen        Active Markets
                    ↓                     ↓
              MCP Client         Keyword + Volume Filter
                    ↓                     ↓
    call_polymarket_search_api   High-Volume Relevant Markets
                    ↓                     ↓
       polymarket_markets.db   File Bus (out/*.json)
                    ↓
        Run Log (logs/{run_id}.json)
```

## Key Features

### 1. Direct Polymarket API Integration

- **Gamma API**: Primary endpoint for market discovery
- **No authentication required** for read operations
- Fetches popular markets sorted by volume
- Returns comprehensive market data:
  - Market ID, title, description
  - Outcomes (Yes/No or multiple)
  - Prices (probability-based)
  - Volume and liquidity
  - Status and close time
  - Slug for correct URL generation

### 2. Local Keyword + Volume Filtering

Markets are scored and filtered locally using:
- **Keyword matching** in question/title/description (case-insensitive, word-level)
- **High-volume filter**: require volume above a configurable threshold
- **Active-only**: ignore closed/resolved or zero-activity markets

### 3. Data Validation
- ✅ Required fields present (title, prices)
- ✅ Prices are valid dictionaries with values in \[0, 1]
- ✅ Volume is non-negative number (when present)

### 5. Database Storage

All queries stored in `polymarket_markets.db`:
- Original query
- Market results (JSON)
- Market IDs
- Aggregated metrics (avg probability, total volume)
- Session tracking
- Timestamp





## Input Parameters

| Parameter | Type | Required | Default | Max | Description |
|-----------|------|----------|---------|-----|-------------|
| query | str | Yes | - | - | Natural language search query |
| session_id | str | No | Auto-generated | - | Unique session identifier |
| limit | int | No | 10 | 50 | Maximum results to return |

## Output Format

### File Bus Output

**Location**: `workspace/agents/polymarket-agent/out/{id:06d}.json`

```json
{
  "data": [{
    "query": "Will Bitcoin reach $100k?",
    "session_id": "20251113143022_a3f2e9",
    "markets": [
      {
        "market_id": "0x123...",
        "title": "Bitcoin to reach $100,000 by end of 2025?",
        "description": "Resolves YES if Bitcoin reaches $100k",
        "outcomes": ["Yes", "No"],
        "prices": {"Yes": 0.65, "No": 0.35},
        "volume": 1250000,
        "liquidity": 85000,
        "status": "active",
        "url": "https://polymarket.com/event/bitcoin-100k-2025",
        "slug": "bitcoin-100k-2025",
        "close_time": "2025-12-31T23:59:59Z"
      }
    ],
    "result_count": 5
  }],
  "metadata": {
    "query": "Polymarket search: Will Bitcoin reach $100k?",
    "search_method": "phrase_and_keyword_matching",
    "timestamp": "2025-11-13T14:30:22Z",
    "row_count": 1,
    "agent": "polymarket-agent",
    "version": "1.0"
  }
}
```

### Run Log

**Location**: `workspace/agents/polymarket-agent/logs/{run_id}.json`

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

## Tools Used

### search_polymarket_markets

Main tool for searching Polymarket markets:

1. **Fetch Popular Markets**: Queries Polymarket Gamma API for up to 300 active markets sorted by volume
2. **Keyword Extraction**: Strips punctuation, splits on hyphens and spaces, filters common words
3. **Local Filtering**: Applies phrase and keyword matching with word boundary detection
4. **Relevance Scoring**: Scores markets based on phrase matches, keyword frequency, and field importance
5. **Validation**: Ensures all markets have valid URLs, prices, volumes, and required fields
6. **Database Storage**: Stores query and results in `polymarket_markets.db`
7. **Returns**: Top N markets sorted by relevance score

**GPT-4 powered** - intelligent reasoning and analysis with structured output.

### get_polymarket_history

Retrieves historical queries from database:

- Filter by session_id
- Filter by date range
- Limit number of results
- Returns full query history with market results

## Database Schema

**Database**: `polymarket_markets.db`
**Table**: `prediction_queries`

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PRIMARY KEY | Auto-incrementing ID |
| session_id | TEXT | Unique session identifier |
| user_query | TEXT | Original user query |
| expanded_keywords | TEXT | Empty (legacy field) |
| timestamp | TEXT | Query timestamp (ISO-8601) |
| results | TEXT | JSON string of market data |
| platform | TEXT | Platform name ('polymarket') |
| market_ids | TEXT | JSON array of market IDs |
| avg_probability | REAL | Average probability across markets |
| total_volume | INTEGER | Total volume across markets |
| result_count | INTEGER | Number of markets returned |
| created_at | TEXT | Record creation timestamp |

**Indices**:
- `idx_session_id` on `session_id`
- `idx_timestamp` on `timestamp`
- `idx_platform` on `platform`

## Error Handling

### Validation Errors

```python
# Empty query
ValueError: "Query cannot be empty"

# Empty session ID
ValueError: "Session ID cannot be empty"

# Invalid limit
ValueError: "limit must be between 1 and 50"
```

### API Errors

- **Connection errors**: Logged and raised with details
- **HTTP errors**: Status code and error body logged
- **Cloudflare blocks**: Browser-like headers automatically added
- **Gzip encoding**: Automatically detected and decompressed

### Data Validation

Markets automatically filtered if they fail validation:
- Missing required fields (title, URL, prices, volume, slug)
- Invalid URL format
- Invalid price format
- Negative volume
- Closed or resolved status
- Zero activity (volume and liquidity both 0)

## Performance

- **API call**: ~500-1000ms (depends on Polymarket)
- **Market filtering**: ~50-150ms (local processing, 300 markets)
- **Relevance scoring**: ~20-50ms (regex + scoring)
- **Data validation**: ~10-30ms (per market)
- **Database write**: ~5-10ms (SQLite)
- **Total duration**: ~600-1200ms per query

**GPT-4 analysis** - adds ~2-4s for intelligent reasoning and relevance scoring.

## Integration

### With Consumer Agent

Output format is compatible with existing consumer agents:

```python
from src.agents.consumer_agent.run import ConsumerAgent

# Run Polymarket agent
polymarket_agent = PolymarketAgent()
output_path = polymarket_agent.run(query="Bitcoin predictions")

# Process with consumer
consumer = ConsumerAgent()
consumer_output = consumer.run(input_path=output_path)
```

### With File Bus

Uses standard file bus operations:
- Atomic writes (crash-safe)
- Manifest-driven incremental IDs
- Schema validation
- Run logging

## Configuration

**File**: `src/agents/polymarket_agent/config.py`

```python
AGENT_NAME = "polymarket-agent"
AGENT_VERSION = "1.0"
DEFAULT_MAX_RESULTS = 10
MAX_RESULTS = 50
SESSION_ID_HASH_LENGTH = 3  # 3 bytes = 6 hex chars
```

## Testing

Run E2E tests:

```bash
pytest tests/e2e/test_polymarket_e2e.py -v
```

Test classes:
1. Agent initialization and session IDs
2. Market search and local filtering
3. Database integration
4. Multi-user session isolation
5. Manifest increments
6. Run logs and error handling
7. Data validation and URL correctness

## Troubleshooting

### No Results Returned

**Possible Causes**:
1. No markets exist on Polymarket for this topic
2. Keywords are too specific
3. All matching markets are inactive/closed

**Solutions**:
- Try broader query terms (e.g., "Bitcoin" instead of "Bitcoin price $100k prediction")
- Check polymarket.com directly to verify markets exist
- Try removing punctuation manually if seeing issues
- Check logs for filtered market count vs. returned count

### Wrong/Irrelevant Results

**Possible Causes**:
1. Query is too broad or generic
2. Not enough matching keywords (40% threshold)
3. Popular markets dominate by volume

**Solutions**:
- Use more specific terms
- Include multiple keywords: "Russia Ukraine ceasefire" instead of just "war"
- Use phrases that match market titles (check polymarket.com for exact wording)

### API Connection Errors

**Possible Causes**:
1. Network issues
2. Polymarket API down
3. Cloudflare blocking (rare with current headers)

**Solutions**:
- Check internet connection
- Check Polymarket API status
- Review logs for HTTP error codes
- Wait and retry (may be temporary)

### Database Not Found

**Cause**: Database not initialized

**Solution**:
```bash
python scripts/setup_polymarket_db.py
```

Or it will be auto-created on first run.

### URL Validation Failures

**Cause**: Markets with invalid slugs filtered out

**Solution**: This is expected behavior - invalid markets are automatically removed. Check logs for details.

## Future Enhancements

- **Historical price data**: Fetch price history per market
- **Rate limiting**: Implement exponential backoff for API calls
- **Result caching**: Cache API responses for repeated queries
- **Market metadata**: Fetch additional market metadata (tags, categories)
- **Multi-platform**: Extend to other prediction market platforms
- **Semantic search**: Optional LLM-based expansion for complex queries (behind flag)
- **Category filtering**: Filter by Polymarket categories (Politics, Crypto, Sports, etc.)

## References

- [Polymarket API Documentation](https://docs.polymarket.com/)
- [Polymarket Gamma API](https://gamma-api.polymarket.com/)
- [Polymarket Website](https://polymarket.com/)

