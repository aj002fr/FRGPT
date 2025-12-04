"""
Utility script to generate noisy variants of planner queries for GEPA prompt optimisation.

Reads a plain text file where each non-empty line starts with an integer index followed by a dot,
then the query text, e.g.:

    1. Show me how 2y, 5y, 10y, and 30y U.S. Treasury yields moved intraday today...

It writes a new file with one or more noisy variants per query to increase test-space coverage.
Only Python stdlib is used.
"""

from __future__ import annotations

import argparse
import random
import re
from pathlib import Path
from typing import Callable, Iterable, List, Tuple


LINE_RE = re.compile(r"^\s*(\d+)\.\s*(.*\S)\s*$")


def parse_queries(lines: Iterable[str]) -> List[Tuple[int, str]]:
    """Parse numbered queries from text lines."""
    queries: List[Tuple[int, str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        m = LINE_RE.match(stripped)
        if not m:
            # Fallback: treat whole line as text with a synthetic index
            idx = len(queries) + 1
            queries.append((idx, stripped))
        else:
            idx = int(m.group(1))
            text = m.group(2).strip()
            queries.append((idx, text))
    return queries


def _random_typo(text: str, rng: random.Random) -> str:
    if len(text) < 5:
        return text
    idx = rng.randrange(0, len(text))
    c = text[idx]
    op = rng.choice(["drop", "dup", "swap"])
    if op == "drop":
        return text[:idx] + text[idx + 1 :]
    if op == "dup":
        return text[:idx] + c + text[idx:]
    if op == "swap" and idx + 1 < len(text):
        return text[:idx] + text[idx + 1] + c + text[idx + 2 :]
    return text


def _random_case(text: str, rng: random.Random) -> str:
    mode = rng.choice(["lower", "upper", "sentence", "mixed"])
    if mode == "lower":
        return text.lower()
    if mode == "upper":
        return text.upper()
    if mode == "sentence":
        return text[:1].upper() + text[1:].lower()
    # mixed: randomly upper-case some words
    words = text.split()
    for i, w in enumerate(words):
        if rng.random() < 0.25:
            words[i] = w.upper()
    return " ".join(words)


def _insert_filler(text: str, rng: random.Random) -> str:
    fillers = [
        "pls",
        "plz",
        "tbh",
        "imo",
        "btw",
        "lol",
        "idk",
        "thx",
    ]
    words = text.split()
    if not words:
        return text
    pos = rng.randrange(0, len(words) + 1)
    filler = rng.choice(fillers)
    words.insert(pos, filler)
    return " ".join(words)


def _noisy_punctuation(text: str, rng: random.Random) -> str:
    # Add or duplicate punctuation at the end
    endings = ["?", "??", "???", "!", "!!", "...", "?!"]
    if rng.random() < 0.5:
        # strip existing trailing punctuation
        text = text.rstrip(" ?!.")
    return text + " " + rng.choice(endings)


def _extra_whitespace(text: str, rng: random.Random) -> str:
    # Random multiple spaces between some words
    parts = text.split(" ")
    for i in range(len(parts) - 1):
        if rng.random() < 0.2:
            parts[i] = parts[i] + " " * rng.randint(1, 3)
    return " ".join(parts)


def _truncate(text: str, rng: random.Random) -> str:
    words = text.split()
    if len(words) <= 4:
        return text
    cut = rng.randrange(3, len(words))
    return " ".join(words[:cut])


NOISE_FUNCS: List[Callable[[str, random.Random], str]] = [
    _random_typo,
    _random_case,
    _insert_filler,
    _noisy_punctuation,
    _extra_whitespace,
    _truncate,
]


def make_noisy_variant(text: str, rng: random.Random, max_ops: int = 3) -> str:
    """Apply 1–max_ops random noise operations to a query."""
    ops = rng.randint(1, max_ops)
    funcs = rng.sample(NOISE_FUNCS, k=min(ops, len(NOISE_FUNCS)))
    noisy = text
    for func in funcs:
        noisy = func(noisy, rng)
    return noisy


def generate_noisy_queries(
    queries: List[Tuple[int, str]],
    variants: int,
    rng: random.Random,
) -> List[str]:
    """
    Generate noisy variants.

    Output format:
        original_index.variant_index. noisy_query
    e.g.:
        1. Show me...
        1a. show me   how 2y 5y 10y us tsy yields moved intraday today ??
    """
    results: List[str] = []

    # include originals first
    for idx, text in queries:
        results.append(f"{idx}. {text}")

    suffixes = "abcdefghijklmnopqrstuvwxyz"
    for idx, text in queries:
        for v in range(variants):
            if v >= len(suffixes):
                suffix = f"x{v}"
            else:
                suffix = suffixes[v]
            noisy = make_noisy_variant(text, rng=rng)
            results.append(f"{idx}{suffix}. {noisy}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate noisy variants of planner queries for GEPA prompt testing.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("workspace") / "planner_queries.txt",
        help="Path to input queries text file (default: workspace/planner_queries.txt)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("workspace") / "planner_queries_noisy.txt",
        help="Path to output noisy queries file (default: workspace/planner_queries_noisy.txt)",
    )
    parser.add_argument(
        "--variants",
        type=int,
        default=2,
        help="Number of noisy variants to generate per query (default: 2)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)

    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")

    lines = args.input.read_text(encoding="utf-8").splitlines()
    queries = parse_queries(lines)
    noisy_lines = generate_noisy_queries(queries, variants=args.variants, rng=rng)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(noisy_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()


