ALTER TABLE database_monitor_incidents
    ADD COLUMN failure_count bigint NOT NULL DEFAULT 3 CHECK (failure_count >= 3),
    ADD COLUMN last_failed_at double precision;

WITH known_failures AS (
    SELECT m.incident_id,
        GREATEST(3, d.failures, MAX(o.occurrences)) AS failure_count,
        GREATEST(m.confirmed_at, d.last_checked_at, MAX(EXTRACT(EPOCH FROM o.last_seen_at))) AS last_failed_at
    FROM database_monitor_incidents m
    LEFT JOIN database_monitors d ON d.monitor_id=m.monitor_id AND d.open_incident_id=m.incident_id AND d.failures>0
    LEFT JOIN system_incidents o ON o.component='database-monitor' AND o.resource_id=m.incident_id
    GROUP BY m.incident_id, m.confirmed_at, d.failures, d.last_checked_at
)
UPDATE database_monitor_incidents m
SET failure_count=f.failure_count, last_failed_at=f.last_failed_at
FROM known_failures f WHERE f.incident_id=m.incident_id;

ALTER TABLE database_monitor_incidents ALTER COLUMN last_failed_at SET NOT NULL;
