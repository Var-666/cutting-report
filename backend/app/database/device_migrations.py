from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import datetime

from app.database.sqlite import SQLiteDatabase


def backup_before_device_upgrade(database: SQLiteDatabase) -> None:
    """Back up an existing pre-device database before startup changes its schema."""
    if not database.path.is_file():
        return
    with database.connect() as connection:
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(report_jobs)')}
        if not columns or 'device_id' in columns:
            return
        folder = database.path.parent / 'backups'
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f'{database.path.stem}-before-devices-{datetime.now():%Y%m%d-%H%M%S-%f}.db'
        with sqlite3.connect(target) as backup:
            connection.backup(backup)


def rebuild_table(database: SQLiteDatabase, table: str, transform: Callable[[str], str]) -> None:
    """Rebuild one parent table without changing children or their foreign keys.

    Run during initialization, before workers start. The schema and data changes
    share a transaction; foreign-key validation must pass before commit.
    """
    with database.connect() as connection:
        original = connection.execute(
            'SELECT sql FROM sqlite_master WHERE type = ? AND name = ?', ('table', table)
        ).fetchone()['sql']
        updated = transform(original)
        if updated == original:
            return
        indexes = [row['sql'] for row in connection.execute(
            'SELECT sql FROM sqlite_master WHERE type = ? AND tbl_name = ? AND sql IS NOT NULL',
            ('index', table),
        )]
        temporary = f'{table}_device_upgrade'
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('BEGIN IMMEDIATE')
        connection.execute(re.sub(r'(?i)(CREATE TABLE\s+)(?:IF NOT EXISTS\s+)?["\[]?' + re.escape(table) + r'["\]]?',
                                  lambda match: match[1] + temporary, updated, count=1))
        connection.execute(f'INSERT INTO {temporary} SELECT * FROM {table}')
        connection.execute(f'DROP TABLE {table}')
        connection.execute(f'ALTER TABLE {temporary} RENAME TO {table}')
        for statement in indexes:
            connection.execute(statement)
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('设备范围迁移外键检查失败，已回滚')
