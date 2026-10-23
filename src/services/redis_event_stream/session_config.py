"""DST-aware session event generation for Tokyo/London/US bond sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from config import settings
from .models import ScheduledEvent


ET_TZ = ZoneInfo("America/New_York")
SESSION_SOURCE_FILE = "__session_config__"


@dataclass(frozen=True)
class SessionDefinition:
    """Local-market definition for open/close session markers."""

    open_event_name: str
    close_event_name: str
    country: str
    timezone: str
    open_time: str
    close_time: str


SESSION_DEFINITIONS = (
    SessionDefinition(
        open_event_name=settings.TOKYO_OPEN_EVENT_NAME,
        close_event_name=settings.TOKYO_CLOSE_EVENT_NAME,
        country="JP",
        timezone=settings.TOKYO_LOCAL_TZ,
        open_time=settings.TOKYO_OPEN_LOCAL_TIME,
        close_time=settings.TOKYO_CLOSE_LOCAL_TIME,
    ),
    SessionDefinition(
        open_event_name=settings.LONDON_OPEN_EVENT_NAME,
        close_event_name=settings.LONDON_CLOSE_EVENT_NAME,
        country="GB",
        timezone=settings.LONDON_LOCAL_TZ,
        open_time=settings.LONDON_OPEN_LOCAL_TIME,
        close_time=settings.LONDON_CLOSE_LOCAL_TIME,
    ),
    SessionDefinition(
        open_event_name=settings.US_OPEN_EVENT_NAME,
        close_event_name=settings.US_CLOSE_EVENT_NAME,
        country="US",
        timezone=settings.US_LOCAL_TZ,
        open_time=settings.US_OPEN_LOCAL_TIME,
        close_time=settings.US_CLOSE_LOCAL_TIME,
    ),
)


def _parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _build_event(
    event_name: str,
    country: str,
    local_date: date,
    local_clock: time,
    local_tz: ZoneInfo,
) -> ScheduledEvent:
    local_dt = datetime.combine(local_date, local_clock, tzinfo=local_tz)
    event_time_et = local_dt.astimezone(ET_TZ)
    event_id = ScheduledEvent.build_event_id(event_name, event_time_et, SESSION_SOURCE_FILE)
    return ScheduledEvent(
        event_id=event_id,
        event_name=event_name,
        country=country,
        scheduled_time_et=event_time_et,
        source_file=SESSION_SOURCE_FILE,
        is_active=True,
    )


def build_session_events(
    start_date_local: date,
    end_date_local: date,
    holiday_dates_by_country: dict[str, set[date]] | None = None,
) -> list[ScheduledEvent]:
    """
    Build daily session open/close events in ET for all configured sessions.

    Args:
        start_date_local: First local-market date to generate.
        end_date_local: Last local-market date to generate (inclusive).
        holiday_dates_by_country: Optional map country -> set(local market dates).
    """
    if end_date_local < start_date_local:
        return []

    holidays = holiday_dates_by_country or {}
    events: list[ScheduledEvent] = []

    for definition in SESSION_DEFINITIONS:
        local_tz = ZoneInfo(definition.timezone)
        open_clock = _parse_hhmm(definition.open_time)
        close_clock = _parse_hhmm(definition.close_time)
        blocked_dates = holidays.get(definition.country, set())

        current = start_date_local
        while current <= end_date_local:
            # Weekday-only and holiday-aware in local market calendar.
            if current.weekday() < 5 and current not in blocked_dates:
                events.append(
                    _build_event(
                        event_name=definition.open_event_name,
                        country=definition.country,
                        local_date=current,
                        local_clock=open_clock,
                        local_tz=local_tz,
                    )
                )
                events.append(
                    _build_event(
                        event_name=definition.close_event_name,
                        country=definition.country,
                        local_date=current,
                        local_clock=close_clock,
                        local_tz=local_tz,
                    )
                )
            current += timedelta(days=1)

    return events

