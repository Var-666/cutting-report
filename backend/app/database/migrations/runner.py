"""Transactional schema upgrades with a consistent backup before each pending batch."""
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from app.database.sqlite import SQLiteDatabase
from app.services.process_lock import FileLock


def execute_script(connection: sqlite3.Connection, script: str) -> None:
    # sqlite3.executescript commits an open transaction; execute statements individually.
    statement = ''
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ''
    if statement.strip():
        connection.execute(statement)


def apply_migrations(database: SQLiteDatabase, *, names: Iterable[str] | None = None) -> None:
    from .registry import MIGRATIONS
    selected = set(names) if names is not None else {name for name, _ in MIGRATIONS}
    unknown = selected - {name for name, _ in MIGRATIONS}
    if unknown:
        raise ValueError(f'未知数据库迁移：{sorted(unknown)}')
    run_migrations(database, [(name, upgrade) for name, upgrade in MIGRATIONS if name in selected])


def run_migrations(database: SQLiteDatabase, migrations: list[tuple[str, Callable]]) -> None:
    identifiers = [name for name, _ in migrations]
    if identifiers != sorted(set(identifiers)):
        raise ValueError('数据库迁移编号必须唯一且按顺序排列')
    database.initialize()
    with FileLock(database.path.with_suffix('.migrations.lock')):
        with database.connect() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            applied = {row[0] for row in connection.execute('SELECT name FROM schema_migrations')} if 'schema_migrations' in tables else set()
            pending = [(name, upgrade) for name, upgrade in migrations if name not in applied]
            if not pending:
                return
            if tables - {'schema_migrations'}:
                _backup(database.path, connection)
            # A baseline can rebuild legacy tables. Check all foreign keys before commit.
            connection.execute('PRAGMA foreign_keys=OFF')
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE IF NOT EXISTS schema_migrations(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
            for name, upgrade in pending:
                upgrade(connection)
                connection.execute('INSERT INTO schema_migrations(name, applied_at) VALUES (?, ?)',
                                   (name, datetime.now(timezone.utc).isoformat()))
            if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('迁移后存在无效外键，已回滚数据库升级')


def _backup(path: Path, connection: sqlite3.Connection) -> None:
    folder = path.parent / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{path.stem}-before-schema-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.db'
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with sqlite3.connect(target) as destination:
        connection.backup(destination)
