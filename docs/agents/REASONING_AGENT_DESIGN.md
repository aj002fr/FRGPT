# Reasoning Agent for Historical Market Queries

## Problem Statement

**Current System**: Can only search current markets and return current prices
**User Need**: Query historical market opinions at specific dates

**Example Queries**:
- "What was the market opinion on November 1st about federal shutdown ending?"
- "How did the Bitcoin $100k prediction change from Oct 1 to Nov 1?"
- "When did the market shift on Ukraine ceasefire?"

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     USER QUERY                               │
│  "What was opinion on Nov 1 about shutdown ending?"          │
└──────────────────────────────────────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────────┐
│              1. REASONING AGENT                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Query Parser (LLM or rule-based)                      │  │
│  │  • Extract intent: "historical_opinion"                │  │
│  │  │  Extract topic: "federal shutdown end"              │  │
│  │  │  Extract date: "2024-11-01"                         │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────────┐
│              2. ORCHESTRATOR                                 │
│  Based on intent, coordinate tool calls:                     │
│                                                              │
│  IF intent = "historical_opinion":                           │
│    a) search_polymarket_markets(topic) → get market_id      │
│    b) get_market_history(market_id, date) → get price       │
│    c) format_response()                                      │
└──────────────────────────────────────────────────────────────┘
        ↓                                    ↓
┌─────────────────────┐      ┌─────────────────────────────────┐
│  EXISTING TOOLS     │      │  NEW TOOL (need to create)      │
│  • search_markets   │      │  • get_market_history           │
│  • search_polymarket│      │    - Fetch historical prices    │
└─────────────────────┘      │    - Filter by date range       │
                             │    - Return time-series data     │
                             └─────────────────────────────────┘
```

## Components Needed

### 1. Query Parser (Simple First, LLM Later)

**Simple Rule-Based Approach** (no LLM required):
```python
def parse_query(query: str) -> Dict[str, Any]:
    """
    Parse query to extract intent, topic, and date.
    
    Rules:
    - "what was" + date → historical_opinion
    - "how did" + two dates → price_change
    - Keywords: "opinion", "thought", "predicted" → opinion intent
    """
    result = {
        "intent": None,
        "topic": None,
        "date": None,
        "start_date": None,
        "end_date": None
    }
    
    # Detect intent
    if "what was" in query.lower() and has_date(query):
        result["intent"] = "historical_opinion"
    elif "how did" in query.lower() and has_date_range(query):
        result["intent"] = "price_change"
    else:
        result["intent"] = "current_search"
    
    # Extract dates using dateparser or regex
    dates = extract_dates(query)
    if dates:
        result["date"] = dates[0]
    
    # Extract topic (everything except date words)
    result["topic"] = extract_topic(query)
    
    return result
```

**Advanced LLM Approach** (GPT-4):
```python
def parse_query_with_llm(query: str) -> Dict[str, Any]:
    """Use GPT-4 to parse complex queries."""
    prompt = f"""
    Parse this market query and extract structured information:
    
    Query: "{query}"
    
    Return JSON:
    {{
        "intent": "historical_opinion" | "current_search" | "price_change",
        "topic": "what market to search for",
        "date": "YYYY-MM-DD or null",
        "start_date": "YYYY-MM-DD or null",
        "end_date": "YYYY-MM-DD or null"
    }}
    
    Examples:
    - "What was opinion on Nov 1 about shutdown?" 
      → {{"intent": "historical_opinion", "topic": "shutdown", "date": "2024-11-01"}}
    
    - "Bitcoin price predictions"
      → {{"intent": "current_search", "topic": "bitcoin price", "date": null}}
    """
    
    response = openai.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    
    return json.loads(response.choices[0].message.content)
