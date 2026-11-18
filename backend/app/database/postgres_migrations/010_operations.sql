CREATE TABLE system_incidents (
    incident_id text PRIMARY KEY,
    fingerprint text NOT NULL,
    error_code text NOT NULL,
    level text NOT NULL CHECK (level IN ('WARNING', 'ERROR', 'CRITICAL')),
    component text NOT NULL,
    role text NOT NULL CHECK (role IN ('web', 'scheduler', 'worker', 'automation')),
    instance_id text,
    line_id text,
    source_id text,
    resource_id text,
    message text NOT NULL,
    context jsonb NOT NULL,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    occurrences bigint NOT NULL CHECK (occurrences > 0),
    status text GENERATED ALWAYS AS (
        CASE WHEN recovered_at IS NOT NULL THEN 'recovered'
             WHEN acknowledged_at IS NOT NULL THEN 'acknowledged'
             ELSE 'open' END
    ) STORED,
    acknowledged_at timestamptz,
    acknowledged_by text,
    note text,
    recovered_at timestamptz,
    recovery_evidence text
);

CREATE UNIQUE INDEX uq_system_incidents_active ON system_incidents(fingerprint) WHERE recovered_at IS NULL;
CREATE INDEX idx_system_incidents_recent ON system_incidents(last_seen_at DESC, incident_id DESC);
CREATE INDEX idx_system_incidents_state ON system_incidents(status, last_seen_at DESC, incident_id DESC);
CREATE INDEX idx_system_incidents_level ON system_incidents(level, last_seen_at DESC, incident_id DESC);
CREATE INDEX idx_system_incidents_retention ON system_incidents(recovered_at) WHERE recovered_at IS NOT NULL;

CREATE TABLE operations_samples (
    sample_key text PRIMARY KEY,
    healthy boolean NOT NULL,
    bucket bigint NOT NULL,
    streak integer NOT NULL
);
