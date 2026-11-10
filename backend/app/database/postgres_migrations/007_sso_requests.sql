CREATE TABLE sso_requests (
    state_hash text PRIMARY KEY,
    browser_hash text NOT NULL,
    nonce text NOT NULL,
    verifier text NOT NULL,
    return_to text NOT NULL,
    expires_at double precision NOT NULL
);
CREATE INDEX idx_sso_requests_expiry ON sso_requests(expires_at);
