"""Redis stream subscriber for market pre-alerts."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis

from config import settings
from .models import EventMessage


logger = logging.getLogger(__name__)
ET_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class StreamEnvelope:
    stream_id: str
    message: EventMessage


class EventSubscriber:
    """Consumer-group based stream subscriber."""

    def __init__(
        self,
        group: str,
        consumer: str,
        stream: str = settings.REDIS_EVENT_STREAM,
    ) -> None:
        self.group = group
        self.consumer = consumer
        self.stream = stream
        self.redis = aioredis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
        )

    async def _ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(
                name=self.stream,
                groupname=self.group,
                id="0-0",
                mkstream=True,
            )
            logger.info("Created group=%s stream=%s", self.group, self.stream)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    @staticmethod
    def _parse_message(fields: dict[str, str]) -> EventMessage:
        scheduled = datetime.fromisoformat(fields["scheduled_time_et"])
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=ET_TZ)
        return EventMessage(
            event_id=fields["event_id"],
            event_name=fields["event_name"],
            country=fields.get("country", "UNK"),
            scheduled_time_et=scheduled,
            source_file=fields.get("source_file", ""),
        )

    async def listen(self):
        """Yield stream envelopes and auto-ack messages."""
        await self._ensure_group()
        while True:
            data = await self.redis.xreadgroup(
                groupname=self.group,
                consumername=self.consumer,
                streams={self.stream: ">"},
                count=50,
                block=5000,
            )
            if not data:
                continue

            for stream_name, messages in data:
                _ = stream_name
                for message_id, fields in messages:
                    message = self._parse_message(fields)
                    await self.redis.xack(self.stream, self.group, message_id)
                    yield StreamEnvelope(stream_id=message_id, message=message)

    async def close(self) -> None:
        await self.redis.aclose()


async def _run_cli(args) -> None:
    subscriber = EventSubscriber(
        group=args.group,
        consumer=args.consumer,
        stream=args.stream,
    )
    try:
        async for envelope in subscriber.listen():
            msg = envelope.message
            print(
                f"[PRE-ALERT] {msg.event_name} scheduled={msg.scheduled_time_et.isoformat()} "
                f"country={msg.country} id={msg.event_id}"
            )
    finally:
        await subscriber.close()


def main() -> None:
    default_identity = (
        os.getenv("USERNAME")
        or os.getenv("USER")
        or socket.gethostname()
        or "default"
    )
    default_group = f"alerts:{default_identity}".replace(" ", "_").lower()
    default_consumer = f"{socket.gethostname()}-{os.getpid()}".replace(" ", "_").lower()

    parser = argparse.ArgumentParser(description="Redis pre-alert stream subscriber")
    parser.add_argument(
        "--group",
        default=default_group,
        help=f"Consumer group name (default: {default_group})",
    )
    parser.add_argument(
        "--consumer",
        default=default_consumer,
        help=f"Consumer name (default: {default_consumer})",
    )
    parser.add_argument("--stream", default=settings.REDIS_EVENT_STREAM, help="Redis stream key")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    logger.info(
        "Starting subscriber stream=%s group=%s consumer=%s",
        args.stream,
        args.group,
        args.consumer,
    )
    asyncio.run(_run_cli(args))


if __name__ == "__main__":
    main()

