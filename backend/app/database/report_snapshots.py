"""Bounded application-owned statistics and paginated report snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from app.database.postgres import PostgresRepository, lock_key


def fingerprint(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ReportKey:
    line_id: str
    report_type: str
    period_start: str
    period_end: str
    filters: dict
    source_fingerprint: str
    schema_version: int = 1
    access_scope: str = 'admin'

    @property
    def query_fingerprint(self) -> str:
        return fingerprint({'filters': self.filters, 'access_scope': self.access_scope})

    @property
    def values(self) -> tuple:
        return (self.line_id, self.report_type, self.period_start, self.period_end,
                self.query_fingerprint, self.source_fingerprint, self.schema_version)


@dataclass
class ReportSnapshot:
    key: ReportKey
    payload: dict
    data_version: str
    data_as_of: datetime
    refreshed_at: datetime
    expires_at: datetime


class ReportSnapshotRepository(PostgresRepository):

    def get(self, key: ReportKey) -> ReportSnapshot | None:
        with self.connect() as connection:
            row = connection.execute('''SELECT * FROM report_snapshots WHERE
                line_id=%s AND report_type=%s AND period_start=%s AND period_end=%s AND
                query_fingerprint=%s AND source_fingerprint=%s AND snapshot_schema_version=%s''', key.values).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row['payload'])
            if not isinstance(payload, dict) or fingerprint(payload) != row['data_version']:
                return None
            stamps = [datetime.fromisoformat(row[name]) for name in ('data_as_of', 'refreshed_at', 'expires_at')]
            if any(stamp.tzinfo is None for stamp in stamps):
                return None
            return ReportSnapshot(key, payload, row['data_version'], *stamps)
        except (ValueError, TypeError):
            return None

    def save(self, snapshot: ReportSnapshot) -> None:
        payload = json.dumps(snapshot.payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if fingerprint(snapshot.payload) != snapshot.data_version:
            raise ValueError('快照内容版本不一致')
        with self.connect() as connection:
            lock_key(connection, "report-snapshots:" + snapshot.key.line_id)
            connection.execute('''INSERT INTO report_snapshots (line_id, report_type, period_start, period_end, query_fingerprint, source_fingerprint, snapshot_schema_version, data_version, data_as_of, refreshed_at, expires_at, payload) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(line_id, report_type, period_start, period_end, query_fingerprint,
                            source_fingerprint, snapshot_schema_version) DO UPDATE SET
                data_version=excluded.data_version, data_as_of=excluded.data_as_of,
                refreshed_at=excluded.refreshed_at, expires_at=excluded.expires_at, payload=excluded.payload''',
                (*snapshot.key.values, snapshot.data_version, snapshot.data_as_of.isoformat(),
                 snapshot.refreshed_at.isoformat(), snapshot.expires_at.isoformat(), payload))
            # Cache eviction never touches existing business snapshots or production records.
            connection.execute('''DELETE FROM report_snapshots WHERE line_id=%s AND insertion_order NOT IN
                (SELECT insertion_order FROM report_snapshots WHERE line_id=%s ORDER BY refreshed_at DESC, insertion_order DESC LIMIT 128)''',
                (snapshot.key.line_id, snapshot.key.line_id))
            # Other lines may be publishing concurrently; skip their locked rows.
            connection.execute('''DELETE FROM report_snapshots WHERE insertion_order IN
                (SELECT insertion_order FROM report_snapshots WHERE expires_at<%s FOR UPDATE SKIP LOCKED)''',
                               ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),))
