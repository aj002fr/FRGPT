"""Perplexity API client for market news search (stdlib-only)."""

import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.mcp.discovery import register_tool

logger = logging.getLogger(__name__)

# API endpoint
PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_MODEL = "sonar"  # Perplexity's online search model
DEFAULT_TIMEOUT = 30


def _get_api_key() -> Optional[str]:
    """Load Perplexity API key from config or environment."""
    # Try environment first
    if "PERPLEXITY_API_KEY" in os.environ:
        return os.environ["PERPLEXITY_API_KEY"]
    
    # Try config file
    config_file = Path(__file__).parent.parent.parent.parent / "config" / "keys.env"
    if config_file.exists():
        with open(config_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("PERPLEXITY_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


def _call_perplexity_api(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    max_tokens: int = 500,
    temperature: float = 0.1,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    Call Perplexity API using stdlib urllib.
    
    Args:
        messages: List of message dicts with 'role' and 'content'
        model: Model name (default: sonar)
        max_tokens: Max tokens for response
        temperature: Temperature for generation
        timeout: Request timeout in seconds
        
    Returns:
        Full API response dict or error dict
    """
    api_key = _get_api_key()
    if not api_key:
        return {
            "success": False,
            "error": "No Perplexity API key configured",
        }
    
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    
    data = json.dumps(payload).encode("utf-8")
    
    req = urllib.request.Request(
        PERPLEXITY_API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    
    # Create SSL context (some environments need this)
    ssl_context = ssl.create_default_context()
    
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))
            return {
                "success": True,
                "data": response_data,
            }
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        logger.error(f"Perplexity API HTTP error {e.code}: {error_body}")
        return {
            "success": False,
            "error": f"HTTP {e.code}: {error_body[:200]}",
        }
    except urllib.error.URLError as e:
        logger.error(f"Perplexity API URL error: {e.reason}")
        return {
            "success": False,
            "error": f"URL error: {e.reason}",
        }
    except TimeoutError:
        logger.error("Perplexity API timeout")
        return {
            "success": False,
            "error": "Request timeout",
        }
    except Exception as e:
        logger.error(f"Perplexity API error: {e}")
        return {
            "success": False,
            "error": str(e),
        }


def _extract_content_and_citations(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract content and citations from Perplexity response.
    
    Perplexity may return citations in the response; extract them if present.
    """
    content = ""
    citations = []
    
    choices = response_data.get("choices", [])
    if choices:
        message = choices[0].get("message", {})
        content = message.get("content", "").strip()
    
    # Perplexity sometimes includes citations in the response metadata
    # Check for 'citations' field at root or in message
    if "citations" in response_data:
        raw_citations = response_data["citations"]
        if isinstance(raw_citations, list):
            for c in raw_citations:
                if isinstance(c, str):
                    citations.append({"url": c, "title": ""})
                elif isinstance(c, dict):
                    citations.append({
                        "url": c.get("url", ""),
                        "title": c.get("title", ""),
                    })
    
    return {
        "content": content,
        "citations": citations,
        "model": response_data.get("model", DEFAULT_MODEL),
    }


def _try_parse_json_content(content: str) -> Optional[Dict[str, Any]]:
    """Attempt to parse JSON from a Perplexity response content string."""
    try:
        json_str = content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
        return json.loads(json_str)
    except (json.JSONDecodeError, ValueError, IndexError):
        return None


def _parse_fade_value_to_bps(fade_amount: Optional[float], fade_unit: Optional[str]) -> Optional[float]:
    """Convert a fade amount with unit to bps (if possible)."""
    if fade_amount is None or fade_unit is None:
        return None

    unit = fade_unit.lower().strip()
    try:
        value = float(fade_amount)
    except (TypeError, ValueError):
        return None

    if unit in ("bp", "bps", "basis point", "basis points"):
        return value
    if unit in ("tick", "ticks", "32nds", "32nd"):
        # 1 tick = 1/4 bp for TY
        return value * 0.25
    if unit in ("point", "points", "pt", "pts"):
        # 1 point = 8 bps for TY
        return value * 8.0

    return None


@register_tool(
    name="search_market_news",
    description="Search Perplexity for news explaining unusual market moves on a specific date.",
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Date to search for (YYYY-MM-DD)"},
            "symbol": {"type": "string", "description": "Market symbol (default: TY)"},
            "context": {"type": "string", "description": "Additional context about the move"},
        },
        "required": ["date"],
    },
    agent="anomaly_notes_agent",
)
def search_market_news(
    date: str,
    symbol: str = "TY",
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Search Perplexity for news explaining unusual market moves.
    
    Args:
        date: Date to search for (YYYY-MM-DD)
        symbol: Market symbol (default: TY for 10-Year Treasury futures)
        context: Additional context about the move (e.g., "large 45 bps range")
        
    Returns:
        Dictionary with search results:
        - success: Whether search succeeded
        - summary: AI-generated summary of news
        - source: "perplexity"
        - error: Error message if failed
    """
    # Format date for natural language
    try:
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        date_str = date_obj.strftime("%B %d, %Y")  # e.g., "January 15, 2025"
    except ValueError:
        date_str = date
    
    # Build query
    context_str = f" The market showed {context}." if context else ""
    query = (
        f"What happened in US Treasury markets on {date_str}? "
        f"Were there any major economic releases, Fed announcements, geopolitical events, "
        f"or other news that could have moved 10-year Treasury futures?{context_str} "
        f"Provide a brief 2-3 sentence summary."
    )
    
    logger.info(f"Searching Perplexity for: {date}")
    
    result = _call_perplexity_api(
        messages=[{"role": "user", "content": query}],
        max_tokens=250,
        temperature=0.1,
    )
    
    if not result["success"]:
        return {
            "success": False,
            "summary": None,
            "source": "perplexity",
            "error": result.get("error", "Unknown error"),
        }
    
    extracted = _extract_content_and_citations(result["data"])
    logger.info(f"Perplexity returned: {extracted['content'][:100]}...")
    
    return {
        "success": True,
        "summary": extracted["content"],
        "citations": extracted["citations"],
        "source": "perplexity",
        "date": date,
        "model": extracted["model"],
    }


def search_market_fade(
    date: str,
    event_name: str,
    symbol: str = "TY",
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Search Perplexity for how much the market faded after an event.

    Returns:
        - success: Whether search succeeded
        - fade_bps: Fade estimate converted to bps if available
        - fade_amount: Raw fade value if provided
        - fade_unit: Raw unit (bps/ticks/points/unknown)
        - summary: Brief summary from Perplexity
        - citations: List of sources if available
        - source: "perplexity"
        - error: Error message if failed
    """
    try:
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        date_str = date_obj.strftime("%B %d, %Y")
    except ValueError:
        date_str = date

    context_str = f" Context: {context}." if context else ""
    query = (
        f"For the event '{event_name}' on {date_str}, summarize what happened in US 10-year "
        f"Treasury futures ({symbol}). Provide a concise but specific summary that includes: "
        f"(1) approximate move size in bps (if available), (2) the time window in ET "
        f"(when the move occurred), and (3) key headlines or drivers. If there were multiple "
        f"distinct moves, mention up to two with timing."
        f"{context_str}"
    )

    system_prompt = (
        "Return ONLY valid JSON with fields: "
        "fade_amount (number or null), fade_unit (bps|ticks|points|unknown), "
        "summary (string), window (string or null), confidence (0-1). "
        "If you cannot quantify a bps move, set fade_amount to null but still provide a summary."
    )

    logger.info(f"Searching Perplexity for fade: {date} - {event_name}")

    result = _call_perplexity_api(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        max_tokens=450,
        temperature=0.1,
    )

    if not result["success"]:
        return {
            "success": False,
            "fade_bps": None,
            "fade_amount": None,
            "fade_unit": None,
            "summary": None,
            "citations": [],
            "source": "perplexity",
            "error": result.get("error", "Unknown error"),
        }

    extracted = _extract_content_and_citations(result["data"])
    content = extracted["content"]
    parsed = _try_parse_json_content(content) or {}

    fade_amount = parsed.get("fade_amount")
    fade_unit = parsed.get("fade_unit", "unknown")
    summary = parsed.get("summary", content)

    fade_bps = _parse_fade_value_to_bps(fade_amount, fade_unit)

    return {
        "success": True,
        "fade_bps": fade_bps,
        "fade_amount": fade_amount,
        "fade_unit": fade_unit,
        "summary": summary,
        "window": parsed.get("window"),
        "confidence": parsed.get("confidence"),
        "citations": extracted["citations"],
        "source": "perplexity",
        "date": date,
        "model": extracted["model"],
    }


# =============================================================================
# FOMC Sentiment Labeling
# =============================================================================

FOMC_SENTIMENT_PROMPT_VERSION = "v1"

FOMC_SENTIMENT_SYSTEM_PROMPT = """You are an expert Fed watcher analyzing FOMC press conference outcomes.

Given an FOMC meeting date, determine the prevailing market sentiment based on:
- Rate decision (hold, hike, cut)
- Forward guidance language
- Chair's press conference tone
- Market reaction (Treasuries, equities)

Classify the outcome as:
- "hawkish": Fed signaled tighter policy, rates higher for longer, inflation concerns
- "dovish": Fed signaled easier policy, rate cuts coming, employment concerns
- "neutral": Balanced tone, no clear directional signal
- "unknown": Insufficient information to classify

Respond with ONLY valid JSON in this exact format:
{"label": "hawkish|dovish|neutral|unknown", "confidence": 0.0-1.0, "summary": "2-3 sentence summary of what happened"}"""


def search_fomc_sentiment(
    date: str,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Search for FOMC meeting sentiment classification for a specific date.
    
    Args:
        date: FOMC meeting date (YYYY-MM-DD)
        force_refresh: If True, bypass any caching (handled by caller)
        
    Returns:
        Dictionary with:
        - success: Whether search succeeded
        - label: "hawkish" | "dovish" | "neutral" | "unknown"
        - confidence: Float 0-1
        - summary: Brief summary of what happened
        - citations: List of {url, title} dicts if available
        - source: "perplexity"
        - prompt_version: Version of the prompt used
        - error: Error message if failed
    """
    # Format date for natural language
    try:
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        date_str = date_obj.strftime("%B %d, %Y")
    except ValueError:
        date_str = date
    
    user_query = (
        f"On {date_str}, what was the outcome of the FOMC meeting and Fed press conference? "
        f"Was the overall tone hawkish, dovish, or neutral? "
        f"What did Chair Powell emphasize? How did Treasury markets react?"
    )
    
    logger.info(f"Searching FOMC sentiment for: {date}")
    
    result = _call_perplexity_api(
        messages=[
            {"role": "system", "content": FOMC_SENTIMENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_query},
        ],
        max_tokens=400,
        temperature=0.1,
    )
    
    if not result["success"]:
        return {
            "success": False,
            "label": "unknown",
            "confidence": 0.0,
            "summary": None,
            "citations": [],
            "source": "perplexity",
            "prompt_version": FOMC_SENTIMENT_PROMPT_VERSION,
            "error": result.get("error", "Unknown error"),
        }
    
    extracted = _extract_content_and_citations(result["data"])
    content = extracted["content"]
    
    # Try to parse JSON from response
    label = "unknown"
    confidence = 0.0
    summary = content  # Fallback to raw content
    
    # Try to extract JSON from response
    try:
        # Look for JSON in the response (may be wrapped in markdown code blocks)
        json_str = content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
        
        parsed = json.loads(json_str)
        label = parsed.get("label", "unknown")
        confidence = float(parsed.get("confidence", 0.0))
        summary = parsed.get("summary", content)
        
        # Validate label
        if label not in ("hawkish", "dovish", "neutral", "unknown"):
            label = "unknown"
            
    except (json.JSONDecodeError, ValueError, IndexError) as e:
        logger.warning(f"Could not parse JSON from response: {e}")
        # Keep raw content as summary, label as unknown
        summary = content
    
    logger.info(f"FOMC sentiment for {date}: {label} (confidence: {confidence:.2f})")
    
    return {
        "success": True,
        "label": label,
        "confidence": confidence,
        "summary": summary,
        "citations": extracted["citations"],
        "source": "perplexity",
        "date": date,
        "prompt_version": FOMC_SENTIMENT_PROMPT_VERSION,
        "model": extracted["model"],
    }
