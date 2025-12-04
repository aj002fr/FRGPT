# Analytics Agent

## Purpose

The Analytics Agent performs statistical analysis and generates visualizations for market and economic data. It can access multiple databases, compute statistics, and create SVG plots.

## Capabilities

### 1. Descriptive Statistics
- Mean, median, mode
- Standard deviation, variance
- Percentiles (5th, 10th, 25th, 50th, 75th, 90th, 95th)
- Skewness and kurtosis
- Outlier detection (IQR and z-score methods)

### 2. Percentile Rank Analysis
- Find where a specific value falls in a distribution
- Calculate z-scores
- Interpret position (e.g., "top 10%", "below median")

### 3. Distribution Comparison
- Compare two datasets statistically
- Cohen's d effect size
- Range overlap analysis

### 4. Correlation Analysis
- Pearson correlation coefficient
- R-squared values
- Strength interpretation

### 5. Visualization (SVG)
- Histograms for distributions
- Line charts for time series
- Scatter plots for relationships
- Bar charts for comparisons

### 6. Event Impact Analysis
- Pull economic event dates from calendar
- Find market prices on those dates
- Analyze price behavior around events

### 7. Surprise Analysis
- Calculate surprise = actual - consensus
- Find percentile rank of current surprise vs historical
- Identify outlier events

## Available Databases

### market_data
- **Tables**: market_data
- **Key columns**: symbol, bid, ask, price, timestamp, file_date
- **Use for**: Price analysis, bid-ask spreads, time series

### economic_events
- **Tables**: economic_events
- **Key columns**: event_name, country, actual, consensus, forecast, previous, event_date
- **Use for**: Event analysis, surprise calculations, historical comparisons

## Example Use Cases

### 1. Basic Statistics on Market Prices
```
Query: "What are the statistics for XCME.OZN prices?"
Process:
1. Query market_data for symbol LIKE 'XCME.OZN%'
2. Extract price column
3. Call compute_statistics tool
4. Return mean, std dev, percentiles, etc.
```

### 2. Event Surprise Percentile
```
Query: "Where does today's NFP surprise fall vs historical?"
Process:
1. Query economic_events for event_name LIKE '%Nonfarm%'
2. Calculate historical surprises: actual - consensus
3. Get today's surprise value
4. Call compute_percentile_rank with today's value and historical data
5. Return percentile and interpretation
```

### 3. Market Prices on Event Dates
```
Query: "Show me market prices on NFP release dates"
Process:
1. Query economic_events for NFP dates
2. For each date, query market_data for that file_date
3. Aggregate and compute statistics
4. Generate histogram of price distribution
```

### 4. Distribution Visualization
```
Query: "Show me the distribution of bid-ask spreads"
Process:
1. Query market_data for bid and ask columns
2. Calculate spreads: ask - bid
3. Call generate_histogram tool
4. Return SVG path and statistics
```

## Input Format

```json
{
    "query": "Natural language description of analysis needed",
    "database": "market_data | economic_events | both",
    "analysis_type": "descriptive | percentile_rank | distribution | comparison | correlation | event_impact | surprise_analysis",
    "filters": {
        "symbol": "pattern",
        "event_name": "pattern",
        "country": "US",
        "date_from": "YYYY-MM-DD",
        "date_to": "YYYY-MM-DD"
    },
    "options": {
        "generate_plot": true,
        "plot_type": "histogram | line | scatter | bar",
        "percentiles": [5, 25, 50, 75, 95]
    }
}
```

## Output Format

```json
{
    "success": true,
    "analysis_type": "descriptive",
    "statistics": {
        "count": 1000,
        "mean": 123.45,
        "median": 122.00,
        "std_dev": 5.67,
        "percentiles": {"25": 118.0, "50": 122.0, "75": 128.0}
    },
    "plot": {
        "svg_path": "workspace/agents/analytics-agent/plots/histogram_20251202.svg",
        "plot_type": "histogram"
    },
    "interpretation": "The data shows a normal distribution with mean 123.45...",
    "metadata": {
        "database": "market_data",
        "rows_analyzed": 1000,
        "filters_applied": {"symbol": "XCME.OZN%"}
    }
}
```

## Process Flow

1. **Parse Query**: Extract analysis type, database, filters
2. **Fetch Data**: Query appropriate database(s)
3. **Validate Data**: Ensure sufficient data points
4. **Compute Statistics**: Call appropriate statistics tools
5. **Generate Visualization**: Create SVG if requested
6. **Interpret Results**: Provide human-readable summary
7. **Write Output**: Save to file bus with metadata

## Error Handling

- Return clear error messages for invalid queries
- Handle missing data gracefully
- Warn when data points are insufficient
- Validate all numeric inputs

## Integration

The Analytics Agent can be called by:
- Orchestrator Agent for complex multi-step analysis
- Direct CLI via test_analytics.py
- Other agents needing statistical analysis

