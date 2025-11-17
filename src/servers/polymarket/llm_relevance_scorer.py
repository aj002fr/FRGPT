"""
LLM-Powered Relevance Scoring for Polymarket Search

Uses GPT-4 to evaluate semantic relevance of markets to user queries.
Much more accurate than keyword matching.
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


def score_market_relevance_batch(
    query: str,
    markets: List[Dict[str, Any]],
    top_k: int = 10,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Use GPT-4 to score market relevance to query.
    
    Strategy:
    1. Send query + batch of market titles to GPT-4
    2. GPT-4 returns relevance scores (0-10) for each
    3. Sort by relevance and return top K
    
    Fallback behavior:
    - Always returns at least 1 market if any exist
    - Falls back to keyword matching if API key unavailable
    
    Args:
        query: User's search query
        markets: List of market dicts with 'title' field
        top_k: Number of top markets to return
        api_key: OpenAI API key (optional, will try to load)
        
    Returns:
        List of markets sorted by relevance with scores (at least 1 if markets exist)
    """
    if not markets:
        return []
    
    # Load API key if not provided
    if not api_key:
        api_key = _load_api_key()
    
    if not api_key:
        logger.warning("No OpenAI API key found, falling back to keyword matching")
        return markets[:top_k]  # Fallback
    
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("OpenAI library not installed, falling back to keyword matching")
        return markets[:top_k]
    
    client = OpenAI(api_key=api_key)
    
    # Prepare market list for GPT-4
    market_list = []
    for i, market in enumerate(markets[:50]):  # Limit to 50 for API cost/speed
        market_list.append({
            "id": i,
            "title": market.get("title", "")[:150]  # Truncate long titles
        })
    
    # Create prompt
    prompt = f"""You are a search relevance scorer for prediction markets.

User Query: "{query}"

Below are prediction market titles. Score each market's relevance to the query on a scale of 0-10:
- 10: Highly relevant, directly answers the query
- 7-9: Very relevant, related to main topic
- 4-6: Somewhat relevant, tangentially related
- 1-3: Barely relevant, weak connection
- 0: Not relevant at all

Markets:
{json.dumps(market_list, indent=2)}

Return ONLY a JSON array with relevance scores in this exact format:
[
  {{"id": 0, "score": 8.5, "reason": "brief explanation"}},
  {{"id": 1, "score": 3.0, "reason": "brief explanation"}},
  ...
]

Be strict: only give high scores (7+) to markets that truly match the query intent.
For "federal shutdown", "Fed rate cuts" should score low (different topics).
For "Bitcoin predictions", only Bitcoin price markets should score high, not generic crypto.
"""

    try:
        logger.info(f"Calling GPT-4 to score {len(market_list)} markets for query: '{query}'")
        
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,  # Low temperature for consistent scoring
            max_tokens=2000
        )
        
        # Parse response
        content = response.choices[0].message.content.strip()
        
        # Remove markdown code blocks if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        
        scores = json.loads(content)
        
        logger.info(f"Received {len(scores)} relevance scores from GPT-4")
        
        # Apply scores to markets (include ALL scored markets, no filtering)
        scored_markets = []
        for score_data in scores:
            market_id = score_data.get("id")
            score = score_data.get("score", 0)
            reason = score_data.get("reason", "")
            
            if market_id < len(markets):
                market = markets[market_id].copy()
                market["relevance_score"] = score / 10  # Normalize to 0-1
                market["relevance_reason"] = reason
                scored_markets.append(market)
        
        # Sort by score (highest first)
        scored_markets.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        
        # Always return at least 1 result if we have any markets
        if scored_markets and top_k > 0:
            result = scored_markets[:max(1, top_k)]
        else:
            result = scored_markets[:top_k]
        
        logger.info(f"Returning {len(result)} relevant markets (scored from {len(markets)})")
        if result:
            logger.info(f"Top result: '{result[0].get('title', '')[:60]}' (score: {result[0].get('relevance_score', 0):.2f})")
        
        return result
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse GPT-4 response as JSON: {e}")
        logger.debug(f"Response content: {content[:200]}")
        return markets[:top_k]  # Fallback
        
    except Exception as e:
        logger.error(f"LLM relevance scoring failed: {e}")
        return markets[:top_k]  # Fallback


