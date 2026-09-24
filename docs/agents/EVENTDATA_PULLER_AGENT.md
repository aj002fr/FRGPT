# EventData Puller Agent

The **EventData Puller Agent** is a specialized agent for managing economic calendar data. It fetches, stores, and queries economic events from TradingEconomics, maintaining a local SQLite database for historical analysis and supporting real-time event streaming.

## Capabilities

1. **Calendar Management**: Fetch and update economic calendar data
2. **Event Querying**: Search historical events with rich filtering
3. **Correlation Analysis**: Find relationships between economic events
4. **Real-time Streaming**: Stream live economic releases via WebSocket
5. **Surprise Analysis**: Calculate actual vs consensus surprises

## Architecture

The agent operates on a dual-storage model:
- **SQLite Database**: `workspace/economic_events.db` for persistent history
- **In-Memory Buffer**: For real-time WebSocket streaming

### Data Flow
```mermaid
graph TD
    TE_API[TradingEconomics API] -->|Fetch| Agent
    TE_WS[TradingEconomics WebSocket] -->|Stream| Agent
    Agent -->|Store| DB[(economic_events.db)]
    Agent -->|Emit| FileBus[File Bus (out/000001.json)]
    DB -->|Query| Agent
```

## Actions

These are the supported `EventDataPullerAgent.run(action=...)` actions:

### 1. `update_calendar`
Fetches economic calendar data for a specified date range and stores it in the database.

**Inputs:**
- `start_date` (optional): YYYY-MM-DD
- `end_date` (optional): YYYY-MM-DD
- `country` (optional): Filter by country (e.g., "US")
- `importance` (optional): Filter by importance ("low", "medium", "high")

### 2. `query_event`
Retrieves historical events matching criteria.

**Inputs:**
- `event_id` (optional): Specific event ticker (e.g., "USANFP")
- `event_name` (optional): Partial name match (e.g., "Non Farm Payrolls")
- `country` (optional): Country code
- `limit` (optional): Max results (default: 100)

**Note:** requires at least one of `event_id` or `event_name`.

### 3. `search_events`
Searches for event types by keyword/category/country/importance.

### 4. `stream_start` / `stream_stop` / `stream_status`
Manages real-time WebSocket connection for live event updates.

### 5. `get_live_events`
Returns buffered live events from the WebSocket stream.

## Database Schema

**Table: `economic_events`**
| Column | Type | Description |
|--------|------|-------------|
| event_id | TEXT | Unique ticker (e.g., USANFP) |
| event_name | TEXT | Human-readable name |
| country | TEXT | Country code |
| event_date | TEXT | ISO-8601 timestamp |
| actual | REAL | Released value |
| consensus | REAL | Market consensus |
| previous | REAL | Previous period value |

## Usage Examples

### Python Script
```python
from src.agents.eventdata_puller_agent import EventDataPullerAgent

agent = EventDataPullerAgent()

# 1. Update calendar for US high-impact events
agent.run(action="update_calendar", country="US", importance="high", start_date="2024-01-01")

# 2. Query specific event history
result_path = agent.run(action="query_event", event_name="Non Farm Payrolls", limit=12)
```

### CLI
```bash
# Run agent directly (if configured with main runner)
python scripts/run_agent.py eventdata --action query_event --params '{"event_name": "CPI"}'
```

## Configuration
- **Database Path**: `workspace/economic_events.db`
- **API Keys**: Requires `TE_API_KEY` and `TE_API_SECRET` in `config/keys.env`

