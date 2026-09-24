"""
Surprise Analysis MCP Tools.

Tools for analyzing the relationship between economic surprise (actual - consensus)
and price returns. Uses core plotting functions.
"""

import logging
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from src.mcp.discovery import register_tool
from src.servers.analytics.plot_factory import create_plot
from src.servers.analytics.core_plotting import plot_correlation, plot_distribution

logger = logging.getLogger(__name__)


def _check_dependencies():
    """Check if required dependencies are available."""
    try:
        import pandas
        import numpy
        from scipy import stats
        return True
    except ImportError:
        return False


def _get_price_data(
    event_datetime: datetime,
    symbol: str,
    return_window_mins: int = 30,
    path_window_mins: int = 60,
) -> Optional[Dict]:
    """Get price return and price path for an event."""
    try:
        from src.servers.distro_analytics.db_reader import get_ohlcv_from_db, get_bps_factor
        import pandas as pd
        import numpy as np
        
        start_time = event_datetime - timedelta(minutes=5)
        end_time = event_datetime + timedelta(minutes=path_window_mins)
        
        df = get_ohlcv_from_db(
            symbol_pattern=f"{symbol}%",
            start_date=start_time.strftime('%Y-%m-%d %H:%M:%S'),
            end_date=end_time.strftime('%Y-%m-%d %H:%M:%S'),
        )
        
        if df.empty or len(df) < 10:
            return None
        
        df = df.reset_index()
        bps_factor = get_bps_factor(symbol)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        time_diffs = (df['timestamp'] - event_datetime).abs()
        event_idx = time_diffs.idxmin()
        event_price = df.loc[event_idx, 'Close']
        event_time = df.loc[event_idx, 'timestamp']
        
        df['return_bps'] = (df['Close'] - event_price) * bps_factor
        df['mins_from_event'] = (df['timestamp'] - event_time).dt.total_seconds() / 60
        
        df_post = df[df['mins_from_event'] >= 0].copy()
        if len(df_post) < 5:
            return None
        
        return_idx = df_post['mins_from_event'].sub(return_window_mins).abs().idxmin()
        price_return = df_post.loc[return_idx, 'return_bps']
        
        return {
            'price_return_bps': float(price_return),
            'minutes': df_post['mins_from_event'].tolist()[:path_window_mins],
            'prices_bps': df_post['return_bps'].tolist()[:path_window_mins],
        }
    except Exception as e:
        logger.debug(f"Failed to get price data: {e}")
        return None


def _calculate_percentile(value: float, values: List[float]) -> float:
    """Calculate percentile rank of value in list."""
    count_below = sum(1 for v in values if v < value)
    count_equal = sum(1 for v in values if v == value)
    return (count_below + 0.5 * count_equal) / len(values) * 100


