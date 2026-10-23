"""
Rule builder for X filtered stream rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence

TAG_PREFIX = "xmacro"
RULE_MAX_LEN = 2048
RULE_SUFFIX = " -is:retweet -is:reply"


@dataclass(frozen=True)
class Rule:
    value: str
    tag: str


def normalize_handle(handle: str) -> str:
    handle = handle.strip()
    if handle.startswith("@"):
        handle = handle[1:]
    return handle.lower()


def _escape_quotes(text: str) -> str:
    return text.replace('"', '\\"')


def normalize_keyword(keyword: str) -> str:
    keyword = " ".join(keyword.strip().split()).lower()
    if not keyword:
        return keyword
    if keyword.startswith('"') and keyword.endswith('"') and len(keyword) >= 2:
        return keyword
    if re.search(r"\s", keyword):
        return f"\"{_escape_quotes(keyword)}\""
    return keyword


def build_account_rule(handle: str) -> str:
    handle = normalize_handle(handle)
    return f"from:{handle}{RULE_SUFFIX}"


def _build_or_group(tokens: Sequence[str]) -> str:
    if not tokens:
        return ""
    if len(tokens) == 1:
        return tokens[0]
    return "(" + " OR ".join(tokens) + ")"


def _rule_len(handle_tokens: Sequence[str], keyword_tokens: Sequence[str]) -> int:
    handle_group = _build_or_group(handle_tokens)
    keyword_group = _build_or_group(keyword_tokens)
    if handle_group and keyword_group:
        value = f"{handle_group} {keyword_group}{RULE_SUFFIX}"
    elif handle_group:
        value = f"{handle_group}{RULE_SUFFIX}"
    else:
        value = f"{keyword_group}{RULE_SUFFIX}"
    return len(value)


def build_gated_rules(
    handles: Iterable[str],
    keywords: Iterable[str],
    max_len: int = RULE_MAX_LEN,
) -> List[str]:
    handles_list = [f"from:{normalize_handle(h)}" for h in handles if h.strip()]
    keyword_list = [normalize_keyword(k) for k in keywords if k.strip()]
    if not handles_list or not keyword_list:
        return []

    max_handle_len = max(len(h) for h in handles_list)
    handle_placeholder = "from:" + ("x" * (max_handle_len - len("from:")))

    # Step 1: chunk keywords so that even one handle fits
    keyword_groups: List[List[str]] = []
    current: List[str] = []
    for kw in keyword_list:
        candidate = current + [kw]
        if _rule_len([handle_placeholder], candidate) <= max_len:
            current = candidate
        else:
            if current:
                keyword_groups.append(current)
            current = [kw]
    if current:
        keyword_groups.append(current)

    # Step 2: for each keyword group, chunk handles to fit
    rules: List[str] = []
    for group_index, keyword_group in enumerate(keyword_groups):
        current_handles: List[str] = []
        for handle in handles_list:
            candidate = current_handles + [handle]
            if _rule_len(candidate, keyword_group) <= max_len:
                current_handles = candidate
            else:
                if not current_handles:
                    # Single handle + keyword group exceeds max_len, force single handle rule.
                    rules.append(
                        f"{_build_or_group([handle])} {_build_or_group(keyword_group)}{RULE_SUFFIX}"
                    )
                else:
                    rules.append(
                        f"{_build_or_group(current_handles)} {_build_or_group(keyword_group)}{RULE_SUFFIX}"
                    )
                current_handles = [handle]
        if current_handles:
            rules.append(
                f"{_build_or_group(current_handles)} {_build_or_group(keyword_group)}{RULE_SUFFIX}"
            )
    return rules


def build_rule_tag(kind: str, identifier: str) -> str:
    identifier = identifier.strip()
    return f"{TAG_PREFIX}:{kind}:{identifier}"


def build_account_rules(handles: Iterable[str]) -> List[Rule]:
    rules: List[Rule] = []
    for handle in handles:
        normalized = normalize_handle(handle)
        if not normalized:
            continue
        rules.append(
            Rule(
                value=build_account_rule(normalized),
                tag=build_rule_tag("all", normalized),
            )
        )
    return rules


def build_gated_rule_objects(
    handles: Iterable[str],
    keywords: Iterable[str],
    max_len: int = RULE_MAX_LEN,
) -> List[Rule]:
    rules: List[Rule] = []
    for idx, value in enumerate(build_gated_rules(handles, keywords, max_len=max_len), start=1):
        rules.append(Rule(value=value, tag=build_rule_tag("gated", str(idx))))
    return rules
