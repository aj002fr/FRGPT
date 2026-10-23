

> **Pure Python MCP system for extracting information from all market sources**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests: Passing](https://img.shields.io/badge/tests-passing-green.svg)](tests/)

---


## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd market_data_puller

# Install dependencies
pip install -r requirements.txt

# Initialize databases
python scripts/setup_polymarket_db.py

# Run demo
python main.py
```

### Basic Usage

```bash
# Run unified demo (recommended)
python main.py                                   # Runs all 3 pipelines

# Individual pipelines:

# 0. ⭐ Orchestrator (Multi-Agent Coordination) 
python scripts/test_orchestrator.py --list       # Show sample queries
python scripts/test_orchestrator.py --query 4    # Complex multi-agent query
python scripts/test_orchestrator.py --custom ""

# 1. SQL Market Data
python scripts/test_queries.py --list            # Show available queries
python scripts/test_queries.py --query 1         # Run specific query

# 2. Direct Polymarket Search (simple, API-only)
python scripts/test_polymarket_simple.py --custom "super bowl champion 2026" --max-results 10

# View results
python scripts/show_logs.py
```

---

## 📖 Documentation

- **[Quick Start](QUICK_START.md)** - Get running in 5 minutes
- **[Orchestrator Quick Start](docs/ORCHESTRATOR_QUICKSTART.md)** 
- **[Orchestrator Implementation](docs/ORCHESTRATOR_IMPLEMENTATION.md)** - Complete orchestrator guide
- **[Index](docs/INDEX.md)** - Central documentation hub
- **[Architecture](docs/ARCHITECTURE.md)** - System design & patterns
- **[API Reference](docs/API.md)** - Complete tool reference
- **[Unified Dashboard Runbook](docs/unified_dashboard_runbook.md)** - Local live pre/post event dashboard with scheduled data refresh
- **[Memory Bank](memory-bank/)** - Technical knowledge base
- **[Changelog](CHANGELOG.md)** - Version history and changes

---

## 📁 Project Structure

```
market_data_puller/
├── src/
│   ├── agents/          # AI agents
│   │   ├── orchestrator_agent/  # Multi-agent coordination (11 files)
│   │   ├── market_data_agent/   # SQL query producer
│   │   ├── polymarket_agent/    # Prediction market search
│   │   ├── eventdata_puller_agent/  # Economic events
│   │   ├── analytics_agent/     # Statistical analysis
│   │   ├── runner_agent/        # Final answer generation (GPT)
│   │   └── consumer_agent/      # Statistics consumer (demo)
│   ├── servers/         # MCP tools
│   │   ├── marketdata/      # SQL queries
│   │   ├── polymarket/      # Polymarket API tools
│   │   ├── analytics/       # Statistics & plotting tools
│   │   ├── distro_analytics/  # Distribution analysis tools (used by analytics_agent)
│   │   └── tradingeconomics/  # Economic calendar API
│   ├── bus/             # File-based communication
│   ├── mcp/             # Tool discovery & execution
│   └── core/            # Utilities (logging, telegram, scheduling, run store)
├── scripts/             # CLI tools & test scripts
├── tests/e2e/           # E2E test suite
├── docs/                # Documentation
├── memory-bank/         # Knowledge base
├── config/              # Configuration
├── workspace/           # Agent outputs (auto-generated)
├── runs/                # Orchestration run artifacts (auto-generated)
└── README.md            # This file
```

---

## 🔧 Core Components

### 1. **MCP System** (Model Context Protocol)
- **Discovery**: Auto-register tools via decorators
- **Execution**: Direct Python function calls (no network)
- **Tools**: SQL queries, API searches, price history

### 2. **Agents** (7 Agents)
- **OrchestratorAgent**: Two-stage planner coordinating multiple workers for complex queries
- **RunnerAgent**: GPT-powered final answer generation and consolidation
- **MarketDataAgent**: SQL query execution with whitelist security
- **PolymarketAgent**: Simple API-only Polymarket search (volume-sorted, keyword-filtered)
- **EventDataPullerAgent**: Economic calendar fetching and querying
- **AnalyticsAgent**: Statistical analysis and visualization
- **ConsumerAgent**: Data processing & statistics computation (demo)

### 3. **File Bus** (Inter-Agent Communication)
- **Atomic Writes**: Crash-safe operations
- **Manifest System**: Incremental IDs (000001.json, 000002.json, ...)
- **Schema Validation**: Ensures data integrity
- **Run Logging**: Full audit trail

### 4. **Tools** (Data Sources)
- **run_query**: Execute SQL on market_data table
- **search_polymarket_markets**: Search Polymarket markets via Gamma API
- **get_market_price_history**: Historical prices from CLOB API
- **query_event_history**: Economic events from Trading Economics
- **compute_returns_distribution**: Session-based returns analysis
- **compute_volatility_distribution**: Volatility analysis
- **compute_probability_matrix**: Price movement probabilities
- **compute_event_distribution**: Event impact analysis
- **compute_statistics**: Pure Python statistical functions

---

## 📚 Documentation

| Document | Description | Use When |
|----------|-------------|----------|
| **[README.md](README.md)** | Overview & quick start | First time setup |
| **[CHANGELOG.md](CHANGELOG.md)** | Version history | Checking changes |
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | System design | Understanding structure |
| **[docs/USAGE.md](docs/USAGE.md)** | Detailed usage | Advanced features |
| **[docs/agents/](docs/agents/)** | Agent documentation | Agent-specific info |
| **[memory-bank/activeContext.md](memory-bank/activeContext.md)** | Current status | Latest system state |
| **[memory-bank/io-schema.md](memory-bank/io-schema.md)** | API contracts | Tool interfaces |

---



## 🧪 Testing

```bash
# Run all E2E tests
python -m pytest tests/e2e/ -v

# Test specific components
python -m pytest tests/e2e/test_marketdata_e2e.py -v
python -m pytest tests/e2e/test_polymarket_e2e.py -v
python -m pytest tests/e2e/test_predictions_e2e.py -v

# Manual testing
python scripts/test_queries.py --query 1                  # Market data
python scripts/test_polymarket_simple.py --custom "..."   # Direct Polymarket search
```

---

## 📊 Performance

| Operation | Time | Notes |
|-----------|------|-------|
| SQL Query | 5-20ms | Local SQLite |
| Market Search | 1-2s | API call + filtering |
| Price History | 0.5-1s | Polymarket CLOB API |
| Reasoning | 2-4s | GPT-4 parsing + search + prices |
| File Operations | 1-5ms | Atomic writes |

---

## 🔌 Dependencies

### Runtime
```
Python 3.11+ (stdlib only for core)
openai>=2.0.0 (optional, for orchestrator task planning)
```

### Testing
```
pytest>=7.4.0
```


## 🛠️ Development

### Adding a New Agent

```python
# 1. Create agent directory
src/agents/my_agent/
  ├── __init__.py
  ├── config.py    # Configuration
  └── run.py       # Main logic

# 2. Implement agent
class MyAgent:
    def run(self, query: str):
        # Your logic here
        return output_path

# 3. Add to scripts/
scripts/test_my_agent.py
```

### Adding a New Tool

```python
# 1. Create tool file
src/servers/mytool/
  ├── __init__.py   # Import tools
  ├── schema.py     # Constants
  └── my_tool.py    # Tool implementation

# 2. Register tool
from src.mcp.discovery import register_tool

@register_tool("my_tool", "Description")
def my_tool(param: str) -> dict:
    return {"result": "data"}

# 3. Tool auto-discovered on import


## 📄 License

MIT License - see LICENSE file

---


