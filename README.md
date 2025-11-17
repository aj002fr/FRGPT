# Market Data Puller - Code-Mode MCP System

> **Pure Python MCP system for market data analysis with AI-powered reasoning**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests: Passing](https://img.shields.io/badge/tests-passing-green.svg)](tests/)

---

## 🎯 What is This?

A production-ready Python system that combines:
- **Model Context Protocol (MCP)** for tool orchestration
- **GPT-4 Reasoning v2.0** - simplified, always shows current + historical
- **Polymarket Integration** for prediction market data
- **Smart Sorting** - relevance first, then volume
- **Low Volume Flags** - automatic risk warnings
- **Zero Dependencies** (stdlib only for core functionality)

### Quick Example

```bash
# Run the unified demo
python main.py

# This runs 3 pipelines automatically:
# 1. SQL Market Data (Database → Statistics)
# 2. Polymarket Intelligence (AI → API → Validation)
# 3. Direct Polymarket Search (API only, no AI)

# Or ask specific questions:
python scripts/test_reasoning.py --custom "What was opinion on Jan 1 2025 about Bitcoin?"

# System automatically (v2.0):
# 1. Extracts topic + date (GPT-4)
# 2. Searches Polymarket markets
# 3. Shows current state (always)
# 4. Compares with Jan 1 (or past week if no date)
# 5. Sorts by relevance → volume
# 6. Flags low volume markets

# Output:
# Will Bitcoin reach $200K by Dec 31, 2025?
# 📊 Current: Yes 1.7%, No 98.4%
# 📅 Jan 1, 2025: Yes 21.1%, No 78.9%
# 📉 Change: -19.4pp (down)
# 💰 Volume: $125,000
# 🎯 Relevance: 0.98
```

---

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd market_data_puller

# Install dependencies
pip install -r requirements.txt

# Set up OpenAI API key (for reasoning agent only)
# Create config/keys.env and add:
# OPENAI_API_KEY=your-key-here

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

# 0. ⭐ Orchestrator (Multi-Agent Coordination) - NEW!
python scripts/test_orchestrator.py --list       # Show sample queries
python scripts/test_orchestrator.py --query 4    # Complex multi-agent query
python scripts/test_orchestrator.py --custom "What were Bitcoin predictions and market data?"

# 1. SQL Market Data
python scripts/test_queries.py --list            # Show available queries
python scripts/test_queries.py --query 1         # Run specific query

# 2. Polymarket Intelligence (AI)
python scripts/test_reasoning.py --list          # Show test queries
python scripts/test_reasoning.py --query 1       # Run test query
python scripts/test_reasoning.py --custom "What was opinion on Bitcoin?"

# 3. Direct Polymarket Search (No AI)
python scripts/test_polymarket.py --list         # Show sample queries
python scripts/test_polymarket.py --query 3      # Bitcoin markets
python scripts/test_polymarket.py --custom "AI regulation"

# View results
python scripts/show_logs.py
```

---

## 📖 Documentation

- **[Quick Start](QUICK_START.md)** - Get running in 5 minutes
- **[Orchestrator Quick Start](docs/ORCHESTRATOR_QUICKSTART.md)** - ⭐ **NEW** - Multi-agent coordination in 5 minutes
- **[Orchestrator Implementation](docs/ORCHESTRATOR_IMPLEMENTATION.md)** - ⭐ **NEW** - Complete orchestrator guide
- **[Index](docs/INDEX.md)** - Central documentation hub
- **[Architecture](docs/ARCHITECTURE.md)** - System design & patterns
- **[API Reference](docs/API.md)** - Complete tool reference
- **[Memory Bank](memory-bank/)** - Technical knowledge base
- **[Changelog](CHANGELOG.md)** - Version history and changes

---

## 📁 Project Structure

```
market_data_puller/
├── src/
│   ├── agents/          # AI agents (reasoning, polymarket, consumer)
│   ├── servers/         # MCP tools (marketdata, polymarket)
│   ├── bus/             # File-based communication
│   ├── mcp/             # Tool discovery & execution
│   └── core/            # Logging utilities
├── scripts/             # CLI tools & test scripts
├── tests/               # E2E test suite
├── docs/                # Documentation
│   ├── agents/          # Agent-specific docs
│   ├── implementation/  # Technical implementation notes
│   ├── ARCHITECTURE.md  # System design
│   └── USAGE.md         # Detailed usage guide
├── memory-bank/         # Knowledge base
│   ├── activeContext.md # Current system state
│   ├── code-index.md    # File summaries
│   ├── io-schema.md     # API contracts
│   └── progress.md      # Implementation history
├── config/              # Configuration
├── workspace/           # Agent outputs (auto-generated)
├── logs/                # System logs (auto-generated)
├── README.md            # This file (START HERE)
└── CHANGELOG.md         # Version history
```

---

## 🔧 Core Components

### 1. **MCP System** (Model Context Protocol)
- **Discovery**: Auto-register tools via decorators
- **Execution**: Direct Python function calls (no network)
- **Tools**: SQL queries, API searches, price history

### 2. **Agents** (5 Core Agents)
- **OrchestratorAgent**: ⭐ **NEW** - Meta-agent that coordinates multiple workers for complex queries
- **ReasoningAgent v2.0**: Simplified GPT-4 parsing - always shows current + historical, sorted by relevance & volume
- **PolymarketAgent**: Direct API search with validation (no AI required)
- **MarketDataAgent**: SQL query execution with whitelist security
- **ConsumerAgent**: Data processing & statistics computation

### 3. **File Bus** (Inter-Agent Communication)
- **Atomic Writes**: Crash-safe operations
- **Manifest System**: Incremental IDs (000001.json, 000002.json, ...)
- **Schema Validation**: Ensures data integrity
- **Run Logging**: Full audit trail

### 4. **Tools** (Data Sources)
- **run_query**: Execute SQL on market_data table
- **search_polymarket_markets**: Search Polymarket with LLM scoring
- **get_market_price_history**: Historical prices from CLOB API
- **get_market_price_range**: Price trends over date ranges
- **get_*_history**: Query history retrieval

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

## 🎯 Key Features

### ✅ Natural Language Queries
```python
"What was opinion on Nov 1 2024 about Bitcoin?"
→ Extracts: historical_opinion, Bitcoin, 2024-11-01
→ Searches: 3 relevant markets
→ Returns: Historical prices with comparison
```

### ✅ Multi-Layer Validation
- **URL Validation**: Checks if markets exist
- **Date Validation**: Ensures markets existed on query date
- **Token ID Resolution**: Uses correct CLOB token IDs
- **Data Parsing**: Handles JSON strings and arrays

### ✅ Historical Price Data
```python
# Single date
get_market_price_history(market_id, date="2025-01-01")
→ {"yes": 0.21, "no": 0.79}

# Date range
get_market_price_range(market_id, start="2025-01-01", end="2025-01-31")
→ Daily prices + trend analysis
```

### ✅ Production Ready
- **Zero Network Protocol**: Direct function calls
- **Atomic Operations**: Crash-safe file writes
- **Comprehensive Logging**: INFO level events + file logs
- **Error Handling**: Graceful degradation
- **Type Hints**: Full type annotations
- **Tests**: E2E test suite included

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
python scripts/test_queries.py --query 1      # Market data
python scripts/test_polymarket.py --query 2   # Polymarket
python scripts/test_reasoning.py --query 3    # AI reasoning
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
openai>=2.0.0 (reasoning agent only)
```

### Testing
```
pytest>=7.4.0
```

### What We DON'T Need
- ❌ No web3 or blockchain libraries
- ❌ No authentication for price data
- ❌ No external databases
- ❌ No complex frameworks

---

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
```

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes with tests
4. Update documentation
5. Submit pull request

### Code Style
- Type hints for public APIs
- Docstrings for classes/methods
- Logging at INFO level
- Atomic file operations

---

## 📄 License

MIT License - see LICENSE file

---

## 🔗 Links

- **Documentation**: [docs/](docs/)
- **Memory Bank**: [memory-bank/](memory-bank/)
- **Tests**: [tests/e2e/](tests/e2e/)
- **Scripts**: [scripts/](scripts/)
- **Changelog**: [CHANGELOG.md](CHANGELOG.md)

---

## 💡 Example Workflows

### Workflow 1: Historical Analysis
```bash
# Query historical opinion
python scripts/test_reasoning.py --custom "What did people think about Trump winning on Nov 5 2024?"

# System flow:
# 1. GPT-4 parses: historical_opinion, "Trump winning", 2024-11-05
# 2. Searches Polymarket for Trump election markets
# 3. Validates market existed on Nov 5
# 4. Fetches historical prices for that date
# 5. Compares with current prices
# 6. Returns analysis
```

### Workflow 2: Price Trends
```bash
# Get price evolution
python -c "
from src.mcp.client import MCPClient
client = MCPClient()
result = client.call_tool('get_market_price_range', {
    'market_id': '12345...',
    'start_date': '2025-01-01',
    'end_date': '2025-01-31',
    'interval_days': 7
})
print(result)
"
```

### Workflow 3: Market Discovery
```bash
# Find and analyze markets
python scripts/test_polymarket.py --custom "AI regulation 2025"

# System:
# 1. Searches 600 markets (400 recent + 200 popular)
# 2. Filters by keywords with scoring
# 3. Validates URLs and data
# 4. Returns top results with prices/volumes
```

---

## 🎓 Learning Path

1. **Start Here**: Read this README
2. **Quick Test**: Run `python main.py`
3. **Try Queries**: `python scripts/test_reasoning.py --list`
4. **Understand Architecture**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
5. **Explore Code**: [memory-bank/code-index.md](memory-bank/code-index.md)
6. **Advanced Usage**: [docs/USAGE.md](docs/USAGE.md)
7. **Build Your Own**: Follow agent/tool templates above

---

## ❓ FAQ

**Q: Do I need API keys?**
A: Only for GPT-4 reasoning agent. Polymarket data is public.

**Q: Can I use this without OpenAI?**
A: Yes! Use `test_polymarket.py` or `test_queries.py` directly.

**Q: How do I add more markets?**
A: Just query them! Polymarket has 1000s of active markets.

**Q: Is historical data accurate?**
A: Yes, from official Polymarket CLOB API with weighted averaging.

**Q: Can I deploy this in production?**
A: Yes! Zero external dependencies (except OpenAI if using reasoning).

---

**Questions? Check [docs/](docs/) or [memory-bank/](memory-bank/)**

**Ready to start? Run:** `python main.py`
