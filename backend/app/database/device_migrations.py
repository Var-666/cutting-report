from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import datetime

from app.database.sqlite import SQLiteDatabase


# Only the historical single-device schema established this ownership.
LEGACY_DEVICE_ID = 'Cutter01'
DEVICE_MIGRATION = 'device-isolation-v2'
SCOPED_TABLES = {
    'report_jobs': 'device_id',
    'analysis_source_versions': 'device_id',
    'report_schedules': 'device_id',
    'scheduled_report_runs': 'device_id',
    'workshop_reports': 'scope_machine_no',
    'workshop_confirmations': 'scope_machine_no',
    'workshop_confirmation_audits': 'scope_machine_no',
}


def _backup(database: SQLiteDatabase) -> None:
    folder = database.path.parent / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{database.path.stem}-before-device-isolation-{datetime.now():%Y%m%d-%H%M%S-%f}.db'
    with database.connect() as connection, sqlite3.connect(target) as backup:
        connection.backup(backup)


def _rebuild(connection: sqlite3.Connection, table: str, transform: Callable[[str], str]) -> None:
    original = connection.execute(
        'SELECT sql FROM sqlite_master WHERE type = ? AND name = ?', ('table', table)
    ).fetchone()['sql']
    updated = transform(original)
    if updated == original:
        return
    objects = [row['sql'] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE type IN ('index','trigger') AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    )]
    temporary = f'{table}_device_upgrade'
    connection.execute(re.sub(r'(?i)(CREATE TABLE\s+)(?:IF NOT EXISTS\s+)?["\[]?' + re.escape(table) + r'["\]]?',
                              lambda match: match[1] + temporary, updated, count=1))
    columns = ','.join('"' + row['name'] + '"' for row in connection.execute(f'PRAGMA table_info({table})'))
    connection.execute(f'INSERT INTO {temporary} ({columns}) SELECT {columns} FROM {table}')
    connection.execute(f'DROP TABLE {table}')
    connection.execute(f'ALTER TABLE {temporary} RENAME TO {table}')
    for statement in objects:
        connection.execute(statement)


def rebuild_table(database: SQLiteDatabase, table: str, transform: Callable[[str], str]) -> None:
    with database.connect() as connection:
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('BEGIN IMMEDIATE')
        _rebuild(connection, table, transform)
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('设备范围迁移外键检查失败，已回滚')


