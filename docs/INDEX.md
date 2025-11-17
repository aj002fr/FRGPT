# Documentation Index

> **Complete documentation for the Market Data Puller MCP System**

---

## 📍 Start Here

| Document | Description | Read When |
|----------|-------------|-----------|
| **[../README.md](../README.md)** | Project overview & quick start | First time |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | System design & components | Understanding structure |
| **[USAGE.md](USAGE.md)** | Detailed usage guide | Using the system |

---

## 🤖 Agents

Documentation for individual AI agents:

| Agent | File | Description |
|-------|------|-------------|
| **Reasoning Agent** | [agents/REASONING_AGENT_DESIGN.md](agents/REASONING_AGENT_DESIGN.md) | GPT-4 natural language query parser |
| | [agents/REASONING_AGENT_IMPLEMENTATION.md](agents/REASONING_AGENT_IMPLEMENTATION.md) | Implementation details |
| | [agents/REASONING_AGENT_SUCCESS.md](agents/REASONING_AGENT_SUCCESS.md) | Results & examples |
| **Polymarket Agent** | [agents/POLYMARKET_AGENT.md](agents/POLYMARKET_AGENT.md) | Direct Polymarket API search |
| **Market Data Agent** | [agents/MARKET_DATA_AGENT.md](agents/MARKET_DATA_AGENT.md) | SQL query execution |

---

## 🔧 Implementation Notes

Technical details and migration history:

| Document | Description |
|----------|-------------|
| **[implementation/HISTORICAL_PRICES_STATUS.md](implementation/HISTORICAL_PRICES_STATUS.md)** | Historical price implementation |
| **[implementation/BLOCKCHAIN_REMOVAL_SUMMARY.md](implementation/BLOCKCHAIN_REMOVAL_SUMMARY.md)** | Migration from blockchain to API |
| **[implementation/VALIDATION_SUMMARY.md](implementation/VALIDATION_SUMMARY.md)** | Data validation implementation |
| **[../CHANGELOG.md](../CHANGELOG.md)** | Version history with all system changes |

---

## 📚 Memory Bank

Living knowledge base (auto-updated):

| File | Purpose |
|------|---------|
| **[../memory-bank/activeContext.md](../memory-bank/activeContext.md)** | Current system state |
| **[../memory-bank/code-index.md](../memory-bank/code-index.md)** | File summaries |
| **[../memory-bank/io-schema.md](../memory-bank/io-schema.md)** | API contracts & schemas |
| **[../memory-bank/progress.md](../memory-bank/progress.md)** | Implementation history |

---

## 🎯 By Topic

### Getting Started
1. [README.md](../README.md) - Overview & installation
2. [USAGE.md](USAGE.md) - Basic usage
3. [ARCHITECTURE.md](ARCHITECTURE.md) - How it works

### Development
1. [agents/](agents/) - Agent documentation
2. [../memory-bank/code-index.md](../memory-bank/code-index.md) - Code navigation
3. [../memory-bank/io-schema.md](../memory-bank/io-schema.md) - API contracts

### Implementation
1. [implementation/](implementation/) - Technical notes
2. [../CHANGELOG.md](../CHANGELOG.md) - Version history
3. [../memory-bank/progress.md](../memory-bank/progress.md) - Development log

---

## 🔍 Quick Find

**Looking for...**

| What | Where |
|------|-------|
| How to run queries | [USAGE.md](USAGE.md) |
| System design | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Agent details | [agents/](agents/) |
| API contracts | [../memory-bank/io-schema.md](../memory-bank/io-schema.md) |
| File summaries | [../memory-bank/code-index.md](../memory-bank/code-index.md) |
| Version changes | [../CHANGELOG.md](../CHANGELOG.md) |
| Historical prices | [implementation/HISTORICAL_PRICES_STATUS.md](implementation/HISTORICAL_PRICES_STATUS.md) |
| Validation | [implementation/VALIDATION_SUMMARY.md](implementation/VALIDATION_SUMMARY.md) |

---

## 📊 Documentation Map

```
docs/
├── INDEX.md (you are here)
├── ARCHITECTURE.md
├── USAGE.md
├── PREDICTIVE_MARKETS_IMPLEMENTATION.md
├── caveats.md
├── agents/
│   ├── REASONING_AGENT_DESIGN.md
│   ├── REASONING_AGENT_IMPLEMENTATION.md
│   ├── REASONING_AGENT_SUCCESS.md
│   ├── POLYMARKET_AGENT.md
│   └── MARKET_DATA_AGENT.md
└── implementation/
    ├── HISTORICAL_PRICES_STATUS.md
    ├── BLOCKCHAIN_REMOVAL_SUMMARY.md
    └── VALIDATION_SUMMARY.md
```

---

## 💡 Navigation Tips

1. **New to the project?** Start with [../README.md](../README.md)
2. **Understanding code?** Check [../memory-bank/code-index.md](../memory-bank/code-index.md)
3. **Building agents?** Read [agents/](agents/) docs
4. **API details?** See [../memory-bank/io-schema.md](../memory-bank/io-schema.md)
5. **Recent changes?** Check [../CHANGELOG.md](../CHANGELOG.md)

---

## 🔄 Keep Updated

This index is manually maintained. If you add new documentation:

1. Create the file in appropriate directory
2. Add entry to this INDEX.md
3. Link from relevant sections
4. Update [../README.md](../README.md) if needed

---

**Need help? Check [../README.md](../README.md) FAQ section**
