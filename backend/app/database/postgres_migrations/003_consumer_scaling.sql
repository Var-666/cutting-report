-- Explicit maintenance only: never upgrade across a live old or new Consumer.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM process_leases
        WHERE (name='worker' OR name LIKE 'worker:%')
          AND expires_at>EXTRACT(EPOCH FROM clock_timestamp())) THEN
        RAISE EXCEPTION 'Stop all Consumers and wait for lease expiry before upgrading';
    END IF;
END;
$$;

CREATE INDEX idx_process_lease_owner ON process_leases(owner);
ALTER TABLE background_tasks ADD COLUMN claim_token TEXT COLLATE "C";
