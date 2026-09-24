"""Postgres-watermark-driven Redis pre-alert publisher daemon."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor
import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from config import settings
from .models import EventMessage, ScheduledEvent


logger = logging.getLogger(__name__)
ET_TZ = ZoneInfo("America/New_York")


def get_pg_connection():
    return psycopg2.connect(
        host=settings.PG_HOST,
        port=settings.PG_PORT,
        database=settings.PG_DATABASE,
        user=settings.PG_USER,
        password=settings.PG_PASSWORD,
    )


class RedisEventPublisher:
    """Poll Postgres by updated_at watermark and publish pre-alerts to Redis stream."""

    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone=ET_TZ)
        self.redis = aioredis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
        )
        self.alert_offset = timedelta(minutes=10)
        self._shutdown = asyncio.Event()
        self.watermark_path = (
            settings.WORKSPACE_PATH / "redis_event_stream" / "publisher_watermark.txt"
        )

    def _load_watermark(self) -> datetime:
        if self.watermark_path.exists():
            value = self.watermark_path.read_text(encoding="utf-8").strip()
            if value:
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ET_TZ)
                return parsed
        return datetime(1970, 1, 1, tzinfo=ET_TZ)

    def _save_watermark(self, watermark: datetime) -> None:
        self.watermark_path.parent.mkdir(parents=True, exist_ok=True)
        self.watermark_path.write_text(watermark.isoformat(), encoding="utf-8")

    def _fetch_bootstrap_rows(self) -> list[ScheduledEvent]:
        table = settings.SCHEDULED_EVENT_ALERTS_TABLE
        conn = get_pg_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT event_id, event_name, country, scheduled_time_et, source_file, is_active, updated_at
                    FROM {table}
                    WHERE is_active = TRUE
                      AND scheduled_time_et > NOW()
                    ORDER BY scheduled_time_et ASC
                    """
                )
                rows = cur.fetchall()
                return [ScheduledEvent.from_db_row(row) for row in rows]
        finally:
            conn.close()

    def _fetch_deltas(self, watermark: datetime) -> list[ScheduledEvent]:
        table = settings.SCHEDULED_EVENT_ALERTS_TABLE
        conn = get_pg_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT event_id, event_name, country, scheduled_time_et, source_file, is_active, updated_at
                    FROM {table}
                    WHERE updated_at > %s
                    ORDER BY updated_at ASC, event_id ASC
                    """,
                    (watermark,),
                )
                rows = cur.fetchall()
                return [ScheduledEvent.from_db_row(row) for row in rows]
        finally:
            conn.close()

    def _schedule_event(self, event: ScheduledEvent) -> None:
        alert_time = event.scheduled_time_et - self.alert_offset
        now_et = datetime.now(ET_TZ)
        if alert_time <= now_et:
            return

        # Replace existing schedule on updates.
        self.scheduler.add_job(
            self._publish_event,
            trigger=DateTrigger(run_date=alert_time),
            id=event.event_id,
            args=[event],
            replace_existing=True,
            misfire_grace_time=120,
            coalesce=True,
        )

    def _unschedule_event(self, event_id: str) -> None:
        job = self.scheduler.get_job(event_id)
        if job:
            self.scheduler.remove_job(event_id)

    async def _publish_event(self, event: ScheduledEvent) -> None:
        dedupe_key = f"{settings.REDIS_DEDUPE_PREFIX}{event.event_id}"
        inserted = await self.redis.set(
            dedupe_key,
            "1",
            ex=settings.REDIS_DEDUPE_TTL_SECONDS,
            nx=True,
        )
        if not inserted:
            logger.info("Skipped duplicate publish for event_id=%s", event.event_id)
            return

        payload = EventMessage.from_scheduled_event(event).to_stream_fields()
        await self.redis.xadd(settings.REDIS_EVENT_STREAM, payload)
        logger.info(
            "Published pre-alert event_id=%s event_name=%s scheduled=%s",
            event.event_id,
            event.event_name,
            event.scheduled_time_et.isoformat(),
        )

    async def _poll_loop(self) -> None:
        watermark = self._load_watermark()
        logger.info("Starting DB poll loop from watermark=%s", watermark.isoformat())

        while not self._shutdown.is_set():
            try:
                deltas = self._fetch_deltas(watermark)
                max_updated = watermark

                for event in deltas:
                    if event.updated_at and event.updated_at > max_updated:
                        max_updated = event.updated_at

                    if event.is_active:
                        self._schedule_event(event)
                    else:
                        self._unschedule_event(event.event_id)

                if max_updated > watermark:
                    watermark = max_updated
                    self._save_watermark(watermark)

            except Exception:
                logger.exception("Error in publisher poll loop")

            await asyncio.sleep(settings.EVENT_PUBLISHER_POLL_SECONDS)

    async def start(self) -> None:
        self.scheduler.start()

        bootstrap = self._fetch_bootstrap_rows()
        for event in bootstrap:
            self._schedule_event(event)
        logger.info("Bootstrapped %s active events from Postgres", len(bootstrap))

        try:
            await self._poll_loop()
        finally:
            await self.stop()

    async def stop(self) -> None:
        if not self._shutdown.is_set():
            self._shutdown.set()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        await self.redis.aclose()


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    publisher = RedisEventPublisher()
    await publisher.start()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass

