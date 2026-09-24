"""Excel -> Postgres ingestion for scheduled event pre-alert service."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

from config import settings
from .excel_parser import ParseMetrics, parse_excel_calendar
from .models import ScheduledEvent
from .session_config import build_session_events


logger = logging.getLogger(__name__)
ET_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class IngestResult:
    upserted: int
    deactivated: int
    archived: int
    deleted: int
    row_count: int
    parse_metrics: ParseMetrics


def get_pg_connection():
    return psycopg2.connect(
        host=settings.PG_HOST,
        port=settings.PG_PORT,
        database=settings.PG_DATABASE,
        user=settings.PG_USER,
        password=settings.PG_PASSWORD,
    )


def ensure_schema(conn) -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    ddl = schema_path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()


def _event_rows(events: Iterable[ScheduledEvent]) -> list[tuple]:
    return [
        (
            e.event_id,
            e.event_name,
            e.country,
            e.scheduled_time_et,
            e.source_file,
            e.is_active,
        )
        for e in events
    ]


def _upsert_events(conn, events: list[ScheduledEvent]) -> int:
    if not events:
        return 0
    rows = _event_rows(events)
    table = settings.SCHEDULED_EVENT_ALERTS_TABLE
    sql = f"""
        INSERT INTO {table} (
            event_id, event_name, country, scheduled_time_et, source_file, is_active
        ) VALUES %s
        ON CONFLICT (event_id) DO UPDATE SET
            event_name = EXCLUDED.event_name,
            country = EXCLUDED.country,
            scheduled_time_et = EXCLUDED.scheduled_time_et,
            source_file = EXCLUDED.source_file,
            is_active = EXCLUDED.is_active,
            updated_at = NOW()
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
    return len(rows)


def _soft_deactivate_missing(conn, source_file: str, active_event_ids: set[str]) -> int:
    table = settings.SCHEDULED_EVENT_ALERTS_TABLE
    with conn.cursor() as cur:
        if active_event_ids:
            cur.execute(
                f"""
                UPDATE {table}
                SET is_active = FALSE, updated_at = NOW()
                WHERE source_file = %s
                  AND is_active = TRUE
                  AND NOT (event_id = ANY(%s))
                """,
                (source_file, list(active_event_ids)),
            )
        else:
            cur.execute(
                f"""
                UPDATE {table}
                SET is_active = FALSE, updated_at = NOW()
                WHERE source_file = %s
                  AND is_active = TRUE
                """,
                (source_file,),
            )
        return cur.rowcount


def _archive_and_purge_old(conn) -> tuple[int, int]:
    """Retention control for table growth: archive then delete stale rows."""
    hot_table = settings.SCHEDULED_EVENT_ALERTS_TABLE
    archive_table = settings.SCHEDULED_EVENT_ALERTS_ARCHIVE_TABLE
    retention_days = settings.EVENT_RETENTION_DAYS

    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {archive_table} (
                event_id, event_name, country, scheduled_time_et, source_file, is_active, updated_at
            )
            SELECT event_id, event_name, country, scheduled_time_et, source_file, is_active, updated_at
            FROM {hot_table}
            WHERE scheduled_time_et < NOW() - (%s || ' days')::interval
            """,
            (retention_days,),
        )
        archived = cur.rowcount

        cur.execute(
            f"""
            DELETE FROM {hot_table}
            WHERE scheduled_time_et < NOW() - (%s || ' days')::interval
            """,
            (retention_days,),
        )
        deleted = cur.rowcount
    return archived, deleted


def _row_count_guardrail(conn) -> int:
    table = settings.SCHEDULED_EVENT_ALERTS_TABLE
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = int(cur.fetchone()[0])

    if count >= settings.EVENT_ROWCOUNT_CRITICAL_THRESHOLD:
        logger.error(
            "scheduled_event_alerts row count critical: %s >= %s",
            count,
            settings.EVENT_ROWCOUNT_CRITICAL_THRESHOLD,
        )
    elif count >= settings.EVENT_ROWCOUNT_WARN_THRESHOLD:
        logger.warning(
            "scheduled_event_alerts row count warning: %s >= %s",
            count,
            settings.EVENT_ROWCOUNT_WARN_THRESHOLD,
        )
    return count


def _build_session_rows(
    parsed_events: list[ScheduledEvent],
    holiday_dates_by_country: dict[str, set[date]],
) -> list[ScheduledEvent]:
    if parsed_events:
        start = min(e.scheduled_time_et.date() for e in parsed_events) - timedelta(days=3)
        end = max(e.scheduled_time_et.date() for e in parsed_events) + timedelta(days=3)
    else:
        today = datetime.now(ET_TZ).date()
        start = today - timedelta(days=7)
        end = today + timedelta(days=90)
    return build_session_events(
        start_date_local=start,
        end_date_local=end,
        holiday_dates_by_country=holiday_dates_by_country,
    )


def ingest_excel_to_postgres(excel_path: str | None = None) -> IngestResult:
    """
    Parse upstream Excel + generated session rows and sync to Postgres.

    Soft-delete semantics:
      - Rows from the current source_file/session-source missing in latest ingest are set inactive.
    """
    path = excel_path or settings.resolve_event_upstream_excel_path()
    parsed_events, holiday_map, metrics = parse_excel_calendar(path)
    session_events = _build_session_rows(parsed_events, holiday_map)

    # Merge and de-duplicate by event_id (calendar wins if collision).
    merged_by_id: dict[str, ScheduledEvent] = {e.event_id: e for e in session_events}
    merged_by_id.update({e.event_id: e for e in parsed_events})
    merged_events = list(merged_by_id.values())

    source_file = Path(path).name
    source_event_ids = {e.event_id for e in parsed_events if e.source_file == source_file}
    session_event_ids = {e.event_id for e in session_events}

    conn = get_pg_connection()
    try:
        ensure_schema(conn)
        upserted = _upsert_events(conn, merged_events)
        deactivated_excel = _soft_deactivate_missing(conn, source_file=source_file, active_event_ids=source_event_ids)
        deactivated_sessions = _soft_deactivate_missing(
            conn, source_file="__session_config__", active_event_ids=session_event_ids
        )
        archived, deleted = _archive_and_purge_old(conn)
        row_count = _row_count_guardrail(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    deactivated = deactivated_excel + deactivated_sessions
    logger.info(
        "Ingest complete upserted=%s deactivated=%s archived=%s deleted=%s row_count=%s",
        upserted,
        deactivated,
        archived,
        deleted,
        row_count,
    )
    return IngestResult(
        upserted=upserted,
        deactivated=deactivated,
        archived=archived,
        deleted=deleted,
        row_count=row_count,
        parse_metrics=metrics,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = ingest_excel_to_postgres()
    logger.info("Ingest result: %s", result)

