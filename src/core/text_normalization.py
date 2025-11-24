"""Text normalization and tokenization for trading chat messages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


URL_RE = re.compile(r"https?://\S+")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class NormalizedMessage:
    """Normalized message text and token list."""

    cleaned_text: str
    tokens: List[str]


def _strip_urls(text: str) -> str:
    return URL_RE.sub("", text)


def _clean_whitespace(text: str) -> str:
    text = text.replace("\n", " ").replace("\r", " ")
    return WHITESPACE_RE.sub(" ", text).strip()


def _tokenize(text: str) -> List[str]:
    raw_tokens = text.split(" ")
    tokens: List[str] = []
    for raw in raw_tokens:
        token = raw.strip()
        if not token:
            continue
        # Skip standalone punctuation
        if all(ch in ".,;:!?'\"()[]{}" for ch in token):
            continue
        tokens.append(token)
    return tokens


def normalize_message(text: str) -> NormalizedMessage:
    """Normalize a raw message for lexicon extraction.

    - Removes URLs
    - Collapses whitespace
    - Preserves case for symbols/acronyms
    """
    without_urls = _strip_urls(text)
    cleaned = _clean_whitespace(without_urls)
    tokens = _tokenize(cleaned)
    return NormalizedMessage(cleaned_text=cleaned, tokens=tokens)


