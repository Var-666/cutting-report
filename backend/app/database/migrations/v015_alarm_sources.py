"""Isolate external alarm identifiers while retaining incident/report identities."""
from hashlib import sha256
import sqlite3

from app.database.sqlite import SQLiteDatabase


def scoped_fingerprint(source_name: str, value: str) -> str:
    return sha256(f'{source_name}|{value}'.encode()).hexdigest()


def upgrade(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute('PRAGMA table_info(alarm_source_events)')}
    if 'source_name' in columns:
        return
    connection.execute('ALTER TABLE alarm_source_events RENAME TO alarm_source_events_v014')
    connection.execute('''CREATE TABLE alarm_source_events (
        source_name TEXT NOT NULL DEFAULT '', source_alarm_id INTEGER NOT NULL,
        incident_id TEXT NOT NULL, source_hash TEXT NOT NULL, start_time TEXT,
        remove_time TEXT, is_open INTEGER NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
        PRIMARY KEY (source_name, source_alarm_id),
        FOREIGN KEY(incident_id) REFERENCES alarm_incidents(incident_id))''')
    connection.execute('''INSERT INTO alarm_source_events
        (source_alarm_id, incident_id, source_hash, start_time, remove_time, is_open, first_seen_at, last_seen_at)
        SELECT source_alarm_id, incident_id, source_hash, start_time, remove_time, is_open, first_seen_at, last_seen_at
        FROM alarm_source_events_v014''')
    connection.execute('DROP TABLE alarm_source_events_v014')
    connection.execute('CREATE INDEX idx_alarm_source_events_open ON alarm_source_events(source_name, is_open, source_alarm_id)')
    connection.execute('CREATE INDEX idx_alarm_source_events_incident ON alarm_source_events(incident_id, is_open)')


def bind_legacy_sources(database: SQLiteDatabase, bindings: dict[str, tuple[str, str]], *, analysis_sources: dict[str, str] | None = None) -> None:
    """Finish v015's config-dependent backfill once, under the shared startup lock."""
    with database.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        for legacy_name, (machine_no, source_name) in bindings.items():
            incidents = connection.execute('''SELECT DISTINCT i.incident_id, i.fingerprint
                FROM alarm_incidents i JOIN alarm_source_events e ON e.incident_id=i.incident_id
                WHERE e.source_name='' AND i.machine_no=? COLLATE NOCASE''', (machine_no,)).fetchall()
            for incident in incidents:
                connection.execute('UPDATE alarm_source_events SET source_name=? WHERE incident_id=? AND source_name=\'\'',
                                   (source_name, incident['incident_id']))
                connection.execute('UPDATE alarm_incidents SET fingerprint=? WHERE incident_id=?',
                                   (scoped_fingerprint(source_name, incident['fingerprint']), incident['incident_id']))
            old = connection.execute('SELECT * FROM alarm_sync_state WHERE source_name=?', (legacy_name,)).fetchone()
            if old:
                if connection.execute('SELECT 1 FROM alarm_sync_state WHERE source_name=?', (source_name,)).fetchone():
                    raise ValueError('新旧告警游标冲突，请核对后迁移')
                connection.execute('UPDATE alarm_sync_state SET source_name=? WHERE source_name=?', (source_name, legacy_name))
        if connection.execute("SELECT 1 FROM alarm_source_events WHERE source_name='' LIMIT 1").fetchone():
            raise ValueError('历史告警设备缺少明确的产线绑定，拒绝猜测归属')
        for line_id, scope in (analysis_sources or {}).items():
            connection.execute('UPDATE report_analyses SET display_scope=? WHERE display_scope=?', (scope, line_id))
            connection.execute("""INSERT INTO analysis_source_versions (report_type, report_date, data_version, updated_at, device_id)
                SELECT report_type, report_date, data_version, updated_at, ? FROM analysis_source_versions WHERE device_id=?
                ON CONFLICT(report_type, report_date, device_id) DO NOTHING""", (scope, line_id))
            connection.execute('DELETE FROM analysis_source_versions WHERE device_id=?', (line_id,))