def score_market_relevance_streaming(
    query: str,
    markets: List[Dict[str, Any]],
    top_k: int = 10,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Alternative: Score markets one-by-one using simpler prompts.
    
    Faster and cheaper for small market sets, but less context-aware.
    
    Args:
        query: User's search query
        markets: List of market dicts
        top_k: Number of top markets to return
        api_key: OpenAI API key
        
    Returns:
        Scored and sorted markets
    """
    if not markets:
        return []
    
    # Load API key
    if not api_key:
        api_key = _load_api_key()
    
    if not api_key:
        logger.warning("No OpenAI API key, using batch scoring fallback")
        return markets[:top_k]
    
    try:
        from openai import OpenAI
    except ImportError:
        return markets[:top_k]
    
    client = OpenAI(api_key=api_key)
    
    scored_markets = []
    
    # Score each market individually (simple prompt)
    for i, market in enumerate(markets[:30]):  # Limit for cost
        title = market.get("title", "")
        
        prompt = f"""Rate the relevance of this prediction market to the user's query.

Query: "{query}"
Market: "{title}"

Score 0-10 where:
- 10: Perfect match, directly answers query
- 7-9: Highly relevant
- 4-6: Somewhat relevant
- 0-3: Not relevant

Return only a JSON object: {{"score": X.X, "reason": "brief explanation"}}"""

        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=100
            )
            
            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            
            result = json.loads(content)
            score = result.get("score", 0)
            
            # Include all markets regardless of score
            market_copy = market.copy()
            market_copy["relevance_score"] = score / 10
            market_copy["relevance_reason"] = result.get("reason", "")
            scored_markets.append(market_copy)
                
        except Exception as e:
            logger.warning(f"Failed to score market {i}: {e}")
            continue
    
    # Sort by score
    scored_markets.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    
    # Always return at least 1 result if we have any markets
    if scored_markets and top_k > 0:
        return scored_markets[:max(1, top_k)]
    return scored_markets[:top_k]


def _load_api_key() -> Optional[str]:
    """Load OpenAI API key from config/keys.env."""
    # Try environment variable first
    api_key = os.environ.get('OPENAI_API_KEY')
    if api_key:
        return api_key
    
    # Try config file
    try:
        config_dir = Path(__file__).parent.parent.parent.parent / "config"
        env_file = config_dir / "keys.env"
        
        if env_file.exists():
            with open(env_file, 'r') as f:
                for line in f:
                    if line.startswith('OPENAI_API_KEY='):
                        return line.split('=', 1)[1].strip()
    except Exception as e:
        logger.debug(f"Failed to load API key from file: {e}")
    
    return None


# Hybrid approach: keyword filter + LLM re-rank
def hybrid_search(
    query: str,
    all_markets: List[Dict[str, Any]],
    keywords: List[str],
    keyword_filter_func,
    top_k: int = 10,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Hybrid approach: Fast keyword filter + Accurate LLM re-ranking.
    
    Strategy:
    1. Use keyword matching to get top 50 candidates (fast)
    2. Use GPT-4 to re-rank those 50 by true relevance (accurate)
    3. Return top K
    
    Fallback behavior:
    - If keyword filter finds nothing, returns top market by volume
    - If LLM scoring fails, returns top keyword match
    - Always returns at least 1 result if any markets exist
    
    Args:
        query: User's search query
        all_markets: Full list of markets
        keywords: Extracted keywords from query
        keyword_filter_func: Function to do keyword filtering
        top_k: Number of results to return
        api_key: OpenAI API key
        
    Returns:
        Top K most relevant markets (at least 1 if markets exist)
    """
    logger.info(f"Hybrid search: filtering {len(all_markets)} markets")
    
    # Step 1: Keyword filter to get top 50 candidates
    keyword_filtered = keyword_filter_func(all_markets, keywords)
    candidates = keyword_filtered[:50]  # Top 50 from keyword matching
    
    logger.info(f"Keyword filter found {len(candidates)} candidates")
    
    # If keyword filter found nothing, return top market by volume as fallback
    if not candidates and all_markets:
        logger.warning("Keyword filter found no matches, returning top market by volume")
        # Return top market from all_markets (sorted by volume if available)
        sorted_markets = sorted(
            all_markets, 
            key=lambda x: x.get('volume', 0), 
            reverse=True
        )
        return sorted_markets[:max(1, top_k)]
    elif not candidates:
        return []
    
    # Step 2: LLM re-rank
    llm_scored = score_market_relevance_batch(
        query=query,
        markets=candidates,
        top_k=top_k,
        api_key=api_key
    )
    
    logger.info(f"LLM scoring returned {len(llm_scored)} relevant markets")
    
    # Ensure we always return at least 1 result if we had candidates
    if not llm_scored and candidates:
        logger.warning("LLM scoring returned nothing, falling back to top keyword match")
        return candidates[:max(1, top_k)]
    
    return llm_scored

