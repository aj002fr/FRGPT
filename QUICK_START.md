# Quick Start Guide

**Get up and running in 5 minutes!**

---

## 🚀 Installation

```bash
# 1. Clone & setup
cd market_data_puller
pip install -r requirements.txt

# 2. Configure OpenAI key (for AI pipeline only)
echo "OPENAI_API_KEY=your-key-here" > config/keys.env

# 3. Run demo
python main.py
```

---

## ✨ Three Ways to Use

### 1️⃣ **SQL Market Data** (Local Database)

```bash
# Fast, structured queries on local SQLite database
python scripts/test_queries.py --query 1

# Example output:
# ✅ Found 50 call options
# Avg bid: $12.34, Avg ask: $12.56
```

**Use when**: You have market data in SQLite and need statistics.

---

### 2️⃣ **AI Intelligence** (Natural Language)

```bash
# Ask questions in plain English
python scripts/test_reasoning.py --custom "What was opinion on Jan 1 2025 about Bitcoin?"

# System automatically:
# • Parses your intent
# • Extracts dates
# • Searches Polymarket
# • Validates data
# • Fetches historical prices
```

**Use when**: You want natural language queries with AI reasoning.  
**Requires**: OpenAI API key

---

### 3️⃣ **Direct Polymarket Search** (No AI)

```bash
# Fast keyword search without AI
python scripts/test_polymarket.py --custom "AI regulation"

# Searches 600 markets (recent + popular)
# Returns relevant markets instantly
```

**Use when**: You want fast searches without AI costs.  
**Requires**: No API key needed!

---

## 📊 View Results

```bash
# Show all agent outputs
python scripts/show_logs.py

# See latest runs with timestamps
# Includes all pipeline results
```

---

## 🧪 Run Tests

```bash
# Test everything
python -m pytest tests/e2e/ -v

# Test specific pipelines
python -m pytest tests/e2e/test_marketdata_e2e.py -v
python -m pytest tests/e2e/test_polymarket_e2e.py -v
```

---

## 🎯 What Gets Created

```
workspace/
└── agents/
    ├── market-data-agent/    # SQL query results
    ├── consumer-agent/       # Statistics
    ├── polymarket-agent/     # Direct search results
    └── reasoning-agent/      # AI analysis results
```

Each agent creates:
- `out/NNNNNN.json` - Structured output
- `logs/TIMESTAMP.json` - Run metadata

---

## ⚡ Quick Examples

### Example 1: Find call options

```bash
python scripts/test_queries.py --query 1
```

### Example 2: Bitcoin sentiment

```bash
python scripts/test_reasoning.py --custom "What was sentiment on Bitcoin?"
```

### Example 3: Search for AI markets

```bash
python scripts/test_polymarket.py --custom "artificial intelligence"
```

---

## 🛠️ Common Issues

### No OpenAI key

**Problem**: `OPENAI_API_KEY` not found  
**Solution**: Only needed for Pipeline 2 (AI reasoning). Pipelines 1 & 3 work without it!

```bash
echo "OPENAI_API_KEY=sk-..." > config/keys.env
```

### Tool not found error

**Problem**: MCP tools not discovered  
**Solution**: Ensure `src/servers/*/` has `__init__.py`

```bash
python -c "from src.mcp.discovery import discover_tools; from pathlib import Path; print(len(discover_tools(Path('src/servers'))))"
# Should output: 7
```

### Import errors

**Problem**: Module not found  
**Solution**: Ensure you're in project root

```bash
cd market_data_puller  # Project root
python main.py         # Run from here
```

---

## 📖 Next Steps

1. **Read the docs**: `docs/INDEX.md`
2. **Check examples**: `docs/USAGE.md`
3. **Understand design**: `docs/ARCHITECTURE.md`
4. **See what's new**: `CHANGELOG.md`

---

## 💡 Pro Tips

1. **Start with `python main.py`** - It runs all 3 pipelines and shows you everything
2. **Use Pipeline 3 for exploration** - No API costs, fast results
3. **Use Pipeline 2 for complex questions** - AI understands nuanced queries
4. **Use Pipeline 1 for local data** - Fastest for database queries

---

## 🎊 You're Ready!

The system is now running. Check `workspace/agents/*/out/` for results!

**Questions?** See [README.md](README.md) FAQ section

