CREATE TABLE database_monitors (
    monitor_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    ip TEXT NOT NULL,
    port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    revision TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    deleted_at DOUBLE PRECISION,
    last_checked_at DOUBLE PRECISION,
    last_success_at DOUBLE PRECISION,
    failure_started_at DOUBLE PRECISION,
    failures INTEGER NOT NULL DEFAULT 0,
    successes INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    open_incident_id TEXT
);
CREATE UNIQUE INDEX idx_database_monitors_endpoint ON database_monitors(ip, port) WHERE deleted_at IS NULL;
CREATE TABLE database_monitor_incidents (
    incident_id TEXT PRIMARY KEY,
    monitor_id TEXT NOT NULL REFERENCES database_monitors(monitor_id),
    name TEXT NOT NULL,
    ip TEXT NOT NULL,
    port INTEGER NOT NULL,
    started_at DOUBLE PRECISION NOT NULL,
    confirmed_at DOUBLE PRECISION NOT NULL,
    ended_at DOUBLE PRECISION,
    end_reason TEXT,
    last_error TEXT NOT NULL
);
CREATE INDEX idx_database_monitor_incidents_time ON database_monitor_incidents(started_at DESC, incident_id);