def migrate_device_isolation(database: SQLiteDatabase, catalog) -> None:
    """Run under the startup initialize lock, before repositories or workers start.

    Back up the committed database first. All ownership validation, table rebuilds,
    cursor renaming and the version marker then commit or roll back together.
    """
    if not database.path.is_file():
        return
    with database.connect() as connection:
        tables = {row['name'] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'schema_migrations' in tables and connection.execute(
            'SELECT 1 FROM schema_migrations WHERE name=?', (DEVICE_MIGRATION,)
        ).fetchone():
            return
        if not tables.intersection((*SCOPED_TABLES, 'alarm_sync_state')):
            return
    _backup(database)
    try:
        with database.connect() as connection:
            connection.execute('PRAGMA foreign_keys=OFF')
            connection.execute('BEGIN IMMEDIATE')
            devices = {device.id: device.machine_no for device in catalog.devices}
            machines = {machine.casefold(): machine for machine in devices.values()}
            legacy_machine = devices.get(LEGACY_DEVICE_ID)
            # Null scopes are only attributable from the old single-device schema
            # or an explicit parent/confirmation. A current catalog default is no evidence.
            single_device_schema = all(column not in {
                row['name'] for row in connection.execute(f'PRAGMA table_info({table})')
            } for table, column in SCOPED_TABLES.items() if table != 'workshop_reports')
            missing_scope = set()
            for table, column in SCOPED_TABLES.items():
                if table not in tables:
                    continue
                columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
                if column not in columns:
                    missing_scope.add(table)
                    connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} TEXT')
                if column == 'device_id':
                    for row in connection.execute(f'SELECT rowid, {column} FROM {table}').fetchall():
                        value = row[column]
                        if not value:
                            if table not in missing_scope or legacy_machine is None:
                                raise ValueError(f'{table} rowid={row["rowid"]} 缺少设备归属')
                            value = LEGACY_DEVICE_ID
                        if value not in devices:
                            raise ValueError(f'{table} rowid={row["rowid"]} 的设备不存在：{value}')
                        connection.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?', (value, row['rowid']))

            if 'workshop_reports' in tables:
                for report in connection.execute('SELECT * FROM workshop_reports').fetchall():
                    value = report['scope_machine_no']
                    evidence = set()
                    for child in ('workshop_confirmations', 'workshop_confirmation_audits'):
                        if child in tables:
                            child_columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({child})')}
                            if 'report_id' in child_columns:
                                evidence.update(row[0] for row in connection.execute(
                                    f'SELECT DISTINCT scope_machine_no FROM {child} WHERE report_id=? AND scope_machine_no IS NOT NULL',
                                    (report['report_id'],),
                                ) if row[0])
                    if value:
                        evidence.add(value)
                    normalized = {machines.get(item.casefold(), item) for item in evidence}
                    if len(normalized) > 1:
                        raise ValueError(f'日报 {report["report_id"]} 的设备归属冲突')
                    if not normalized and single_device_schema and legacy_machine:
                        normalized.add(legacy_machine)
                    if len(normalized) != 1 or next(iter(normalized)).casefold() not in machines:
                        raise ValueError(f'日报 {report["report_id"]} 的设备归属不明确')
                    connection.execute('UPDATE workshop_reports SET scope_machine_no=? WHERE report_id=?',
                                       (next(iter(normalized)), report['report_id']))

            for table in ('workshop_confirmations', 'workshop_confirmation_audits'):
                if table not in tables:
                    continue
                columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
                for row in connection.execute(f'SELECT rowid, * FROM {table}').fetchall():
                    value = row['scope_machine_no']
                    report_id = row['report_id'] if 'report_id' in columns else None
                    parent = connection.execute('SELECT scope_machine_no FROM workshop_reports WHERE report_id=?',
                                                (report_id,)).fetchone() if report_id and 'workshop_reports' in tables else None
                    if parent and value and value.casefold() != parent[0].casefold():
                        raise ValueError(f'{table} task_id={row["task_id"]} 与日报设备归属冲突')
                    value = value or (parent[0] if parent else None)
                    if not value and 'workshop_report_items' in tables and 'workshop_reports' in tables:
                        scopes = {item[0] for item in connection.execute('''
                            SELECT DISTINCT r.scope_machine_no FROM workshop_report_items i
                            JOIN workshop_reports r ON r.report_id=i.report_id WHERE i.task_id=?
                        ''', (row['task_id'],))}
                        if len(scopes) == 1:
                            value = scopes.pop()
                    if not value or value.casefold() not in machines:
                        raise ValueError(f'{table} task_id={row["task_id"]} 的设备归属不明确')
                    connection.execute(f'UPDATE {table} SET scope_machine_no=? WHERE rowid=?',
                                       (machines[value.casefold()], row['rowid']))

            if 'scheduled_report_runs' in tables and 'report_jobs' in tables:
                conflict = connection.execute('''SELECT r.run_id FROM scheduled_report_runs r
                    JOIN report_jobs j ON j.report_id=r.report_id WHERE r.device_id<>j.device_id LIMIT 1''').fetchone()
                if conflict:
                    raise ValueError(f'自动化运行 {conflict[0]} 与报表设备归属冲突')

            if 'workshop_reports' in tables:
                connection.execute('DROP INDEX IF EXISTS idx_workshop_reports_device_date')
            for table, column in SCOPED_TABLES.items():
                if table not in tables:
                    continue
                def transform(sql, table=table, column=column):
                    sql = re.sub(rf'(?i)\b{column}\s+TEXT(?:\s+NOT NULL)?(?:\s+DEFAULT\s+[\'"]Cutter01[\'"])?',
                                 f'{column} TEXT NOT NULL', sql)
                    if table == 'workshop_reports':
                        sql = sql.replace('report_date TEXT NOT NULL UNIQUE', 'report_date TEXT NOT NULL')
                    if table == 'workshop_confirmations':
                        sql = sql.replace('task_id TEXT PRIMARY KEY', 'task_id TEXT NOT NULL')
                        if 'PRIMARY KEY(scope_machine_no, task_id)' not in sql:
                            at = sql.rfind(')')
                            sql = sql[:at] + ', PRIMARY KEY(scope_machine_no, task_id)' + sql[at:]
                    if table == 'analysis_source_versions':
                        sql = sql.replace('PRIMARY KEY(report_type, report_date)', 'PRIMARY KEY(report_type, report_date, device_id)')
                    return sql
                _rebuild(connection, table, transform)
            if 'workshop_reports' in tables:
                connection.execute('''CREATE UNIQUE INDEX idx_workshop_reports_device_date
                    ON workshop_reports(report_date, scope_machine_no COLLATE NOCASE)''')
            if 'alarm_sync_state' in tables:
                old = connection.execute("SELECT 1 FROM alarm_sync_state WHERE source_name='production'").fetchone()
                if old:
                    if legacy_machine is None:
                        raise ValueError('历史告警游标的 Cutter01 设备配置不存在')
                    target = f'production:{LEGACY_DEVICE_ID}'
                    if connection.execute('SELECT 1 FROM alarm_sync_state WHERE source_name=?', (target,)).fetchone():
                        raise ValueError('新旧告警游标同时存在，必须人工核对采集位置与租约')
                    connection.execute("UPDATE alarm_sync_state SET source_name=? WHERE source_name='production'", (target,))
            if connection.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('外键检查失败')
            connection.execute('CREATE TABLE IF NOT EXISTS schema_migrations(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
            connection.execute('INSERT INTO schema_migrations VALUES (?, ?)', (DEVICE_MIGRATION, datetime.now().isoformat()))
    except (ValueError, sqlite3.DatabaseError) as exc:
        raise ValueError(f'设备隔离迁移失败，已回滚；原库备份位于 {database.path.parent / "backups"}：{exc}') from exc