```

### 2. Historical Price Tool (NEW - Need to Create)

**Polymarket Historical API**:
The Polymarket API likely has endpoints like:
- `/markets/{market_id}/prices` - price history
- `/markets/{market_id}/trades` - trade history

```python
@register_tool(
    name="get_market_history",
    description="Get historical prices for a Polymarket market"
)
def get_market_history(
    market_id: str,
    date: str = None,  # "2024-11-01"
    start_date: str = None,
    end_date: str = None
) -> Dict[str, Any]:
    """
    Fetch historical price data for a market.
    
    Args:
        market_id: Polymarket market ID
        date: Single date to get price at
        start_date: Start of date range
        end_date: End of date range
        
    Returns:
        {
            "market_id": "...",
            "prices": [
                {"date": "2024-11-01", "yes": 0.65, "no": 0.35},
                {"date": "2024-11-02", "yes": 0.67, "no": 0.33}
            ]
        }
    """
    # Call Polymarket history API
    # Polymarket Gamma API likely has: 
    # GET /markets/{market_id}/price-history
    
    url = f"https://gamma-api.polymarket.com/markets/{market_id}/price-history"
    # Add date filters
    
    # Parse response
    # Filter by date
    # Return formatted data
```

### 3. Orchestrator (in ReasoningAgent)

```python
class ReasoningAgent:
    def run(self, query: str) -> str:
        # Step 1: Parse query
        parsed = parse_query(query)
        
        # Step 2: Route based on intent
        if parsed["intent"] == "current_search":
            return self._handle_current_search(parsed)
        
        elif parsed["intent"] == "historical_opinion":
            return self._handle_historical_opinion(parsed)
        
        elif parsed["intent"] == "price_change":
            return self._handle_price_change(parsed)
    
    def _handle_historical_opinion(self, parsed: Dict) -> str:
        # Find the market
        markets = self.client.call_tool(
            "search_polymarket_markets",
            {"query": parsed["topic"], "session_id": self.session_id, "limit": 1}
        )
        
        if not markets["markets"]:
            return f"No markets found about {parsed['topic']}"
        
        market = markets["markets"][0]
        market_id = market["market_id"]
        
        # Get historical price
        history = self.client.call_tool(
            "get_market_history",
            {"market_id": market_id, "date": parsed["date"]}
        )
        
        # Format response
        price = history["prices"][0] if history["prices"] else None
        if price:
            return f"""
On {parsed['date']}, the market opinion on "{market['title']}" was:
- YES: {price['yes']*100:.1f}%
- NO: {price['no']*100:.1f}%

Market URL: {market['url']}
            """.strip()
```

## Implementation Steps

### Phase 1: Simple (No LLM, Current Date Only) ⚡
**Time: 2-3 hours**
1. ✅ User can only ask about current markets (existing)
2. Add basic date parsing (dateparser library)
3. If date detected → return message "Historical data coming soon"

### Phase 2: Historical Data (No LLM) 📊
**Time: 4-6 hours**
1. Research Polymarket historical price API
2. Create `get_market_history` tool
3. Simple rule-based query parser (detect "what was" + date)
4. Orchestrator to chain: search → get history → format

### Phase 3: Full Reasoning (LLM) 🧠
**Time: 6-8 hours**
1. Add GPT-4 query parsing
2. Support complex queries:
   - "How did X change from A to B?"
   - "When did market shift on X?"
3. Advanced orchestration

## Alternative: Simpler Hybrid Approach

**Don't create a separate reasoning agent**. Instead:

1. **Enhance existing `search_polymarket_markets` tool** to accept date:
```python
@register_tool(name="search_polymarket_markets")
def search_polymarket_markets(
    query: str,
    session_id: str,
    date: str = None,  # ← NEW PARAMETER
    limit: int = 10
):
    # 1. Search for markets (existing code)
    markets = find_markets(query)
    
    # 2. If date provided, enhance with historical prices
    if date:
        for market in markets:
            hist = get_market_price_at_date(market['market_id'], date)
            market['historical_price'] = hist
            market['historical_date'] = date
    
    return markets
```

2. **User calls with date**:
```python
agent.run(
    query="federal shutdown end",
    date="2024-11-01"  # User manually specifies
)
```

This is **much simpler** but less flexible (no natural language date parsing).

## Recommendation

**Start with Hybrid Approach**:
1. ✅ Add `date` parameter to existing tool
2. ✅ Create `get_market_history` tool  
3. ✅ Document how users can query historical data
4. ⏳ Later: Add reasoning agent for NL date parsing

This gives you 80% of value with 20% of effort!

## Next Steps

Would you like me to:
1. ✅ Create the `get_market_history` tool (research Polymarket API first)
2. ✅ Add date parameter to `search_polymarket_markets`
3. ✅ Create full reasoning agent with LLM parsing
4. ❓ Something else?

