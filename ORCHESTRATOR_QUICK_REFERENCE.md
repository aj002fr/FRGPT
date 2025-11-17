# Orchestrator Agent - Quick Reference Card

## 🚀 Quick Start

```bash
python scripts/test_orchestrator.py --query 4
```

## 📋 CLI Commands

```bash
# List all sample queries
python scripts/test_orchestrator.py --list

# Run sample query (1-8)
python scripts/test_orchestrator.py --query N

# Run custom query
python scripts/test_orchestrator.py --custom "YOUR QUERY"

# Options
--skip-validation     # Skip validation (faster)
--num-subtasks N      # Max subtasks (default: 5)
--verbose             # Debug logging
```

## 🐍 Python API

```python
from src.agents.orchestrator_agent import OrchestratorAgent

agent = OrchestratorAgent()
result = agent.run("Your query here")
```

## 🎯 Sample Queries

| ID | Query | Agents |
|----|-------|--------|
| 1 | Bitcoin predictions | polymarket |
| 2 | Market data for Bitcoin | market_data |
| 3 | Bitcoin predictions on Jan 1st | reasoning |
| **4** | **Predictions + market data** ⭐ | **multi-agent** |
| 5 | Ethereum vs Bitcoin | polymarket |
| 6 | AI regulation + data | multi-agent |
| 7 | Trump election + SQL | multi-agent |
| 8 | Market data from date | market_data |

## 📊 Output Structure

```python
{
    "query": str,              # Your query
    "answer": str,             # NL answer
    "data": {...},             # Merged data
    "validation": {...},       # Pass/fail
    "metadata": {...},         # Stats
    "worker_outputs": [...],   # Full results
    "output_path": str         # File location
}
```

## 🔧 Configuration

### API Keys (config/keys.env)
```bash
ANTHROPIC_API_KEY=sk-ant-xxx    # Required
OPENAI_API_KEY=sk-xxx           # Optional
```

### Worker Agents
- **market_data_agent**: SQL queries
- **polymarket_agent**: Prediction markets
- **reasoning_agent**: Historical analysis

## 🧪 Testing

```bash
# All tests
python -m pytest tests/e2e/test_orchestrator_e2e.py -v

# Quick test
python -m pytest tests/e2e/test_orchestrator_e2e.py::TestSimpleOrchestration -v
```

## 📂 File Locations

```
workspace/agents/orchestrator-agent/
├── out/000001.json              # Results
├── logs/20251114_120000.json    # Run log
└── generated_scripts/           # Scripts
```

## 🔍 Common Issues

| Issue | Solution |
|-------|----------|
| Taskmaster error | Check ANTHROPIC_API_KEY |
| No results | Check worker agent logs |
| Low validation | Review validation issues |
| Script error | Check generated_scripts/ |

## 📚 Documentation

- Quick Start: `docs/ORCHESTRATOR_QUICKSTART.md`
- Full Guide: `docs/ORCHESTRATOR_IMPLEMENTATION.md`
- This Card: `ORCHESTRATOR_QUICK_REFERENCE.md`
- Summary: `ORCHESTRATOR_INTEGRATION_SUMMARY.md`

## 💡 Tips

✅ Start with query 4 (shows all features)
✅ Use `--skip-validation` for faster iteration
✅ Check generated scripts for debugging
✅ Review validation report for issues
✅ All results saved to file bus

## 🎯 Best Practices

1. Start simple (queries 1-3)
2. Progress to complex (query 4+)
3. Use custom queries for real work
4. Skip validation during development
5. Review logs for troubleshooting

## ⚡ Performance

- Simple query: ~5-10s
- Complex query: ~10-30s
- With validation: +2-4s
- Without validation: faster

## 🆘 Help

```bash
# CLI help
python scripts/test_orchestrator.py --help

# Sample queries
python scripts/test_orchestrator.py --list

# Try it!
python scripts/test_orchestrator.py --query 4
```

---

**Ready to orchestrate? Try query 4 now! 🚀**

```bash
python scripts/test_orchestrator.py --query 4
```

