# Reasoning Agent Prompt

## Purpose

Parse natural language queries about prediction markets and return:
1. **Current market state** (always)
2. **Historical comparison** (specified date OR past week)
3. **Sorted results** (relevance first, then volume)
4. **Volume flags** (mark low-volume markets)

## Simplified Approach (v2.0)

**No more intent classification!** Every query follows the same flow:
- Extract topic + optional date
- Search for relevant markets
- Show current prices
- Add historical comparison
- Sort by relevance & volume
- Flag low volume

## Input

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | Natural language query (e.g., "Bitcoin markets", "What was opinion on Jan 1 about AI?") |
| `session_id` | string | No | Optional session identifier |

## Output Structure

```json
{
  "query": "original user query",
  "parsed": {
    "topic": "extracted topic",
    "date": "YYYY-MM-DD or null",
    "confidence": 0.95
  },
  "comparison_date": "YYYY-MM-DD",
  "date_source": "specified | default",
  "markets": [
    {
      "title": "Market title",
      "url": "https://polymarket.com/...",
      "url_valid": true,
      "prices": {
        "Yes": 0.45,
        "No": 0.55
      },
      "volume": 125000,
      "volume_note": "Volume: $125,000",
      "low_volume_flag": false,
      "relevance_score": 0.95,
      
      "historical_price": {
        "yes": 0.60,
        "no": 0.40
      },
      "historical_date": "2025-01-01",
      "historical_note": "Data from Polymarket CLOB API",
      
      "price_change": {
        "yes_change": -15.0,
        "direction": "down"
      },
      
      "created_at": "2024-10-15T12:00:00Z"
    }
  ],
  "metadata": {
    "total_markets": 5,
    "low_volume_count": 1,
    "comparison_note": "Comparing current vs 2025-01-01 (specified)"
  }
}
```

## Query Examples

### Example 1: Simple Topic Search
**Input**: `"Bitcoin price predictions"`

**Process**:
1. Extract topic: "bitcoin price"
2. Date: None → default to 1 week ago
3. Search markets
4. Show current prices
5. Compare with 1 week ago
6. Sort by relevance & volume

**Output**: Current Bitcoin markets + 7-day price change

---

### Example 2: Date-Specific Query
**Input**: `"What was opinion on Jan 1 2025 about AI regulation?"`

**Process**:
1. Extract topic: "AI regulation"
2. Extract date: "2025-01-01"
3. Search markets
4. Show current prices
5. Compare with Jan 1, 2025
6. Sort by relevance & volume

**Output**: Current AI regulation markets + change since Jan 1

---

### Example 3: Low Volume Detection
**Input**: `"Federal shutdown markets"`

**Process**:
1. Extract topic: "federal shutdown"
2. Date: None → 1 week ago
3. Search markets
4. Identify markets with volume < $1,000
5. Flag them with warning

**Output**:
```json
{
  "markets": [
    {
      "title": "Federal shutdown market",
      "volume": 500,
      "low_volume_flag": true,
      "volume_note": "⚠️ Low volume ($500 < $1,000)",
      ...
    }
  ]
}
```

---

## Sorting Logic

Markets are sorted using two criteria (in order):

1. **Relevance Score** (primary)
   - Calculated by `search_polymarket_markets`
   - Based on keyword matching
   - Higher score = better match

2. **Volume** (secondary)
   - Total trading volume in USD
   - Higher volume = more liquid market
   - Tiebreaker when relevance is equal

## Volume Flagging

Markets with `volume < $1,000` are flagged:
- `low_volume_flag`: true
- `volume_note`: "⚠️ Low volume ($XXX < $1,000)"

Users should be cautious with low-volume markets as prices may be unreliable.

---

## Historical Comparison Logic

### When Date is Specified
```
User: "What was opinion on Nov 5 about Bitcoin?"
→ comparison_date = "2024-11-05"
→ date_source = "specified"
```

### When No Date Mentioned
```
User: "Bitcoin markets"
→ comparison_date = 7 days ago
→ date_source = "default"
```

### Historical Price Unavailable
Markets may not have historical data if:
- Market was created after comparison date
- Token ID not available
- API data missing

In these cases:
- `historical_price`: `{"yes": null, "no": null}`
- `historical_note`: Explanation of why data is unavailable
- `price_change`: null

---

## Configuration

| Setting | Value | Description |
|---------|-------|-------------|
| `LOW_VOLUME_THRESHOLD` | $1,000 | Flag markets below this volume |
| `DEFAULT_LOOKBACK_DAYS` | 7 | Days to look back if no date specified |
| `MAX_MARKETS_TO_RETURN` | 10 | Maximum markets in response |

---

## Benefits of Simplified Approach

1. **Consistent Output**: Every query returns current + historical
2. **No Intent Confusion**: Single flow for all queries
3. **Automatic Context**: Always show how things changed
4. **Better Sorting**: Relevance first, then liquidity
5. **Risk Awareness**: Flag low-volume markets

---

## Migration from v1.0

**Old (v1.0)**: Intent-based routing
- `current_search` → current only
- `historical_opinion` → past only
- `price_change` → range analysis
- `market_movement` → timing analysis

**New (v2.0)**: Unified approach
- All queries → current + historical
- Simpler parsing (topic + date)
- Automatic comparison
- Better sorting

---

## Usage in Code

```python
from src.agents.reasoning_agent import ReasoningAgent

agent = ReasoningAgent()

# Simple query - will compare with 1 week ago
result = agent.run("Bitcoin predictions")

# Date-specific query - will compare with specified date
result = agent.run("What was opinion on Jan 1 2025 about AI?")

# Both return: current state + historical comparison + sorted + flagged
```

---

## Error Handling

### No Markets Found
```json
{
  "markets": [],
  "error": "No markets found about 'topic'"
}
```

### URL Validation Failed
```json
{
  "url_valid": false,
  "url_note": "URL may be incorrect or market removed"
}
```

### Historical Data Unavailable
```json
{
  "historical_price": {"yes": null, "no": null},
  "historical_note": "Market created after 2025-01-01"
}
```

---

## Performance

- **GPT-4 Parse**: ~1-2s
- **Market Search**: ~500ms
- **Historical Prices**: ~300ms per market
- **Total**: ~5-8s for 10 markets with full comparison

---

**Version**: 2.0  
**Last Updated**: November 14, 2025

