# EventData Puller Agent

## Purpose

The EventData Puller Agent fetches and analyzes economic calendar data from the Trading Economics API. It maintains a local SQLite database of historical economic events and provides tools for:

- **Calendar Updates**: Fetch and update economic calendar data
- **Event Queries**: Query historical instances of specific events by ID or name
- **Correlation Analysis**: Find events that occurred within ±N hours of a target event
- **Live Streaming**: WebSocket connection for real-time event data (optional)

## Data Source

- **Provider**: Trading Economics (https://tradingeconomics.com)
- **API**: REST API for historical data, WebSocket for live events
- **Coverage**: Major economic indicators from 196 countries
- **Update Frequency**: Events updated as they are released

## Input Parameters

### Required (at least one for query actions)

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_id` | str | Event ID or ticker from Trading Economics (e.g., "USANFP", "UNITEDSTANONFAM") |
| `event_name` | str | Event name for partial match search (e.g., "Non-Farm Payrolls") |

### Optional

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | str | "query_event" | Action to perform (see Available Actions) |
| `country` | str | None | Country code filter (e.g., "US", "GB", "EU") |
| `lookback_timestamp` | str | None | Only return events after this date (ISO format) |
| `lookback_days` | int | None | Days to look back from today |
| `window_hours` | float | 12 | Hours before/after for correlation analysis |
| `target_event_date` | str | None | Specific date for correlation target |
| `importance` | str | None | Importance filter ("low", "medium", "high") |
| `limit` | int | 100 | Maximum results to return |
| `update_calendar` | bool | False | Update calendar before querying |
| `include_correlations` | bool | True | Include correlated events in query_event |

## Available Actions

1. **`update_calendar`**: Fetch/update economic calendar from API
2. **`query_event`**: Query event history by ID/name with optional correlations
3. **`find_correlations`**: Find events within ±window_hours of target event
4. **`search_events`**: Search events by keyword, category, or country
5. **`stream_start`**: Start WebSocket stream (not active by default)
6. **`stream_stop`**: Stop WebSocket stream
7. **`stream_status`**: Get stream connection status

## Output Format

```json
{
  "data": [{
    "success": true,
    "events": [
      {
        "event_id": "USANFP",
        "event_name": "Non-Farm Payrolls",
        "country": "US",
        "category": "Labour",
        "importance": "high",
        "event_date": "2025-01-10T13:30:00+00:00",
        "actual": 256.0,
        "forecast": 165.0,
        "previous": 212.0,
        "unit": "K"
      }
    ],
    "count": 12,
    "summary": {
      "total_instances": 12,
      "actual_avg": 187.5,
      "beat_rate": 0.583
    },
    "correlations": [
      {
        "event_date": "2025-01-10T13:30:00+00:00",
        "correlated_count": 5,
        "correlated_events": [...]
      }
    ]
  }],
  "metadata": {
    "query": "EventData action: query_event",
    "timestamp": "2025-11-27T12:00:00Z",
    "row_count": 1,
    "agent": "eventdata-puller-agent",
    "version": "1.0",
    "action": "query_event"
  }
}
```

## Example Usage

### 1. Query Event History with Correlations

```python
from src.agents.eventdata_puller_agent import EventDataPullerAgent

agent = EventDataPullerAgent()

# Query Non-Farm Payrolls with correlations
output = agent.run(
    action="query_event",
    event_name="Non-Farm Payrolls",
    country="US",
    lookback_days=365,
    window_hours=12,
    include_correlations=True
)
```

### 2. Update Economic Calendar

```python
# Update calendar for US events
output = agent.run(
    action="update_calendar",
    country="US",
    start_date="2024-01-01"
)
```

### 3. Find Correlated Events

```python
# Find events within ±6 hours of a specific FOMC decision
output = agent.run(
    action="find_correlations",
    event_name="Interest Rate Decision",
    country="US",
    target_event_date="2025-01-29",
    window_hours=6
)
```

### 4. Using with Orchestrator

```text
Query: "What economic events happened within 12 hours of the last 5 Non-Farm Payrolls releases?"

Orchestrator will:
1. Map to eventdata_puller_agent
2. Set action="query_event", event_name="Non-Farm Payrolls", include_correlations=True
3. Return event history with correlated events for each instance
```

## Database Schema

Events are stored in `workspace/economic_events.db`:

```sql
CREATE TABLE economic_events (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    country TEXT NOT NULL,
    category TEXT,
    importance TEXT,
    event_date TEXT NOT NULL,
    actual REAL,
    forecast REAL,
    previous REAL,
    revised REAL,
    unit TEXT,
    ticker TEXT,
    source TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(event_id, event_date)
);
```

## Common Event IDs

| Event | ID | Country |
|-------|-----|---------|
| Non-Farm Payrolls | USANFP | US |
| CPI | USCPIYOY | US |
| GDP | USGDPQOQ | US |
| FOMC Rate Decision | FDTR | US |
| Unemployment Rate | USURTOT | US |
| UK CPI | UKCPIY | GB |
| ECB Rate Decision | EURR002W | EU |

## Notes

- Calendar updates are incremental by default (only fetches new events)
- WebSocket streaming is disabled by default (requires explicit start)
- Correlation window is configurable from 1 to 168 hours (7 days)
- Events are indexed by date, country, and importance for fast queries

