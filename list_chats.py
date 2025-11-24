from __future__ import annotations

import argparse

from telethon import TelegramClient


async def main_async(args: argparse.Namespace) -> None:
    client = TelegramClient(args.session, args.api_id, args.api_hash)
    await client.start()

    print("Listing dialogs (chats, groups, channels):")
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        chat_id = getattr(entity, "id", dialog.id)
        username = getattr(entity, "username", None)
        print(f"id={chat_id}  title={dialog.name!r}  username={username!r}")

    await client.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List Telegram chats for the logged-in user (IDs, titles, usernames)."
    )
    parser.add_argument("--api-id", type=int, required=True, help="Telegram API ID")
    parser.add_argument("--api-hash", type=str, required=True, help="Telegram API hash")
    parser.add_argument(
        "--session",
        type=str,
        default="user_session",
        help="Session file name (must match telegram_user_extractor.py).",
    )
    return parser.parse_args()


def main() -> None:
    import asyncio

    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()


# py telegram_user_extractor.py `
#   --api-id 38438173 `
#   --api-hash fc6eb9105f23ed32da181bb1390bf15b `
#   --chat 4992639871 `
#   --user "@anything" `
#   --limit 5000 `
#   --output user_messages.json



# id=4053394970  title='Fibonacci Research'  username=None