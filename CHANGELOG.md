# Changelog

All notable changes to this project will be documented in this file.

---

## [2.1.0] - 2025-11-14 - Perplexity Tool Removal

### 🗑️ Removed
- **Perplexity Integration**: Removed unused Perplexity API tool
  - Deleted `src/servers/perplexity/` directory
  - Removed `PERPLEXITY_API_KEY` from config
  - Removed `search_markets` and `get_query_history` tools (Perplexity)
  - Removed `prediction_queries` table (Perplexity version)
  - System now exclusively uses direct Polymarket API with LLM scoring

### 📝 Documentation Updates
- Updated all documentation to reflect Polymarket-only architecture
- Removed references in ARCHITECTURE.md, README.md, and memory-bank files
- Updated system diagrams and flow charts

### 🎯 Rationale
- Tool was fully implemented but never actively used by any agent
- Polymarket direct API provides better results with LLM scoring
- Reduces external dependencies and simplifies architecture

---

## [2.0.0] - 2025-11-14 - Major Refactor: Blockchain Removal & Validation

### 🎯 Major Changes

**Removed All Blockchain Dependencies**
- Removed `web3>=7.0.0` dependency
- Removed Polygon RPC blockchain queries (~460 lines)
- Removed Goldsky subgraph GraphQL queries (~150 lines)
- Migrated to Polymarket's official CLOB API

**Implemented Comprehensive Validation**
- URL validation (check if markets actually exist)
- Creation date validation (prevent anachronistic queries)
- Token ID resolution (use correct CLOB token IDs)
- Data parsing validation (JSON string → proper objects)

### ✨ New Features

- **Historical Price Data**: Working via Polymarket `prices-history` endpoint
- **Market Validation**: Multi-layer validation ensures data accuracy
- **Better Error Messages**: Clear explanations when data unavailable

### 📊 Performance Improvements

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Query Time | 1-2s | 0.5-1s | 2x faster |
| Dependencies | web3 + stdlib | stdlib only | Simpler |
| Code Size | ~710 lines | ~500 lines | 30% smaller |

### 🔧 Technical Details

**Files Modified**:
- `src/servers/polymarket/schema.py` - Added validation functions
- `src/servers/polymarket/get_price_history.py` - Complete rewrite using API
- `src/agents/reasoning_agent/run.py` - Integrated validation
- `requirements.txt` - Removed web3

**Files Removed**:
- `src/servers/polymarket/expand_keywords.py` - Unused code
- Multiple redundant status files - Consolidated into docs

### 📚 Documentation

- Moved implementation notes to `docs/implementation/`
- Moved agent docs to `docs/agents/`
- Created comprehensive CHANGELOG
- Updated README as navigation hub

---

## [1.5.0] - 2025-11-13 - Reasoning Agent & Polymarket Integration

### ✨ Features

- **GPT-4 Reasoning Agent**: Natural language query understanding
- **Date Extraction**: Parse "Nov 1 2024" → "2024-11-01"
- **Intent Classification**: historical_opinion, current_opinion, price_change, movement
- **Polymarket Integration**: Direct API search with local filtering

### 🔧 Technical

- Added `src/agents/reasoning_agent/`
- Added `src/servers/polymarket/` tools
- Hybrid market search (400 recent + 200 popular)
- Session-based query tracking

---

## [1.0.0] - 2025-11-11 - Initial Code-Mode MCP Implementation

### ✨ Features

- **Pure Python MCP System**: No external network protocols
- **File-Based Bus**: Atomic JSON file operations
- **Manifest System**: Incremental filename generation
- **Multi-Agent**: Producer → Consumer patterns
- **Market Data Tool**: SQL queries with whitelist security
- **Perplexity Tool**: Prediction market search

### 🔧 Technical Stack

- Python 3.11+ stdlib only (runtime)
- SQLite for data storage
- Atomic file operations (crash-safe)
- Progressive disclosure pattern

### 📚 Structure

```
src/
  bus/      - File operations
  mcp/      - Tool discovery
  servers/  - Tool implementations
  agents/   - Producer & consumer agents
  core/     - Logging utilities
```

---

## Version Comparison

| Version | Description | Key Feature |
|---------|-------------|-------------|
| **2.0.0** | API Migration | Blockchain removal, validation |
| **1.5.0** | AI Integration | GPT-4 reasoning, Polymarket |
| **1.0.0** | Foundation | Code-mode MCP system |

---

## Migration Notes

### From 1.5.0 to 2.0.0

**Breaking Changes**: None (API compatible)

**Action Required**:
1. ~~Remove web3 from requirements~~ (Done automatically)
2. ~~Update imports~~ (Done automatically)
3. No code changes needed in your agents

**Benefits**:
- 2x faster queries
- No authentication required
- More reliable data source
- Simpler codebase

---

## Future Roadmap

### Planned Features
- [ ] Price caching (reduce API calls)
- [ ] Multi-market correlation analysis
- [ ] Real-time price updates via WebSocket
- [ ] Market activity alerts
- [ ] Visualization generation

### Under Consideration
- [ ] Support for more prediction market platforms
- [ ] Advanced sentiment analysis
- [ ] Portfolio tracking
- [ ] Trading signal generation

---

For detailed implementation notes, see `docs/implementation/`.

