from __future__ import annotations

import argparse
import json
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List

from telethon import TelegramClient
from telethon.tl.types import PeerChannel, PeerChat, PeerUser


def to_epoch(dt) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


async def fetch_messages_for_user(
    client: TelegramClient,
    chat: str,
    user: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """
    Fetch messages from `chat` (all senders).
    The `user` argument is currently ignored and kept only for CLI compatibility.

    Returns a list of dicts shaped like TelegramMessage for our lexicon:
      {chat_id, message_id, date, sender_id, sender_username, text}
    """
    # Resolve chat entity (supports usernames like @mygroup or numeric IDs)
    if isinstance(chat, str) and chat.lstrip("-").isdigit():
        # Numeric ID: try channel/group resolution first
        chat_id_int = int(chat)
        try:
            chat_entity = await client.get_entity(PeerChannel(chat_id_int))
        except Exception:
            chat_entity = await client.get_entity(PeerChat(chat_id_int))
    else:
        chat_entity = await client.get_entity(chat)

    results: List[Dict[str, Any]] = []

    async for msg in client.iter_messages(chat_entity, limit=limit):
        if not msg.message:
            continue

        # Use Telethon's computed chat_id (works for users, groups, channels)
        chat_id = msg.chat_id

        results.append(
            {
                "chat_id": int(chat_id),
                "message_id": int(msg.id),
                "date": to_epoch(msg.date),
                "sender_id": int(msg.sender_id) if msg.sender_id is not None else None,
                "sender_username": getattr(msg.sender, "username", None),
                "text": msg.message,
            }
        )

    return results


async def main_async(args: argparse.Namespace) -> None:
    client = TelegramClient(args.session, args.api_id, args.api_hash)
    await client.start()  # Will prompt for phone + code on first run

    messages = await fetch_messages_for_user(
        client=client,
        chat=args.chat,
        user=args.user,
        limit=args.limit,
    )

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(messages)} messages to {output_path}")

    await client.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Telegram messages for a specific user in a chat."
    )
    parser.add_argument("--api-id", type=int, required=True, help="Telegram API ID")
    parser.add_argument("--api-hash", type=str, required=True, help="Telegram API hash")
    parser.add_argument(
        "--session",
        type=str,
        default="user_session",
        help="Session file name (for Telethon).",
    )
    parser.add_argument(
        "--chat",
        type=str,
        required=True,
        help="Chat identifier (username like @mygroup or numeric ID).",
    )
    parser.add_argument(
        "--user",
        type=str,
        required=True,
        help="User to filter by (username like @trader or numeric user ID).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Max number of messages to scan.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="telegram_user_messages.json",
        help="Output JSON path.",
    )
    return parser.parse_args()


def main() -> None:
    import asyncio

    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()