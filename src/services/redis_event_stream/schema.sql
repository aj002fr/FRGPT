-- Postgres source-of-truth table for Redis pre-alert publishing.

CREATE TABLE IF NOT EXISTS scheduled_event_alerts (
    event_id TEXT PRIMARY KEY,
    event_name TEXT NOT NULL,
    country TEXT NOT NULL,
    scheduled_time_et TIMESTAMPTZ NOT NULL,
    source_file TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scheduled_event_alerts_archive (
    event_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    country TEXT NOT NULL,
    scheduled_time_et TIMESTAMPTZ NOT NULL,
    source_file TEXT NOT NULL,
    is_active BOOLEAN NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_event_time_active
    ON scheduled_event_alerts (scheduled_time_et)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_event_updated_at
    ON scheduled_event_alerts (updated_at);

CREATE INDEX IF NOT EXISTS idx_archive_event_time
    ON scheduled_event_alerts_archive (scheduled_time_et);

CREATE OR REPLACE FUNCTION set_scheduled_event_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_scheduled_event_updated_at ON scheduled_event_alerts;
CREATE TRIGGER trg_scheduled_event_updated_at
BEFORE UPDATE ON scheduled_event_alerts
FOR EACH ROW
EXECUTE FUNCTION set_scheduled_event_updated_at();

