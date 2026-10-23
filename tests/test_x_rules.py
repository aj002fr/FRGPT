from src.core.x_rules import (
    RULE_MAX_LEN,
    build_account_rule,
    build_account_rules,
    build_gated_rules,
    build_rule_tag,
    normalize_handle,
    normalize_keyword,
)


def test_handle_normalization():
    assert normalize_handle("@FedReserve") == "fedreserve"
    assert normalize_handle("  POTUS ") == "potus"


def test_keyword_normalization_and_quotes():
    assert normalize_keyword("rate cut") == '"rate cut"'
    assert normalize_keyword('"rate cut"') == '"rate cut"'
    assert normalize_keyword("FOMC") == "fomc"


def test_build_account_rule():
    rule = build_account_rule("@Fed")
    assert rule.startswith("from:fed")
    assert "-is:retweet" in rule
    assert "-is:reply" in rule


def test_build_account_rules_tags():
    rules = build_account_rules(["Fed", "POTUS"])
    tags = [r.tag for r in rules]
    assert any(tag.startswith(build_rule_tag("all", "fed")) for tag in tags)


def test_gated_rules_chunking_under_limit():
    handles = [f"handle{i}" for i in range(80)]
    keywords = [f"keyword{i}" for i in range(80)]
    rules = build_gated_rules(handles, keywords, max_len=RULE_MAX_LEN)
    assert rules, "Should build at least one gated rule"
    assert all(len(rule) <= RULE_MAX_LEN for rule in rules)
