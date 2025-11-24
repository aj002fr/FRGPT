"""LLM-powered trading lexicon builder from Telegram messages.

Pipeline:
- Read messages JSON (e.g. user_messages.json from telegram_user_extractor.py)
- Extract candidate terms + example sentences
- Ask GPT-5 to infer meaning and confidence for each term
- Write enriched lexicon JSON with meaning + confidence scores
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List

from config.settings import TRADING_LEXICON_PATH, get_api_key
from src.core.text_normalization import normalize_message

logger = logging.getLogger(__name__)


def _load_openai_api_key() -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        return api_key
    try:
        return get_api_key("OPENAI_API_KEY")
    except Exception:
        raise RuntimeError(
            "OPENAI_API_KEY not found. Set env var or add OPENAI_API_KEY=... to config/keys.env"
        )


def _extract_candidates(
    messages: Iterable[Dict[str, Any]],
    min_frequency: int,
    max_examples: int = 3,
) -> Dict[str, Dict[str, Any]]:
    """Candidate term extractor, biased toward finance-specific tokens.

    Heuristics:
    - Drop stopwords and obvious noise.
    - Consider a message "financial" if it contains FINANCE_MARKERS or
      tokens classified as ticker/syntax.
    - Keep a token if it is:
      * classified as ticker/syntax/acronym, or
      * explicitly in AMBIGUOUS_TERMS, or
      * seen in at least one financial message.
    """
    from src.core.trading_lexicon import (
        STOPWORDS,
        AMBIGUOUS_TERMS,
        FINANCE_MARKERS,
        _classify_kind,
    )

    stats: Dict[str, Dict[str, Any]] = {}

    for msg in messages:
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        norm = normalize_message(text)

        tokens = norm.tokens
        tokens_lower = [t.lower() for t in tokens]
        has_fin_marker = any(t in FINANCE_MARKERS for t in tokens_lower)
        has_ticker_like = any(_classify_kind(t) in {"ticker", "syntax"} for t in tokens)
        in_financial_context = has_fin_marker or has_ticker_like

        for token in tokens:
            lower = token.lower()
            if len(token) <= 1 or token.isdigit():
                continue
            if lower in STOPWORDS:
                continue
            # Skip obviously non-word noise
            if all(ch in ".,;:!?\"'()[]{}" for ch in token):
                continue

            kind = _classify_kind(token)

            # Decide whether this token is worth tracking at all
            important_shape = kind in {"ticker", "syntax", "acronym"}
            explicitly_ambiguous = lower in AMBIGUOUS_TERMS
            if not (important_shape or explicitly_ambiguous or in_financial_context):
                continue

            entry = stats.setdefault(
                token,
                {"frequency": 0, "examples": [], "finance_hits": 0},  # type: ignore[dict-item]
            )
            entry["frequency"] += 1  # type: ignore[assignment]
            if in_financial_context:
                entry["finance_hits"] += 1  # type: ignore[assignment]
            ex_list: List[str] = entry["examples"]  # type: ignore[assignment]
            if len(ex_list) < max_examples and norm.cleaned_text not in ex_list:
                ex_list.append(norm.cleaned_text)

    # Apply frequency + finance filters
    filtered: Dict[str, Dict[str, Any]] = {}
    for term, info in stats.items():
        freq = info.get("frequency", 0)
        finance_hits = info.get("finance_hits", 0)
        lower = term.lower()
        kind = _classify_kind(term)
        important_shape = kind in {"ticker", "syntax", "acronym"}
        explicitly_ambiguous = lower in AMBIGUOUS_TERMS

        if freq < min_frequency:
            continue
        if finance_hits == 0 and not (important_shape or explicitly_ambiguous):
            continue

        filtered[term] = {"frequency": freq, "examples": info.get("examples", [])}  # type: ignore[assignment]

    return filtered


def _chunk_terms(terms: List[str], chunk_size: int) -> Iterable[List[str]]:
    for i in range(0, len(terms), chunk_size):
        yield terms[i : i + chunk_size]


def _call_llm_for_terms(
    client,
    model: str,
    batch: List[str],
    candidates: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Ask GPT-5 to describe meaning + confidence for each term."""
    # Build compact payload with a few examples per term
    items = []
    for term in batch:
        info = candidates[term]
        examples = info.get("examples", [])[:2]
        items.append(
            {
                "term": term,
                "frequency": info.get("frequency", 0),
                "examples": examples,
            }
        )

    prompt = f"""
You are analyzing internal hedge-fund trading chat logs about futures, bonds and options.
For each term below, infer:
- how it is used in this trading context
- how confident you are that you understand it (0.0-1.0)
- whether its meaning here is finance-specific or could be confused with a generic meaning.

Return ONLY a JSON array with this exact shape:
[
  {{
    "term": "TERM",
    "meaning": "short description of how it is used in this context",
    "confidence": 0.0-1.0,
    "finance_specific": true/false,
    "notes": "optional short note"
  }},
  ...
]

Terms with examples:
{json.dumps(items, ensure_ascii=False, indent=2)}
"""

    logger.info("Calling %s for %d terms", model, len(batch))
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_completion_tokens=2000,
    )

    content = response.choices[0].message.content.strip()
    if content.startswith("```"):
        # Strip markdown fences if present
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1]
            if content.lstrip().startswith("json"):
                content = content.lstrip()[4:]
        content = content.strip()

    try:
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError("LLM did not return a JSON list")
        return data
    except Exception as exc:
        logger.error("Failed to parse LLM response: %s", exc)
        logger.debug("Raw content: %s", content[:500])
        return []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a GPT-5-enriched trading lexicon from Telegram messages."
    )
    parser.add_argument(
        "--input-json",
        type=str,
        default="user_messages.json",
        help="Path to JSON file with exported Telegram messages.",
    )
    parser.add_argument(
        "--min-frequency",
        type=int,
        default=3,
        help="Minimum frequency for a term to be considered.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-5.1",
        help="OpenAI model name to use (e.g. gpt-5.1).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Number of terms to send per LLM request.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(TRADING_LEXICON_PATH),
        help="Output JSON path for the LLM-enriched lexicon.",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    input_path = Path(args.input_json)
    if not input_path.exists():
        raise FileNotFoundError(f"Input messages JSON not found: {input_path}")

    messages = json.loads(input_path.read_text(encoding="utf-8"))
    logger.info("Loaded %d messages from %s", len(messages), input_path)

    candidates = _extract_candidates(messages, min_frequency=args.min_frequency)
    terms = sorted(candidates.keys())
    logger.info("Extracted %d candidate terms (min_frequency=%d)", len(terms), args.min_frequency)

    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package is not installed. Run `pip install openai` to use this script.")

    api_key = _load_openai_api_key()
    client = OpenAI(api_key=api_key)

    enriched: List[Dict[str, Any]] = []
    for batch in _chunk_terms(terms, args.batch_size):
        llm_results = _call_llm_for_terms(client, args.model, batch, candidates)
        # Merge in frequency + examples
        for item in llm_results:
            term = item.get("term")
            if term not in candidates:
                continue
            base = candidates[term]
            item["frequency"] = base.get("frequency", 0)
            item["examples"] = base.get("examples", [])
            enriched.append(item)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")

    # Count how many terms the model is uncertain about
    low_conf = [e for e in enriched if isinstance(e.get("confidence"), (int, float)) and e["confidence"] < 0.7]

    print(f"Wrote {len(enriched)} LLM-enriched lexicon entries to {output_path}")
    print(f"Low-confidence / potentially ambiguous terms (<0.7): {len(low_conf)}")


if __name__ == "__main__":
    main()


