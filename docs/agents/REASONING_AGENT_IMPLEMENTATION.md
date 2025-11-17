# Reasoning Agent - Implementation Summary

## ✅ What's Implemented

### **1. GPT-4 Query Parser**
- Analyzes natural language queries
- Extracts intent, topic, and dates
- Falls back to rule-based parsing if GPT-4 unavailable

### **2. Intent Classification**
Supports 4 types of queries:

| Intent | Example | What It Does |
|--------|---------|--------------|
| **current_search** | "Bitcoin predictions" | Search current markets |
| **historical_opinion** | "What was opinion on Nov 1 about X?" | Find market + historical price |
| **price_change** | "How did X change from Oct to Nov?" | Compare prices over time |
| **market_movement** | "When did market shift on X?" | Identify timing of shifts |

### **3. Orchestrator**
Routes queries to appropriate tools based on intent:
```python
if intent == "current_search":
    → search_polymarket_markets(topic)

elif intent == "historical_opinion":
    → search_polymarket_markets(topic)
    → get_market_history(market_id, date)  # Coming soon

elif intent == "price_change":
    → search_polymarket_markets(topic)
    → get_price_range(market_id, start, end)  # Coming soon

elif intent == "market_movement":
    → search_polymarket_markets(topic)
    → analyze_movements(market_id)  # Coming soon
```

## 🔧 Usage

### **Setup**
```bash
# Add OpenAI API key to config/keys.env
echo "OPENAI_API_KEY=sk-..." >> config/keys.env
```

### **Run Sample Queries**
```bash
# List all sample queries
python scripts/test_reasoning.py --list

# Run specific sample query
python scripts/test_reasoning.py --query 1
python scripts/test_reasoning.py --query 2  # Historical example

# Run custom query
python scripts/test_reasoning.py --custom "What was opinion on Nov 1 about federal shutdown?"
```

### **Sample Queries Included**
1. ✅ "Bitcoin price predictions" (current)
2. ✅ "What was the market opinion on November 1st about federal shutdown ending?" (historical)
3. ✅ "What did the market think about Ukraine ceasefire on Oct 15?" (historical)
4. ✅ "How did Bitcoin $100k predictions change from October to November?" (change)
5. ✅ "How has opinion shifted on Trump 2024 from last month to now?" (change)
6. ✅ "When did the market shift on Russia ceasefire?" (movement)
7. ✅ "Supreme Court decisions" (current)
8. ✅ "What was opinion on November 1 about government shutdown?" (your example)

## 📊 Example Output

```
[PARSED INTENT]
   Detected: historical_opinion
   Expected: historical_opinion
   Match: ✓ YES
   Confidence: 0.95

[EXTRACTED INFORMATION]
   Topic: federal shutdown ending
   Date: 2024-11-01
   Start Date: N/A
   End Date: N/A

[NOTE] Historical price data integration coming soon. Showing current markets.

[MARKETS FOUND] 3
1. Will federal shutdown end by December 31, 2025?
   URL: https://polymarket.com/event/...
   Prices: Yes 45.0%, No 55.0%
   Volume: $125,450
```

## 🚧 What's Coming Next

### **Phase 1: Historical Prices** (Next Implementation)
Create tool to fetch historical prices:
```python
@register_tool(name="get_market_history")
def get_market_history(market_id: str, date: str):
    """Fetch price at specific date using The Graph."""
    # Query The Graph subgraph
    # Return price on that date
```

### **Phase 2: Price Change Analysis**
```python
@register_tool(name="get_price_change")
def get_price_change(market_id: str, start_date: str, end_date: str):
    """Calculate price change over date range."""
    # Query The Graph for trades
    # Calculate change
```

### **Phase 3: Movement Detection**
```python
@register_tool(name="detect_market_movements")
def detect_market_movements(market_id: str):
    """Identify significant price shifts."""
    # Analyze price history
    # Detect sharp changes
```

## 🎯 Architecture

```
┌──────────────────────────────────────────────────────────┐
│  USER QUERY                                              │
│  "What was opinion on Nov 1 about shutdown ending?"     │
└──────────────────────────────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────────┐
│  REASONING AGENT (src/agents/reasoning_agent/)           │
│  ┌────────────────────────────────────────────────────┐  │
│  │  GPT-4 Parser                                      │  │
│  │  • Extract intent: "historical_opinion"            │  │
│  │  • Extract topic: "shutdown ending"                │  │
│  │  • Extract date: "2024-11-01"                      │  │
│  │  • Confidence: 0.95                                │  │
│  └────────────────────────────────────────────────────┘  │
│                        ↓                                  │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Orchestrator                                      │  │
│  │  Route to: _handle_historical_opinion()            │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────────┐
│  TOOL CALLS (via MCP Client)                            │
│  1. search_polymarket_markets("shutdown ending")         │
│  2. get_market_history(market_id, "2024-11-01") ← TODO  │
└──────────────────────────────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────────┐
│  FILE BUS OUTPUT                                         │
│  workspace/agents/reasoning-agent/out/000001.json        │
└──────────────────────────────────────────────────────────┘
```

## 🔑 Key Benefits

1. **Natural Language**: Users don't need to know query syntax
2. **Intent Understanding**: GPT-4 understands what user wants
3. **Date Parsing**: Handles "Nov 1", "November 1st", "last week", etc.
4. **Extensible**: Easy to add new intents and tools
5. **Fallback**: Works without GPT-4 (rule-based parsing)

## 🧪 Testing

```bash
# Test current search (works now)
python scripts/test_reasoning.py --query 1

# Test historical search (finds market, needs historical price tool)
python scripts/test_reasoning.py --query 2

# Test your specific example
python scripts/test_reasoning.py --custom "What was opinion on Nov 1 about federal shutdown ending?"
```

## 📝 Next Steps

1. ✅ **Try the reasoning agent** with your example query
2. ⏳ **Implement historical price tool** (The Graph integration)
3. ⏳ **Add price change analysis**
4. ⏳ **Add movement detection**

## 🚀 Try It Now!

```bash
# Make sure OpenAI API key is set
export OPENAI_API_KEY="sk-..."

# Test with your example
python scripts/test_reasoning.py --custom "What was the market opinion on November 1st about federal shutdown ending?"
```

The reasoning agent will:
1. ✅ Parse your query with GPT-4
2. ✅ Identify intent: "historical_opinion"
3. ✅ Extract: topic="shutdown ending", date="2024-11-01"
4. ✅ Find relevant markets
5. ⏳ Return note that historical prices coming soon

Once we add the historical price tool (The Graph), it will also return the actual price on Nov 1st!


