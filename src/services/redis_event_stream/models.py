"""Dataclasses and helpers for Redis event streaming service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
from typing import Any, Dict


@dataclass(frozen=True)
class ScheduledEvent:
    """Normalized scheduled event row stored in Postgres."""

    event_id: str
    event_name: str
    country: str
    scheduled_time_et: datetime
    source_file: str
    is_active: bool = True
    updated_at: datetime | None = None

    @staticmethod
    def build_event_id(event_name: str, scheduled_time_et: datetime, source_file: str) -> str:
        """Build deterministic event id for idempotent upsert/publish."""
        payload = f"{event_name}|{scheduled_time_et.isoformat()}|{source_file}"
        return sha1(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_db_row(cls, row: Dict[str, Any]) -> "ScheduledEvent":
        """Create object from psycopg row dict."""
        return cls(
            event_id=str(row["event_id"]),
            event_name=str(row["event_name"]),
            country=str(row["country"]),
            scheduled_time_et=row["scheduled_time_et"],
            source_file=str(row["source_file"]),
            is_active=bool(row.get("is_active", True)),
            updated_at=row.get("updated_at"),
        )


@dataclass(frozen=True)
class EventMessage:
    """Redis stream payload (all messages are pre-alert notifications)."""

    event_id: str
    event_name: str
    country: str
    scheduled_time_et: datetime
    source_file: str

    @classmethod
    def from_scheduled_event(cls, event: ScheduledEvent) -> "EventMessage":
        return cls(
            event_id=event.event_id,
            event_name=event.event_name,
            country=event.country,
            scheduled_time_et=event.scheduled_time_et,
            source_file=event.source_file,
        )

    def to_stream_fields(self) -> Dict[str, str]:
        """Convert payload to Redis stream string fields."""
        return {
            "event_id": self.event_id,
            "event_name": self.event_name,
            "country": self.country,
            "scheduled_time_et": self.scheduled_time_et.isoformat(),
            "source_file": self.source_file,
        }