def _fetch_event_data(
    event_name: str,
    country: str,
    symbol: str,
    history_limit: int,
    return_window_mins: int,
) -> List[Dict]:
    """Fetch and process event data with surprise and price returns."""
    from src.servers.tradingeconomics.query_events import query_event_history
    import numpy as np
    
    history_result = query_event_history(
        event_name=event_name,
        country=country,
        limit=history_limit
    )
    
    if not history_result.get("success"):
        raise ValueError(f"Failed to fetch history: {history_result.get('error')}")
    
    events = history_result.get("events", [])
    
    if len(events) < 10:
        raise ValueError(f"Insufficient data: only {len(events)} events")
    
    # First pass: collect surprises
    all_surprises = []
    valid_events = []
    
    for event in events:
        actual = event.get("actual")
        consensus = event.get("consensus") or event.get("forecast")
        event_date_str = event.get("event_date")
        
        if actual is None or consensus is None or event_date_str is None:
            continue
        
        surprise = actual - consensus
        all_surprises.append(surprise)
        valid_events.append((event, surprise))
    
    # Second pass: get price data
    results = []
    all_returns = []
    
    for event, surprise in valid_events:
        event_date_str = event.get("event_date")
        
        try:
            event_dt = datetime.fromisoformat(event_date_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        
        price_data = _get_price_data(event_dt, symbol, return_window_mins)
        
        if price_data is None:
            continue
        
        all_returns.append(price_data['price_return_bps'])
        
        results.append({
            'date': str(event_dt.date()),
            'actual': event.get("actual"),
            'consensus': event.get("consensus") or event.get("forecast"),
            'surprise': surprise,
            'price_return': price_data['price_return_bps'],
            'minutes': price_data['minutes'],
            'prices': price_data['prices_bps'],
        })
    
    # Calculate percentiles
    for item in results:
        item['surprise_percentile'] = _calculate_percentile(item['surprise'], all_surprises)
        item['price_percentile'] = _calculate_percentile(item['price_return'], all_returns)
        
        # Assign bucket
        pct = item['surprise_percentile']
        if pct < 25:
            item['bucket'] = "P0-25"
        elif pct < 50:
            item['bucket'] = "P25-50"
        elif pct < 75:
            item['bucket'] = "P50-75"
        elif pct < 90:
            item['bucket'] = "P75-90"
        elif pct < 95:
            item['bucket'] = "P90-95"
        else:
            item['bucket'] = "P95-100"
        
        # Price bin (integer bps)
        ret = item['price_return']
        import numpy as np
        if ret < -10:
            item['price_bin'] = -11
        elif ret >= 10:
            item['price_bin'] = 10
        else:
            item['price_bin'] = int(np.floor(ret))
    
    return results


@register_tool(
    name="compute_surprise_correlation",
    description=(
        "Compute correlation between surprise percentile and price percentile. "
        "Returns Pearson and Spearman correlations with p-values, and bucket-level statistics. "
        "Automatically generates correlation scatter plot."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "event_name": {
                "type": "string",
                "description": "Economic event name (e.g., 'JOLTS Job Openings', 'CPI', 'NFP')"
            },
            "symbol": {
                "type": "string",
                "description": "Market symbol (e.g., 'TY', 'ES', 'ZN')"
            },
            "country": {
                "type": "string",
                "description": "Country code (default: 'US')"
            },
            "history_limit": {
                "type": "integer",
                "description": "Maximum events to analyze (default: 100)"
            },
            "return_window_mins": {
                "type": "integer",
                "description": "Minutes after event to measure return (default: 30)"
            }
        },
        "required": ["event_name", "symbol"]
    }
)
def compute_surprise_correlation(
    event_name: str,
    symbol: str,
    country: str = "US",
    history_limit: int = 100,
    return_window_mins: int = 30,
) -> Dict[str, Any]:
    """
    Compute correlation between surprise percentile and price percentile.
    """
    if not _check_dependencies():
        return {
            "success": False,
            "error": "Missing dependencies: pandas, numpy, scipy"
        }
    
    try:
        import numpy as np
        from scipy.stats import pearsonr, spearmanr, ttest_1samp
        from .db_reader import get_plots_output_dir
        
        # Fetch data
        events = _fetch_event_data(event_name, country, symbol, history_limit, return_window_mins)
        
        if len(events) < 10:
            return {
                "success": False,
                "error": f"Insufficient data: only {len(events)} events with price data"
            }
        
        # Extract arrays
        surprise_pcts = np.array([e['surprise_percentile'] for e in events])
        price_pcts = np.array([e['price_percentile'] for e in events])
        surprises = np.array([e['surprise'] for e in events])
        returns = np.array([e['price_return'] for e in events])
        
        # Correlations
        pearson_r, pearson_p = pearsonr(surprise_pcts, price_pcts)
        spearman_r, spearman_p = spearmanr(surprise_pcts, price_pcts)
        
        raw_pearson_r, raw_pearson_p = pearsonr(surprises, returns)
        raw_spearman_r, raw_spearman_p = spearmanr(surprises, returns)
        
        # Bucket statistics
        buckets = ["P0-25", "P25-50", "P50-75", "P75-90", "P90-95", "P95-100"]
        bucket_stats = {}
        
        for bucket in buckets:
            bucket_events = [e for e in events if e['bucket'] == bucket]
            if bucket_events:
                bucket_returns = [e['price_return'] for e in bucket_events]
                n = len(bucket_returns)
                mean = np.mean(bucket_returns)
                std = np.std(bucket_returns, ddof=1) if n > 1 else 0
                
                # T-test: is mean significantly different from 0?
                if n > 1 and std > 0:
                    t_stat, p_value = ttest_1samp(bucket_returns, 0)
                    significant = bool(p_value < 0.05)
                else:
                    t_stat, p_value = 0.0, 1.0
                    significant = False
                
                bucket_stats[bucket] = {
                    "n": int(n),
                    "mean_bps": round(float(mean), 2),
                    "std_bps": round(float(std), 2),
                    "t_statistic": round(float(t_stat), 3),
                    "p_value": round(float(p_value), 4),
                    "significant": significant,
                }
            else:
                bucket_stats[bucket] = {"n": 0}
        
        # Generate correlation plot using core plotting
        plot_paths = []
        
        # Correlation scatter plot
        corr_result = plot_correlation(
            x_values=surprise_pcts.tolist(),
            y_values=price_pcts.tolist(),
            title=f"{event_name} - {symbol}\nSurprise vs Price Percentile",
            x_label="Surprise Percentile",
            y_label="Price Return Percentile",
            show_regression=True,
            output_dir=get_plots_output_dir(),
        )
        if corr_result.get("success"):
            plot_paths.append(corr_result["png_path"])
        
        # Also generate return distribution
        dist_result = plot_distribution(
            values=[abs(r) for r in returns],
            title=f"{event_name} - {symbol}\nAbsolute Price Return Distribution",
            x_label="Absolute Return (BPS)",
            y_label="Frequency",
            output_dir=get_plots_output_dir(),
        )
        if dist_result.get("success"):
            plot_paths.append(dist_result["png_path"])
        
        # Interpretation
        interpretation = []
        if abs(pearson_r) < 0.1:
            interpretation.append("Very weak linear correlation - surprise has minimal predictive power")
        elif abs(pearson_r) < 0.3:
            interpretation.append("Weak correlation - surprise has some but limited predictive power")
        elif abs(pearson_r) < 0.5:
            interpretation.append("Moderate correlation - surprise shows meaningful predictive power")
        else:
            interpretation.append("Strong correlation - surprise is a good predictor of direction")
        
        sig_buckets = [b for b, s in bucket_stats.items() if s.get('significant')]
        if sig_buckets:
            interpretation.append(f"Significant mean returns in buckets: {', '.join(sig_buckets)}")
        else:
            interpretation.append("No buckets show statistically significant mean returns")
        
        return {
            "success": True,
            "event_name": event_name,
            "symbol": symbol,
            "sample_size": len(events),
            "return_window_mins": return_window_mins,
            "correlations": {
                "percentile_pearson": {
                    "r": round(float(pearson_r), 4),
                    "p_value": round(float(pearson_p), 4),
                    "significant": bool(pearson_p < 0.05),
                },
                "percentile_spearman": {
                    "rho": round(float(spearman_r), 4),
                    "p_value": round(float(spearman_p), 4),
                    "significant": bool(spearman_p < 0.05),
                },
                "raw_pearson": {
                    "r": round(float(raw_pearson_r), 4),
                    "p_value": round(float(raw_pearson_p), 4),
                },
                "raw_spearman": {
                    "rho": round(float(raw_spearman_r), 4),
                    "p_value": round(float(raw_spearman_p), 4),
                },
            },
            "bucket_statistics": bucket_stats,
            "interpretation": interpretation,
            "plot_paths": plot_paths,
        }
        
    except Exception as e:
        logger.exception(f"Surprise correlation analysis failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def _generate_interactive_html(
    events: List[Dict],
    event_name: str,
    symbol: str,
) -> str:
    """Generate interactive HTML with click-to-explore functionality."""
    import numpy as np
    
    buckets = ["P0-25", "P25-50", "P50-75", "P75-90", "P90-95", "P95-100"]
    colors = ["#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#3498db", "#9b59b6"]
    
    # Calculate statistics
    bucket_stats = {}
    for bucket in buckets:
        bucket_events = [e for e in events if e['bucket'] == bucket]
        if bucket_events:
            returns = [e['price_return'] for e in bucket_events]
            bucket_stats[bucket] = {
                'n': len(returns),
                'mean': round(np.mean(returns), 2),
                'std': round(np.std(returns), 2),
            }
        else:
            bucket_stats[bucket] = {'n': 0, 'mean': 0, 'std': 0}
    
    # Prepare JSON data
    events_for_json = []
    for e in events:
        events_for_json.append({
            'date': e['date'],
            'surprise': round(e['surprise'], 3),
            'surprise_pct': round(e['surprise_percentile'], 1),
            'bucket': e['bucket'],
            'price_return': round(e['price_return'], 2),
            'price_bin': e['price_bin'],
            'minutes': [round(m, 1) for m in e.get('minutes', [])[:60]],
            'prices': [round(p, 2) for p in e.get('prices', [])[:60]],
        })
    
    events_json = json.dumps(events_for_json)
    stats_json = json.dumps(bucket_stats)
    
    html_content = f'''<!DOCTYPE html>
<html>
<head>
    <title>{event_name} - Surprise Analysis</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
        }}
        .container {{
            max-width: 1600px;
            margin: 0 auto;
        }}
        h1 {{
            color: #333;
            margin-bottom: 5px;
        }}
        .subtitle {{
            color: #666;
            margin-bottom: 20px;
        }}
        .main-content {{
            display: flex;
            gap: 20px;
        }}
        .histograms-panel {{
            flex: 1;
            background: white;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .paths-panel {{
            flex: 1;
            background: white;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .histogram-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .histogram-box {{
            flex: 1;
            min-width: 200px;
            border: 2px solid #ddd;
            border-radius: 8px;
            padding: 10px;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .histogram-box:hover {{
            border-color: #3498db;
            box-shadow: 0 2px 8px rgba(52, 152, 219, 0.3);
        }}
        .histogram-box.selected {{
            border-color: #2ecc71;
            background: #f0fff4;
        }}
        .bucket-title {{
            font-weight: bold;
            margin-bottom: 5px;
            font-size: 14px;
        }}
        .bucket-stats {{
            font-size: 11px;
            color: #666;
        }}
        .instructions {{
            background: #e8f4f8;
            padding: 10px 15px;
            border-radius: 5px;
            margin-bottom: 15px;
            font-size: 13px;
        }}
        .event-list {{
            max-height: 150px;
            overflow-y: auto;
            font-size: 11px;
            margin-top: 10px;
            border-top: 1px solid #eee;
            padding-top: 10px;
        }}
        .event-item {{
            padding: 3px 5px;
            cursor: pointer;
            border-radius: 3px;
        }}
        .event-item:hover {{
            background: #e8f4f8;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{event_name} - {symbol}</h1>
        <p class="subtitle">Interactive Price Return Distribution by Surprise Percentile</p>
        
        <div class="instructions">
            <strong>How to use:</strong> Click on any histogram bar to see the price paths for events in that bin.
            Integer bps bins on X-axis. Green paths = positive return, Red = negative.
        </div>
        
        <div class="main-content">
            <div class="histograms-panel">
                <h3>Price Return Histograms by Surprise Bucket</h3>
                <div class="histogram-row" id="histograms"></div>
            </div>
            
            <div class="paths-panel">
                <h3 id="paths-title">Price Paths</h3>
                <p id="paths-info" style="font-size: 12px; color: #666;">Click histogram bar to see paths</p>
                <div id="paths-chart" style="height: 400px;"></div>
                <div class="event-list" id="event-list"></div>
            </div>
        </div>
    </div>
    
    <script>
        const events = {events_json};
        const bucketStats = {stats_json};
        const buckets = ["P0-25", "P25-50", "P50-75", "P75-90", "P90-95", "P95-100"];
        const colors = ["#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#3498db", "#9b59b6"];
        
        function createHistograms() {{
            const container = document.getElementById('histograms');
            
            buckets.forEach((bucket, idx) => {{
                const bucketEvents = events.filter(e => e.bucket === bucket);
                const stats = bucketStats[bucket];
                
                const box = document.createElement('div');
                box.className = 'histogram-box';
                box.id = 'hist-' + bucket;
                
                const title = document.createElement('div');
                title.className = 'bucket-title';
                title.innerHTML = bucket + ' <span style="color: ' + colors[idx] + '">(' + stats.n + ')</span>';
                box.appendChild(title);
                
                const statsDiv = document.createElement('div');
                statsDiv.className = 'bucket-stats';
                statsDiv.textContent = 'Mean: ' + stats.mean + ', Std: ' + stats.std + ' bps';
                box.appendChild(statsDiv);
                
                const chartDiv = document.createElement('div');
                chartDiv.id = 'chart-' + bucket;
                chartDiv.style.height = '120px';
                box.appendChild(chartDiv);
                
                container.appendChild(box);
                
                const returns = bucketEvents.map(e => e.price_return);
                
                const trace = {{
                    x: returns,
                    type: 'histogram',
                    xbins: {{ start: -12, end: 12, size: 1 }},
                    marker: {{ color: colors[idx], opacity: 0.7 }},
                    hovertemplate: '%{{x}} to %{{x}}+1 bps<br>Count: %{{y}}<extra></extra>'
                }};
                
                const layout = {{
                    margin: {{ t: 5, b: 25, l: 30, r: 10 }},
                    xaxis: {{ tickfont: {{ size: 9 }}, dtick: 2, range: [-12, 12] }},
                    yaxis: {{ tickfont: {{ size: 9 }} }},
                    shapes: [
                        {{ type: 'line', x0: stats.mean, x1: stats.mean, y0: 0, y1: 1, yref: 'paper', line: {{ color: 'red', dash: 'dash', width: 2 }} }},
                        {{ type: 'line', x0: 0, x1: 0, y0: 0, y1: 1, yref: 'paper', line: {{ color: 'gray', dash: 'dot', width: 1 }} }}
                    ],
                    bargap: 0.05
                }};
                
                Plotly.newPlot('chart-' + bucket, [trace], layout, {{ displayModeBar: false, responsive: true }});
                
                document.getElementById('chart-' + bucket).on('plotly_click', function(data) {{
                    if (data.points.length > 0) {{
                        const binStart = Math.floor(data.points[0].x);
                        selectBin(bucket, binStart, idx);
                    }}
                }});
            }});
        }}
        
        function selectBin(bucket, binStart, colorIdx) {{
            document.querySelectorAll('.histogram-box').forEach(box => box.classList.remove('selected'));
            document.getElementById('hist-' + bucket).classList.add('selected');
            
            const binEnd = binStart + 1;
            const filtered = events.filter(e => e.bucket === bucket && e.price_return >= binStart && e.price_return < binEnd);
            
            document.getElementById('paths-title').textContent = bucket + ': ' + binStart + ' to ' + binEnd + ' bps';
            document.getElementById('paths-info').textContent = filtered.length + ' events';
            
            const traces = filtered.map(e => ({{
                x: e.minutes,
                y: e.prices,
                type: 'scatter',
                mode: 'lines',
                name: e.date,
                line: {{ color: e.price_return > 0 ? 'rgba(0,150,0,0.7)' : 'rgba(200,0,0,0.7)', width: 2 }},
                hovertemplate: '<b>' + e.date + '</b><br>Surprise: ' + e.surprise + '<br>Return: ' + e.price_return + ' bps<extra></extra>'
            }}));
            
            if (traces.length === 0) {{
                traces.push({{ x: [0], y: [0], type: 'scatter', mode: 'text', text: ['No events'], textposition: 'middle center' }});
            }}
            
            const layout = {{
                margin: {{ t: 30, b: 40, l: 50, r: 20 }},
                xaxis: {{ title: 'Minutes' }},
                yaxis: {{ title: 'Return (bps)' }},
                shapes: [{{ type: 'line', x0: 0, x1: 60, y0: 0, y1: 0, line: {{ color: 'gray', dash: 'dash' }} }}],
                showlegend: filtered.length <= 10,
                hovermode: 'closest'
            }};
            
            Plotly.newPlot('paths-chart', traces, layout, {{ responsive: true }});
            
            document.getElementById('event-list').innerHTML = '<strong>Events:</strong><br>' +
                filtered.map(e => '<div class="event-item"><b>' + e.date + '</b> | ' + e.price_return + ' bps</div>').join('');
        }}
        
        createHistograms();
        selectBin('P0-25', 0, 0);
    </script>
</body>
</html>
'''
    return html_content


