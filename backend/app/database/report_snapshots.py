"""Bounded application-owned statistics and paginated report snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from app.database.sqlite import SQLiteRepository


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


class ReportSnapshotRepository(SQLiteRepository):
    def initialize(self) -> None:
        self.database.initialize()
        with self.connect() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS report_snapshots (
                line_id TEXT NOT NULL, report_type TEXT NOT NULL,
                period_start TEXT NOT NULL, period_end TEXT NOT NULL,
                query_fingerprint TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
                snapshot_schema_version INTEGER NOT NULL, data_version TEXT NOT NULL,
                data_as_of TEXT NOT NULL, refreshed_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (line_id, report_type, period_start, period_end,
                             query_fingerprint, source_fingerprint, snapshot_schema_version))''')

    def get(self, key: ReportKey) -> ReportSnapshot | None:
        with self.connect() as connection:
            row = connection.execute('''SELECT * FROM report_snapshots WHERE
                line_id=? AND report_type=? AND period_start=? AND period_end=? AND
                query_fingerprint=? AND source_fingerprint=? AND snapshot_schema_version=?''', key.values).fetchone()
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
            self.begin_immediate(connection)
            connection.execute('''INSERT INTO report_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(line_id, report_type, period_start, period_end, query_fingerprint,
                            source_fingerprint, snapshot_schema_version) DO UPDATE SET
                data_version=excluded.data_version, data_as_of=excluded.data_as_of,
                refreshed_at=excluded.refreshed_at, expires_at=excluded.expires_at, payload=excluded.payload''',
                (*snapshot.key.values, snapshot.data_version, snapshot.data_as_of.isoformat(),
                 snapshot.refreshed_at.isoformat(), snapshot.expires_at.isoformat(), payload))
            # Cache eviction never touches existing business snapshots or production records.
            connection.execute('''DELETE FROM report_snapshots WHERE line_id=? AND rowid NOT IN
                (SELECT rowid FROM report_snapshots WHERE line_id=? ORDER BY refreshed_at DESC, rowid DESC LIMIT 128)''',
                (snapshot.key.line_id, snapshot.key.line_id))
            connection.execute('DELETE FROM report_snapshots WHERE expires_at<?',
                               ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),))
