"""Build a trading lexicon JSON file from exported Telegram messages.

Messages are expected to be provided as a JSON list (e.g. user_messages.json)
with keys: chat_id, message_id, date (epoch seconds), sender_id,
sender_username, text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from config.settings import TRADING_LEXICON_PATH
from src.core.trading_lexicon import build_lexicon_from_messages, export_lexicon_to_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build trading lexicon from exported Telegram messages.")
    parser.add_argument(
        "--input-json",
        type=str,
        default="user_messages.json",
        help="Path to JSON file with exported Telegram messages.",
    )
    parser.add_argument(
        "--min-frequency",
        type=int,
        default=2,
        help="Minimum frequency for a term to be included in the lexicon.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_path = Path(args.input_json)
    if not input_path.exists():
        raise FileNotFoundError(f"Input messages JSON not found: {input_path}")

    messages = json.loads(input_path.read_text(encoding="utf-8"))
    lexicon = build_lexicon_from_messages(messages, min_frequency=args.min_frequency)
    export_lexicon_to_json(lexicon)

    ambiguous_count = sum(1 for e in lexicon if e.is_ambiguous)
    print(f"Wrote {len(lexicon)} lexicon entries to {TRADING_LEXICON_PATH}")
    print(f"Ambiguous terms (meaning-shift candidates): {ambiguous_count}")


if __name__ == "__main__":
    main()

