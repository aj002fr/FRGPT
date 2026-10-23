# Distro Analytics Agent

The **Distro Analytics Agent** (and associated tools) provides statistical analysis and visualization for market data. It leverages the `DistroDashboard` library to compute returns, volatility, probability matrices, and event-based distributions.

## Capabilities

1. **Returns Distribution**: Analyze session-based returns (London, US Open, etc.)
2. **Volatility Analysis**: Analyze intraday high-low ranges
3. **Probability Matrices**: Calculate probability of price moves (X bps in Y hours)
4. **Event Distribution**: Analyze market reaction around specific economic events
5. **Surprise Analysis**: Bucket economic events by surprise magnitude (Beat/Miss) and analyze price impact

## Data Sources

The agent consumes data from two primary sources:
1. **Market Data (PostgreSQL)**:
   - Table: `ohlcv_1m`
   - Columns: `ts_event`, `symbol`, `open`, `high`, `low`, `close`, `volume`
   - Note: Can optionally read from local parquet files if configured.

2. **Economic Events (SQLite)**:
   - File: `workspace/economic_events.db`
   - Used for event-driven analysis (NFP, CPI, etc.)

## Tools

### 1. `compute_returns_distribution`
Computes descriptive statistics and percentiles for price returns, segmented by trading session.

**Inputs:**
- `symbol`: Ticker (e.g., "ZN")
- `interval`: Timeframe (e.g., "1h")
- `start_date` / `end_date`: Filter range

**Output:**
- Statistics per session (Mean, Std Dev, Percentiles)
- Generated distribution plot (PNG)

### 2. `compute_volatility_distribution`
Similar to returns, but analyzes the High-Low range (volatility) per session.

### 3. `compute_probability_matrix`
Generates a probability matrix for price movements.

**Inputs:**
- `target_bps`: Target move size (e.g., 5.0 bps)
- `target_hrs`: Time horizon (e.g., 4 hours)

**Output:**
- Probabilities of exceeding target move
- Visual heatmaps/matrices

### 4. `compute_event_distribution`
Analyzes how price behaves before/after a specific economic event.

**Inputs:**
- `event_name`: e.g., "Non Farm Payrolls"
- `delta_hrs`: Window size (e.g., 1 hour)

### 5. `compute_surprise_price_distribution`
Buckets events by surprise (Actual - Consensus) and plots price reaction for each bucket.

**Inputs:**
- `event_name`: Event to analyze
- `symbol`: Market to check against

## dependencies

These tools require optional dependencies. Ensure `requirements-distro.txt` is installed:
- `pandas`
- `matplotlib`
- `seaborn`
- `scipy`
- `psycopg2-binary` (for PostgreSQL connection)

## Integration Notes

- **Legacy Code**: Relies on `DistroDashboard_clean/` folder at project root.
- **Fail-Safe**: Tools use lazy imports; if dependencies are missing, they return clear error messages rather than crashing the agent system.

## Usage Example

```python
from src.mcp.client import MCPClient

client = MCPClient()

# Analyze ZN returns
result = client.call_tool("compute_returns_distribution", {
    "symbol": "ZN",
    "interval": "1h"
})

print(f"Latest Z-Score: {result['latest']['zscore']}")
print(f"Plot saved to: {result['plot_paths'][0]}")
```

