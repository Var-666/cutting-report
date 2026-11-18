CREATE INDEX idx_system_incidents_business_observation
    ON system_incidents (component, resource_id, error_code, last_seen_at DESC)
    WHERE resource_id IS NOT NULL;