@register_tool(
    name="compute_surprise_price_distribution",
    description=(
        "Compute price return distribution conditional on surprise percentile bucket. "
        "Creates an interactive HTML plot with histograms (integer bps bins) where clicking "
        "on a histogram bar shows the price paths for events in that bin. "
        "Also generates static PNG plots."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "event_name": {
                "type": "string",
                "description": "Economic event name (e.g., 'JOLTS Job Openings', 'CPI', 'NFP')"
            },
            "symbol": {
                "type": "string",
                "description": "Market symbol (e.g., 'TY', 'ES', 'ZN')"
            },
            "country": {
                "type": "string",
                "description": "Country code (default: 'US')"
            },
            "history_limit": {
                "type": "integer",
                "description": "Maximum events to analyze (default: 100)"
            },
            "return_window_mins": {
                "type": "integer",
                "description": "Minutes after event to measure return (default: 30)"
            },
            "output_dir": {
                "type": "string",
                "description": "Directory for output files (default: workspace/plots)"
            }
        },
        "required": ["event_name", "symbol"]
    }
)
def compute_surprise_price_distribution(
    event_name: str,
    symbol: str,
    country: str = "US",
    history_limit: int = 100,
    return_window_mins: int = 30,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compute price distribution by surprise bucket with interactive visualization.
    """
    if not _check_dependencies():
        return {
            "success": False,
            "error": "Missing dependencies: pandas, numpy, scipy"
        }
    
    try:
        import numpy as np
        from src.servers.distro_analytics.db_reader import get_plots_output_dir
        
        # Fetch data
        events = _fetch_event_data(event_name, country, symbol, history_limit, return_window_mins)
        
        if len(events) < 10:
            return {
                "success": False,
                "error": f"Insufficient data: only {len(events)} events with price data"
            }
        
        # Output directory
        if output_dir:
            out_path = Path(output_dir)
        else:
            out_path = get_plots_output_dir()
        out_path.mkdir(parents=True, exist_ok=True)
        
        # Generate interactive HTML
        html_content = _generate_interactive_html(events, event_name, symbol)
        
        event_label = event_name.replace(" ", "_")[:30]
        html_file = out_path / f"{event_label}_{symbol}_surprise_distribution.html"
        
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        # Generate static PNG plots using core plotting
        plot_paths = []
        
        # Overall return distribution
        returns = [e['price_return'] for e in events]
        dist_result = plot_distribution(
            values=[abs(r) for r in returns],
            title=f"{event_name} - {symbol}\nAbsolute Price Return Distribution",
            x_label="Absolute Return (BPS)",
            y_label="Frequency",
            output_dir=out_path,
        )
        if dist_result.get("success"):
            plot_paths.append(dist_result["png_path"])
        
        # Correlation plot
        surprise_pcts = [e['surprise_percentile'] for e in events]
        price_pcts = [e['price_percentile'] for e in events]
        
        corr_result = plot_correlation(
            x_values=surprise_pcts,
            y_values=price_pcts,
            title=f"{event_name} - {symbol}\nSurprise vs Price Percentile",
            x_label="Surprise Percentile",
            y_label="Price Return Percentile",
            output_dir=out_path,
        )
        if corr_result.get("success"):
            plot_paths.append(corr_result["png_path"])
        
        # Calculate bucket statistics
        buckets = ["P0-25", "P25-50", "P50-75", "P75-90", "P90-95", "P95-100"]
        bucket_stats = {}
        
        for bucket in buckets:
            bucket_events = [e for e in events if e['bucket'] == bucket]
            if bucket_events:
                bucket_returns = [e['price_return'] for e in bucket_events]
                bucket_stats[bucket] = {
                    "n": len(bucket_returns),
                    "mean_bps": round(np.mean(bucket_returns), 2),
                    "std_bps": round(np.std(bucket_returns), 2),
                    "min_bps": round(min(bucket_returns), 2),
                    "max_bps": round(max(bucket_returns), 2),
                }
            else:
                bucket_stats[bucket] = {"n": 0}
        
        return {
            "success": True,
            "event_name": event_name,
            "symbol": symbol,
            "sample_size": len(events),
            "return_window_mins": return_window_mins,
            "bucket_statistics": bucket_stats,
            "interactive_plot": str(html_file),
            "plot_paths": plot_paths,
            "instructions": "Open the HTML file in a browser. Click on histogram bars to see price paths.",
        }
        
    except Exception as e:
        logger.exception(f"Surprise distribution analysis failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }
