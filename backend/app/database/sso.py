"""Browser-bound, expiring, single-use OIDC login requests."""
from __future__ import annotations

from hashlib import sha256

from app.database.postgres import PostgresRepository, lock_key


class SsoRequestRepository(PostgresRepository):
    @staticmethod
    def _hash(value: str) -> str:
        return sha256(value.encode()).hexdigest()

    def create(self, state: str, browser: str, *, nonce: str, verifier: str, return_to: str) -> None:
        with self.connect() as connection:
            lock_key(connection, 'sso:requests')
            connection.execute('DELETE FROM sso_requests WHERE expires_at <= EXTRACT(EPOCH FROM clock_timestamp())')
            if connection.execute('SELECT count(*) AS n FROM sso_requests').fetchone()['n'] >= 1000:
                raise ValueError('统一登录请求过多，请稍后重试')
            connection.execute('''INSERT INTO sso_requests VALUES (%s,%s,%s,%s,%s,
                EXTRACT(EPOCH FROM clock_timestamp()) + 300)''',
                (self._hash(state), self._hash(browser), nonce, verifier, return_to))

    def consume(self, state: str, browser: str) -> dict | None:
        with self.connect() as connection:
            return connection.execute('''DELETE FROM sso_requests
                WHERE state_hash=%s AND browser_hash=%s AND expires_at>EXTRACT(EPOCH FROM clock_timestamp())
                RETURNING nonce,verifier,return_to''', (self._hash(state), self._hash(browser))).fetchone()
